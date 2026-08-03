import { create } from "zustand";
import type { ExtractResponse, RenderResponse, Scenario } from "@/lib/api";
import { caseHash, replaceHash, START_HASH } from "@/lib/route";
import { cloneScene, type Scene } from "@/lib/scene";

// The case workflow: pick a source (upload photos OR a sample scenario) →
// review-adjust the proposed scene → render → result. The reviewed `scene` is
// the single source of truth the review panel mutates and the render seals.
export type Step = "source" | "review" | "render" | "result";

export interface LocalPhoto {
  id: string;
  file: File;
  url: string; // object URL for the thumbnail preview
  name: string;
  role: string; // scene_photo | damage_photo | road_photo
}

interface CaseState {
  step: Step;
  caseId: string;

  // source selection
  photos: LocalPhoto[];
  scenario: Scenario | null; // set when a sample scenario is chosen
  contextNote: string;

  // review-adjust
  extraction: ExtractResponse["extraction"] | null;
  inputs: ExtractResponse["inputs"];
  scene: Scene | null;
  // The AI's ORIGINAL proposal, frozen at extraction time — `scene` above is the
  // mutable human-confirmed copy. Both are sent to render so the server can seal
  // the proposed→confirmed approval delta.
  proposedScene: Scene | null;

  // result
  result: RenderResponse | null;

  goTo: (step: Step) => void;
  setCaseId: (id: string) => void;
  addPhotos: (files: File[]) => void;
  removePhoto: (id: string) => void;
  setPhotoRole: (id: string, role: string) => void;
  clearPhotos: () => void;
  chooseScenario: (scenario: Scenario | null) => void;
  setContextNote: (note: string) => void;
  loadExtraction: (res: ExtractResponse) => void;
  setScene: (scene: Scene) => void;
  setResult: (result: RenderResponse) => void;
  reset: () => void;
}

let counter = 0;
const uid = () => `p_${Date.now().toString(36)}_${(counter += 1)}`;
const ACCEPTED = /^image\//;

function inferRole(name: string): string {
  const lower = name.toLowerCase();
  if (lower.includes("damage")) return "damage_photo";
  if (lower.includes("road")) return "road_photo";
  return "scene_photo";
}

export const useCaseStore = create<CaseState>((set, get) => ({
  step: "source",
  caseId: "case",
  photos: [],
  scenario: null,
  contextNote: "",
  extraction: null,
  inputs: [],
  scene: null,
  proposedScene: null,
  result: null,

  goTo: (step) => set({ step }),
  setCaseId: (caseId) => set({ caseId }),

  addPhotos: (files) =>
    set((state) => {
      const next = files
        .filter((f) => ACCEPTED.test(f.type))
        .map<LocalPhoto>((file) => ({
          id: uid(),
          file,
          url: URL.createObjectURL(file),
          name: file.name,
          role: inferRole(file.name),
        }));
      // Choosing an upload clears any previously chosen sample scenario.
      return { photos: [...state.photos, ...next], scenario: next.length ? null : state.scenario };
    }),

  removePhoto: (id) =>
    set((state) => {
      const target = state.photos.find((p) => p.id === id);
      if (target) URL.revokeObjectURL(target.url);
      return { photos: state.photos.filter((p) => p.id !== id) };
    }),

  setPhotoRole: (id, role) =>
    set((state) => ({
      photos: state.photos.map((p) => (p.id === id ? { ...p, role } : p)),
    })),

  clearPhotos: () => {
    get().photos.forEach((p) => URL.revokeObjectURL(p.url));
    set({ photos: [] });
  },

  chooseScenario: (scenario) =>
    set((state) => {
      // Choosing a scenario clears uploaded photos (mutually exclusive source).
      if (scenario) state.photos.forEach((p) => URL.revokeObjectURL(p.url));
      return {
        scenario,
        photos: scenario ? [] : state.photos,
        caseId: scenario ? scenario.id : state.caseId,
      };
    }),

  setContextNote: (contextNote) => set({ contextNote }),

  loadExtraction: (res) =>
    set({
      extraction: res.extraction,
      inputs: res.inputs,
      scene: res.scene,
      // Freeze an independent copy of the AI proposal so later edits to `scene`
      // never mutate the baseline the render seals the delta against.
      proposedScene: cloneScene(res.scene),
    }),

  setScene: (scene) => set({ scene }),

  // The moment a case exists as a durable, fetchable artifact, the URL says
  // its name. Done here rather than in the component that happened to receive
  // it, so the studio's own render and a case reopened from a link both end up
  // with a URL someone can bookmark, refresh or send to an adjuster.
  setResult: (result) => {
    replaceHash(caseHash(result.case_id));
    set({ result, step: "result" });
  },

  reset: () => {
    // Starting over drops the old case from the URL too — otherwise a refresh
    // would reopen the case the visitor just walked away from.
    replaceHash(START_HASH);
    get().photos.forEach((p) => URL.revokeObjectURL(p.url));
    set({
      step: "source", caseId: "case", photos: [], scenario: null, contextNote: "",
      extraction: null, inputs: [], scene: null, proposedScene: null, result: null,
    });
  },
}));
