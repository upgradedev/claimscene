import { beforeEach, describe, expect, it } from "vitest";
import { useCaseStore } from "./useCaseStore";
import { emptyScene } from "@/lib/scene";
import type { ExtractResponse, RenderResponse, Scenario } from "@/lib/api";

const img = (name: string) => new File(["x"], name, { type: "image/png" });

function scenario(id = "s01_rear_end"): Scenario {
  return {
    id,
    title: "Rear End",
    context: "ctx",
    summary: "2 vehicles",
    images: ["view_a.jpg"],
    thumbnail: `/scenarios/${id}/images/view_a.jpg`,
  };
}

describe("useCaseStore", () => {
  beforeEach(() => useCaseStore.getState().reset());

  it("starts at the source step with sane defaults", () => {
    const s = useCaseStore.getState();
    expect(s.step).toBe("source");
    expect(s.caseId).toBe("case");
    expect(s.photos).toEqual([]);
    expect(s.scenario).toBeNull();
    expect(s.result).toBeNull();
  });

  it("goTo / setCaseId / setContextNote are simple setters", () => {
    useCaseStore.getState().goTo("review");
    useCaseStore.getState().setCaseId("crash-7");
    useCaseStore.getState().setContextNote("hit from behind");
    const s = useCaseStore.getState();
    expect(s.step).toBe("review");
    expect(s.caseId).toBe("crash-7");
    expect(s.contextNote).toBe("hit from behind");
  });

  it("addPhotos filters non-images and infers roles from filenames", () => {
    useCaseStore.getState().addPhotos([
      img("front-damage.png"),
      img("the-road.png"),
      img("overview.png"),
      new File(["x"], "notes.txt", { type: "text/plain" }), // dropped
    ]);
    const photos = useCaseStore.getState().photos;
    expect(photos).toHaveLength(3);
    expect(photos.map((p) => p.role)).toEqual([
      "damage_photo",
      "road_photo",
      "scene_photo",
    ]);
    expect(photos[0]!.url).toBe("blob:mock"); // object URL from the setup shim
  });

  it("adding photos clears a previously chosen scenario (mutually exclusive)", () => {
    useCaseStore.getState().chooseScenario(scenario());
    expect(useCaseStore.getState().scenario).not.toBeNull();
    useCaseStore.getState().addPhotos([img("a.png")]);
    expect(useCaseStore.getState().scenario).toBeNull();
  });

  it("removePhoto / setPhotoRole / clearPhotos manage the upload list", () => {
    useCaseStore.getState().addPhotos([img("a.png"), img("b.png")]);
    const [first, second] = useCaseStore.getState().photos;
    useCaseStore.getState().setPhotoRole(first!.id, "damage_photo");
    expect(useCaseStore.getState().photos[0]!.role).toBe("damage_photo");
    useCaseStore.getState().removePhoto(second!.id);
    expect(useCaseStore.getState().photos).toHaveLength(1);
    useCaseStore.getState().clearPhotos();
    expect(useCaseStore.getState().photos).toEqual([]);
  });

  it("chooseScenario adopts the scenario id and clears uploads; null clears it", () => {
    useCaseStore.getState().addPhotos([img("a.png")]);
    useCaseStore.getState().chooseScenario(scenario("s02_left_cross"));
    let s = useCaseStore.getState();
    expect(s.scenario?.id).toBe("s02_left_cross");
    expect(s.caseId).toBe("s02_left_cross");
    expect(s.photos).toEqual([]);
    useCaseStore.getState().chooseScenario(null);
    s = useCaseStore.getState();
    expect(s.scenario).toBeNull();
  });

  it("loadExtraction, setScene and setResult drive the review→result flow", () => {
    const extraction: ExtractResponse = {
      scene: emptyScene(),
      inputs: [{ filename: "p.png", sha256: "a".repeat(64), media_type: "image/png",
                 role: "scene_photo", source: "user_upload" }],
      extraction: { extractor: "fake-vision", source: "fake_extraction", mode: "offline" },
    };
    useCaseStore.getState().loadExtraction(extraction);
    expect(useCaseStore.getState().scene).toEqual(emptyScene());
    expect(useCaseStore.getState().inputs).toHaveLength(1);

    const edited = { ...emptyScene(), sequence: ["impact"] as const };
    useCaseStore.getState().setScene(edited as never);
    expect(useCaseStore.getState().scene?.sequence).toEqual(["impact"]);

    const result = { case_id: "case-1" } as RenderResponse;
    useCaseStore.getState().setResult(result);
    expect(useCaseStore.getState().step).toBe("result");
    expect(useCaseStore.getState().result).toBe(result);
  });

  it("reset returns everything to the initial state", () => {
    useCaseStore.getState().addPhotos([img("a.png")]);
    useCaseStore.getState().goTo("render");
    useCaseStore.getState().reset();
    const s = useCaseStore.getState();
    expect(s.step).toBe("source");
    expect(s.photos).toEqual([]);
    expect(s.scene).toBeNull();
    expect(s.proposedScene).toBeNull();
    expect(s.result).toBeNull();
  });

  it("freezes the AI proposal at extraction, independent of later scene edits", () => {
    const extraction: ExtractResponse = {
      scene: emptyScene(),
      inputs: [],
      extraction: { extractor: "fake-vision", source: "fake_extraction", mode: "offline" },
    };
    useCaseStore.getState().loadExtraction(extraction);
    const afterLoad = useCaseStore.getState();
    // The proposal is a frozen, independent copy of the confirmed scene.
    expect(afterLoad.proposedScene).toEqual(emptyScene());
    expect(afterLoad.proposedScene).not.toBe(afterLoad.scene);

    // Editing the confirmed scene never mutates the sealed baseline.
    const edited = { ...emptyScene(), sequence: ["impact"] as const };
    useCaseStore.getState().setScene(edited as never);
    expect(useCaseStore.getState().scene?.sequence).toEqual(["impact"]);
    expect(useCaseStore.getState().proposedScene?.sequence).toEqual(emptyScene().sequence);
  });

  it("names the sealed case in the URL, and drops it again on reset", () => {
    // The store owns this rather than a component, so a case sealed by the
    // wizard and one reopened from a link both leave a URL worth keeping.
    window.history.replaceState(null, "", "/");
    useCaseStore.getState().setResult({ case_id: "sealed-7" } as never);
    expect(window.location.hash).toBe("#case/sealed-7");
    expect(useCaseStore.getState().step).toBe("result");

    useCaseStore.getState().reset();
    expect(window.location.hash).toBe("#start");
    expect(useCaseStore.getState().result).toBeNull();
  });
});
