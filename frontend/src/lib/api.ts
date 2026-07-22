// Typed ClaimScene API client with runtime Zod validation of every response.
//
// Base-URL contract (see vite.config.ts + firebase.json + the Dockerfile):
//   • Production: VITE_API_BASE empty → relative paths → the FastAPI container
//     serves the client + API from one origin. Zero CORS.
//   • Dev: relative paths hit the Vite dev server, which proxies /health,
//     /scenarios and /cases to VITE_API_BASE (a local backend or Cloud Run).
//   • Escape hatch: an absolute VITE_API_BASE is used verbatim.
import { z } from "zod";
import { SceneSchema, type Scene } from "./scene";

export const API_BASE: string = (import.meta.env.VITE_API_BASE ?? "").replace(/\/$/, "");

// ── schemas ──────────────────────────────────────────────────────────────────
export const HealthSchema = z.object({
  status: z.string(),
  service: z.string(),
  mode: z.string(),
  provider: z.string().optional(),
  extractor: z.string().optional(),
  storage: z.string().optional(),
});
export type Health = z.infer<typeof HealthSchema>;

export const ScenarioSchema = z.object({
  id: z.string(),
  title: z.string(),
  context: z.string(),
  summary: z.string(),
  images: z.array(z.string()),
  thumbnail: z.string().nullable(),
});
export type Scenario = z.infer<typeof ScenarioSchema>;
const ScenariosResponseSchema = z.object({ scenarios: z.array(ScenarioSchema) });

export const InputRecordSchema = z.object({
  filename: z.string(),
  sha256: z.string(),
  media_type: z.string(),
  role: z.string(),
  source: z.string(),
  attribution: z.string().nullable().optional(),
  license: z.string().nullable().optional(),
});
export type InputRecord = z.infer<typeof InputRecordSchema>;

export const ExtractResponseSchema = z.object({
  scene: SceneSchema,
  inputs: z.array(InputRecordSchema),
  extraction: z.object({
    extractor: z.string(),
    source: z.string(),
    mode: z.string(),
  }),
});
export type ExtractResponse = z.infer<typeof ExtractResponseSchema>;

export const PreviewResponseSchema = z.object({
  svg: z.string(),
  warnings: z.array(z.string()),
  contact_point: z.array(z.number()).nullable(),
  vehicle_count: z.number(),
  road: z.object({
    layout: z.string(),
    lanes_per_direction: z.number(),
    signal: z.string(),
  }),
});
export type PreviewResponse = z.infer<typeof PreviewResponseSchema>;

const ArtifactRefSchema = z.object({
  sha256: z.string(),
  size_bytes: z.number(),
  content_type: z.string(),
  url: z.string().nullable(),
});

export const RenderResponseSchema = z.object({
  case_id: z.string(),
  manifest_hash: z.string(),
  manifest_url: z.string(),
  provider: z.string(),
  degraded: z.boolean(),
  provider_degraded: z.boolean(),
  has_schematic_animation: z.boolean(),
  schematic_kind: z.enum(["animation", "static"]),
  schematic_url: z.string(),
  illustration_url: z.string(),
  report_markdown: z.string(),
  scene: SceneSchema,
  warnings: z.array(z.string()),
  artifacts: z.record(ArtifactRefSchema),
  degrade_reason: z.string().optional(),
});
export type RenderResponse = z.infer<typeof RenderResponseSchema>;

// The sealed manifest (GET /cases/{id}) — parsed for the provenance panel.
export const ManifestSchema = z.object({
  schema: z.string(),
  case_id: z.string(),
  created_at: z.string(),
  disclosure: z.string(),
  inputs: z.array(InputRecordSchema),
  scene_graph: z.object({ schema: z.string(), sha256: z.string() }),
  timeline: z.object({ schema: z.string(), sha256: z.string() }),
  schematic: z.record(z.unknown()),
  illustration: z.object({
    provider: z.string(),
    model: z.string(),
    prompt: z.string(),
    sha256: z.string(),
    still_model: z.string().optional(),
    still_prompt: z.string().optional(),
    still_sha256: z.string().optional(),
    degraded: z.boolean(),
  }),
  report: z.object({ media_type: z.string(), sha256: z.string() }),
  manifest_hash: z.string(),
});
export type Manifest = z.infer<typeof ManifestSchema>;

// ── errors ───────────────────────────────────────────────────────────────────
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status?: number,
    readonly cause?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

// ── request helpers ──────────────────────────────────────────────────────────
async function rawFetch(path: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(`${API_BASE}${path}`, {
      headers: { Accept: "application/json", ...(init?.headers ?? {}) },
      ...init,
    });
  } catch (err) {
    throw new ApiError("Network unreachable — check your connection.", undefined, err);
  }
}

async function request<S extends z.ZodTypeAny>(
  path: string,
  schema: S,
  init?: RequestInit,
): Promise<z.infer<S>> {
  const res = await rawFetch(path, init);
  if (!res.ok) {
    throw new ApiError(`Request to ${path} failed (${res.status}).`, res.status);
  }
  let json: unknown;
  try {
    json = await res.json();
  } catch (err) {
    throw new ApiError(`Malformed response from ${path}.`, res.status, err);
  }
  const parsed = schema.safeParse(json);
  if (!parsed.success) {
    throw new ApiError(`Unexpected response shape from ${path}.`, res.status, parsed.error);
  }
  return parsed.data;
}

export interface ExtractRequest {
  scenarioId?: string;
  files?: File[];
  roles?: string[];
  context?: string;
}

export interface RenderRequest {
  scene: Scene;
  caseId?: string;
  scenarioId?: string;
  files?: File[];
  roles?: string[];
}

export const claimsceneApi = {
  health(): Promise<Health> {
    return request("/health", HealthSchema);
  },

  async scenarios(): Promise<Scenario[]> {
    const { scenarios } = await request("/scenarios", ScenariosResponseSchema);
    return scenarios;
  },

  /** Propose a constrained SceneGraph from uploaded photos OR a scenario id.
   *  We never set Content-Type — the browser adds the multipart boundary. */
  extract({ scenarioId, files = [], roles, context }: ExtractRequest): Promise<ExtractResponse> {
    const form = new FormData();
    if (scenarioId) form.append("scenario_id", scenarioId);
    if (context) form.append("context", context);
    if (roles?.length) form.append("roles", roles.join(","));
    for (const file of files) form.append("files", file, file.name);
    return request("/cases/extract", ExtractResponseSchema, { method: "POST", body: form });
  },

  /** Reviewed scene → fast static schematic (the live review-adjust loop). */
  previewSchematic(scene: Scene): Promise<PreviewResponse> {
    return request("/cases/preview-schematic", PreviewResponseSchema, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(scene),
    });
  },

  /** Seal the reviewed scene into a full, verifiable case. */
  render({ scene, caseId = "case", scenarioId, files = [], roles }: RenderRequest): Promise<RenderResponse> {
    const form = new FormData();
    form.append("scene", JSON.stringify(scene));
    form.append("case_id", caseId);
    if (scenarioId) form.append("scenario_id", scenarioId);
    if (roles?.length) form.append("roles", roles.join(","));
    for (const file of files) form.append("files", file, file.name);
    return request("/cases/render", RenderResponseSchema, { method: "POST", body: form });
  },

  /** The parsed sealed manifest (provenance display). */
  manifest(caseId: string): Promise<Manifest> {
    return request(`/cases/${encodeURIComponent(caseId)}`, ManifestSchema);
  },
};
