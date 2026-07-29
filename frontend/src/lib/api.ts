// Typed ClaimScene API client with runtime Zod validation of every response.
//
// Base-URL contract (see vite.config.ts + firebase.json + the Dockerfile):
//   • Production: VITE_API_BASE empty → relative paths → the FastAPI container
//     serves the client + API from one origin. Zero CORS.
//   • Dev: relative paths hit the Vite dev server, which proxies /health,
//     /scenarios and /cases to VITE_API_BASE (a local backend or Cloud Run).
//   • Escape hatch: an absolute VITE_API_BASE is used verbatim.
import { z } from "zod";
import { getIdToken } from "./auth";
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

// Async submit + poll (POST /cases/render/jobs / GET /cases/render/jobs/{job_id})
// — the non-blocking counterpart to POST /cases/render for a caller that
// cannot hold one request open for a multi-minute live render (see
// claimscene.jobs on the backend). A job's terminal `result` is the IDENTICAL
// shape as RenderResponse above, so the success/render path is shared with
// the synchronous flow unchanged.
export const RenderJobSubmitResponseSchema = z.object({
  job_id: z.string(),
  status: z.string(),
});
export type RenderJobSubmitResponse = z.infer<typeof RenderJobSubmitResponseSchema>;

export const RenderJobStatusSchema = z.object({
  job_id: z.string(),
  status: z.enum(["queued", "running", "done", "failed"]),
  created_at: z.string().optional(),
  updated_at: z.string().optional(),
  result: RenderResponseSchema.optional(),
  // Exception CLASS NAME only when status is "failed" (see claimscene.jobs) —
  // never prose, never a stack trace. The UI keeps this out of the headline.
  error: z.string().optional(),
});
export type RenderJobStatus = z.infer<typeof RenderJobStatusSchema>;

// The sealed AI→human approval receipt (review block) inside the manifest.
export const ReviewDiffRowSchema = z.object({
  path: z.string(),
  proposed: z.unknown(),
  confirmed: z.unknown(),
  changed: z.boolean(),
  changed_by: z.string(),
});
export type ReviewDiffRow = z.infer<typeof ReviewDiffRowSchema>;

export const ReviewSchema = z.object({
  schema: z.string(),
  classification: z.string(),
  reviewer_id: z.string().nullable(),
  reviewed_at: z.string(),
  scene_proposed_sha256: z.string().nullable(),
  scene_confirmed_sha256: z.string().nullable(),
  diff: z.array(ReviewDiffRowSchema),
  counts: z.object({
    proposed_fields: z.number(),
    human_changed: z.number(),
    unchanged: z.number(),
  }),
  prior_confidence_notes: z.array(z.string()),
  decision_digest: z.string(),
});
export type Review = z.infer<typeof ReviewSchema>;

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
  // The review receipt is optional so back-compat manifests still parse.
  review: ReviewSchema.optional(),
  manifest_hash: z.string(),
});
export type Manifest = z.infer<typeof ManifestSchema>;

// Optional per-user multitenancy (GET /me/library, DELETE /me/data — see
// claimscene.api and lib/auth.ts). Both routes 401 for a guest caller; the UI
// only ever calls them when signed in (see MyCases.tsx).
export const LibraryCaseSchema = z.object({
  case_id: z.string(),
  manifest_hash: z.string().nullable(),
  created_at: z.string().nullable(),
});
export type LibraryCase = z.infer<typeof LibraryCaseSchema>;

const MyLibraryResponseSchema = z.object({ cases: z.array(LibraryCaseSchema) });

const DeleteMyDataResponseSchema = z.object({ deleted: z.number().int() });

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

/** Merges a fresh `Authorization: Bearer <id token>` into `headers` when a
 *  user is signed in (see lib/auth.ts::getIdToken). Signed out, or auth
 *  disabled on this build — the overwhelmingly common case, and the ONLY
 *  case any test before this feature existed ever exercised — returns
 *  `headers` completely UNCHANGED (same reference when a base was given, or
 *  `undefined` when it wasn't), so every call below is byte-identical to its
 *  pre-multitenancy fetch for a guest. Never caches the token: a fresh one is
 *  requested on every single call.
 *
 *  The `| undefined` overload matters, not just the empty-object case: the
 *  shared `request()` helper spreads `...init` AFTER `rawFetch`'s own default
 *  headers, so an explicit-but-empty `init.headers` key would still clobber
 *  the default `Accept` header a guest call relies on. Callers with no base
 *  headers (submitRenderJob/getRenderJob/manifest/myLibrary/deleteMyData)
 *  therefore omit the `headers` key entirely when there's no token — see
 *  their `auth ? {...} : undefined` pattern below — not pass an empty object.
 *
 *  Only wired into the routes whose backend dependency (`get_tenant` in
 *  claimscene.api) actually reads the tenant: render, submitRenderJob,
 *  getRenderJob, manifest, myLibrary, deleteMyData. `extract` and
 *  `previewSchematic` take no `tenant` parameter on the backend — they never
 *  touch storage — so attaching a token there would cost a token fetch on
 *  every review-loop edit for a header the server would just discard. */
async function withAuth(
  headers?: Record<string, string>,
): Promise<Record<string, string> | undefined> {
  const token = await getIdToken();
  if (!token) return headers;
  return { ...(headers ?? {}), Authorization: `Bearer ${token}` };
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
  /** The AI-proposed scene the human reviewed against — sealed as an approval
   *  receipt (server-computed proposed→confirmed diff). */
  proposedScene?: Scene;
  /** Honesty taxonomy for the approval; the server downgrades an unauthenticated
   *  `authenticated_human` to `interactive_demo`. */
  reviewClassification?: string;
  reviewerId?: string;
}

/** Build the multipart body shared by the sync (`POST /cases/render`) and
 *  async (`POST /cases/render/jobs`) submit paths — both accept the IDENTICAL
 *  form shape (see claimscene.api._prepare_render), so this is the one place
 *  that shape is assembled. */
function renderForm({ scene, caseId = "case", scenarioId, files = [], roles,
                      proposedScene, reviewClassification, reviewerId }: RenderRequest): FormData {
  const form = new FormData();
  form.append("scene", JSON.stringify(scene));
  form.append("case_id", caseId);
  if (scenarioId) form.append("scenario_id", scenarioId);
  if (roles?.length) form.append("roles", roles.join(","));
  if (proposedScene) form.append("proposed_scene", JSON.stringify(proposedScene));
  if (reviewClassification) form.append("review_classification", reviewClassification);
  if (reviewerId) form.append("reviewer_id", reviewerId);
  for (const file of files) form.append("files", file, file.name);
  return form;
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

  /** Seal the reviewed scene into a full, verifiable case. When a proposed
   *  scene is supplied, the server seals an AI→human approval receipt. */
  async render(req: RenderRequest): Promise<RenderResponse> {
    const auth = await withAuth();
    return request("/cases/render", RenderResponseSchema, {
      method: "POST",
      body: renderForm(req),
      ...(auth ? { headers: auth } : {}),
    });
  },

  /** The parsed sealed manifest (provenance display). */
  async manifest(caseId: string): Promise<Manifest> {
    const auth = await withAuth();
    return request(
      `/cases/${encodeURIComponent(caseId)}`,
      ManifestSchema,
      auth ? { headers: auth } : undefined,
    );
  },

  /** Submit a case render as a background job (202 + job_id) instead of
   *  blocking for the full two-step generation — the async counterpart to
   *  `render`, for a caller that cannot hold one request open that long (see
   *  claimscene.jobs on the backend). Same body shape as `render`. Poll the
   *  result with `getRenderJob`. */
  async submitRenderJob(req: RenderRequest): Promise<RenderJobSubmitResponse> {
    const auth = await withAuth();
    return request("/cases/render/jobs", RenderJobSubmitResponseSchema, {
      method: "POST",
      body: renderForm(req),
      ...(auth ? { headers: auth } : {}),
    });
  },

  /** Poll a submitted render job's status (queued/running/done/failed). A 404
   *  (unknown job id) surfaces as an ApiError like any other failed request —
   *  the caller's poll loop tolerates that the same as a transient failure. */
  async getRenderJob(jobId: string): Promise<RenderJobStatus> {
    const auth = await withAuth();
    return request(
      `/cases/render/jobs/${encodeURIComponent(jobId)}`,
      RenderJobStatusSchema,
      auth ? { headers: auth } : undefined,
    );
  },

  /** The signed-in tenant's own cases (GET /me/library). Always attaches the
   *  Authorization header — this route only makes sense for a signed-in
   *  caller (401 for guest) and MyCases never calls it otherwise, but a
   *  missing/expired token still surfaces as an honest ApiError(401) rather
   *  than silently resolving as guest. */
  async myLibrary(): Promise<LibraryCase[]> {
    const auth = await withAuth();
    const { cases } = await request(
      "/me/library",
      MyLibraryResponseSchema,
      auth ? { headers: auth } : undefined,
    );
    return cases;
  },

  /** Erase every object under the signed-in tenant's own storage prefix
   *  (DELETE /me/data). Returns the number of objects deleted. */
  async deleteMyData(): Promise<{ deleted: number }> {
    const auth = await withAuth();
    return request("/me/data", DeleteMyDataResponseSchema, {
      method: "DELETE",
      ...(auth ? { headers: auth } : {}),
    });
  },
};
