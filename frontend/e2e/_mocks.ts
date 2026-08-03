/**
 * Deterministic API mocks for the end-to-end journey / a11y / responsive specs.
 *
 * Every ClaimScene network call is intercepted with `page.route`, so the built
 * client runs the full source → review → render → sealed-case flow with no
 * backend, no credentials and no network — fully reproducible in CI.
 *
 * The sealed-case leg reuses the repo's real byte-exact golden manifest
 * (src/test/fixtures/golden-manifest-reviewed.json). The manifest is served
 * VERBATIM (raw bytes, never re-stringified) so the in-browser provenance
 * verifier recomputes its canonical SHA-256 and the "Verify in browser" panel
 * reports a genuine match. The render response advertises the same
 * `manifest_hash`/`case_id`, so the on-screen hash and the re-fetched manifest
 * agree — exactly as a live seal would.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import type { Page } from "@playwright/test";

// The golden reviewed manifest — served raw so its seal verifies unchanged.
export const GOLDEN_REVIEWED_RAW = readFileSync(
  fileURLToPath(new URL("../src/test/fixtures/golden-manifest-reviewed.json", import.meta.url)),
  "utf8",
);
const GOLDEN = JSON.parse(GOLDEN_REVIEWED_RAW) as {
  case_id: string;
  manifest_hash: string;
};
export const GOLDEN_CASE_ID = GOLDEN.case_id; // "golden-reviewed"
export const GOLDEN_MANIFEST_HASH = GOLDEN.manifest_hash;

const JSON_CT = "application/json";

// A minimal but valid 2-vehicle rear-end scene (mirrors the golden scenario):
// a blue car struck at 6 o'clock (rear) and a red car damaged at 12 o'clock
// (front). The AI "proposes" the second car as silver; the reviewer can correct
// it to red — the very edit the sealed review-ledger records.
const PROPOSED_SCENE = {
  schema: "claimscene/scene/v1",
  road: { layout: "straight", lanes_per_direction: 1, signal: "traffic_light" },
  vehicles: [
    { id: "veh_a", kind: "car", color: "blue", damage: [{ clock_position: 6, severity: "crush", note: null }] },
    { id: "veh_b", kind: "car", color: "silver", damage: [{ clock_position: 12, severity: "dent", note: null }] },
  ],
  movements: [
    { vehicle_id: "veh_a", approach: "N", maneuver: "straight", speed_band: "stopped" },
    { vehicle_id: "veh_b", approach: "N", maneuver: "straight", speed_band: "moderate" },
  ],
  impacts: [
    { vehicle_id: "veh_a", clock_position: 6 },
    { vehicle_id: "veh_b", clock_position: 12 },
  ],
  sequence: ["vehicles_approach", "braking", "impact", "vehicles_come_to_rest"],
  confidence_notes: ["vlm: unsure whether the second car is silver or red — flagged for review"],
};

const CONFIRMED_SCENE = {
  ...PROPOSED_SCENE,
  vehicles: [
    PROPOSED_SCENE.vehicles[0],
    { ...PROPOSED_SCENE.vehicles[1], color: "red" },
  ],
  confidence_notes: [],
};

const HEALTH = {
  status: "ok",
  service: "claimscene",
  mode: "offline",
  provider: "fake-media",
  extractor: "fake-vlm",
  storage: "memory",
};

const SCENARIOS = {
  scenarios: [
    {
      id: "s01_rear_end",
      title: "Rear-end at a red light",
      context: "Two cars, one lane each way, traffic light.",
      summary: "A stopped blue car is struck from behind by a red car that failed to stop.",
      images: ["view_a.jpg", "view_b.jpg", "view_c.jpg"],
      thumbnail: "/scenarios/s01_rear_end/images/view_a.jpg",
    },
  ],
};

const EXTRACT_RESPONSE = {
  scene: PROPOSED_SCENE,
  inputs: [
    {
      filename: "s01_rear_end_view_a.jpg",
      sha256: "2c9660ec2c8b42defcdf16b5a1956f96e0bab00b79d12c2cf71177feb3ef281a",
      media_type: "image/jpeg",
      role: "scene_photo",
      source: "synthetic_generated",
      attribution: "seedream toy-diorama render via the GenblazeMediaProvider adapter",
      license: "CC0-1.0 (synthetic, no real scenes/people/plates)",
    },
  ],
  extraction: { extractor: "committed-ground-truth", source: "committed_ground_truth", mode: "offline" },
};

const PREVIEW_RESPONSE = {
  svg:
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 200 120'>" +
    "<rect width='200' height='120' fill='#0e1a22'/>" +
    "<text x='100' y='112' fill='#5f7d8f' font-size='7' text-anchor='middle'>ILLUSTRATION — NOT EVIDENCE</text>" +
    "</svg>",
  warnings: [],
  contact_point: null,
  vehicle_count: 2,
  road: { layout: "straight", lanes_per_direction: 1, signal: "traffic_light" },
};

const RENDER_RESPONSE = {
  case_id: GOLDEN_CASE_ID,
  manifest_hash: GOLDEN_MANIFEST_HASH,
  manifest_url: `/cases/${GOLDEN_CASE_ID}`,
  provider: "fake-media",
  degraded: true,
  provider_degraded: false,
  has_schematic_animation: false,
  schematic_kind: "static",
  schematic_url: `/cases/${GOLDEN_CASE_ID}/schematic`,
  illustration_url: `/cases/${GOLDEN_CASE_ID}/illustration`,
  report_markdown:
    "# Incident report — factual layer\n\n" +
    "**Road:** straight, 1 lane each way, traffic light.\n\n" +
    "- **veh_a** (blue car): arrived from the north, stopped; crush at 6 o'clock (rear).\n" +
    "- **veh_b** (red car): arrived from the north at moderate speed; dent at 12 o'clock (front).\n\n" +
    "The blue car was struck from behind while stopped at the light.\n",
  scene: CONFIRMED_SCENE,
  warnings: [],
  artifacts: {},
};

// Measured render durations (GET /cases/render/estimate) — how long a render
// takes on this deployment, from the last completed ones. Deterministic here
// so the "about N minutes" copy is stable across runs.
const RENDER_ESTIMATE = {
  samples: 6,
  typical_seconds: 240,
  slow_seconds: 400,
  mode: "offline",
};

// The server-side named-check receipt (GET /cases/{id}/verify): a full 14-check
// live seal, every check re-run from stored bytes and passing.
const VERIFY_RECEIPT = {
  success: true,
  digest: "0f".repeat(32),
  checks: [
    { id: "seal.manifest_hash", label: "Manifest seal recomputes (SHA-256)", passed: true, evidence: "canonical SHA-256 over the sealed body matches manifest_hash" },
    { id: "artifact.scene_graph", label: "Scene-graph bytes match the sealed hash", passed: true, evidence: "re-hashed stored bytes ea9d894576c5… matches the sealed hash" },
    { id: "artifact.timeline", label: "Timeline bytes match the sealed hash", passed: true, evidence: "re-hashed stored bytes 2284a965c2ad… matches the sealed hash" },
    { id: "artifact.schematic_svg", label: "Schematic SVG bytes match the sealed hash", passed: true, evidence: "re-hashed stored bytes 7f4e9306fc92… matches the sealed hash" },
    { id: "artifact.schematic_hero", label: "Schematic hero-frame bytes match the sealed hash", passed: true, evidence: "re-hashed stored bytes ede5231e3a6d… matches the sealed hash" },
    { id: "artifact.schematic_animation", label: "Schematic animation bytes match the sealed hash", passed: true, evidence: "re-hashed stored bytes 9b1c0a77e2f4… matches the sealed hash" },
    { id: "artifact.illustration_still", label: "Illustration still bytes match the sealed hash", passed: true, evidence: "re-hashed stored bytes bb82596de9ad… matches the sealed hash" },
    { id: "artifact.illustration", label: "Illustration clip bytes match the sealed hash", passed: true, evidence: "re-hashed stored bytes be022ec88c54… matches the sealed hash" },
    { id: "artifact.report", label: "Report bytes match the sealed hash", passed: true, evidence: "re-hashed stored bytes 8fe7de793797… matches the sealed hash" },
    { id: "structural.disclosure", label: "Disclosure statement present + exact", passed: true, evidence: "disclosure == 'AI-generated illustration — not evidence'" },
    { id: "structural.input_attribution", label: "Every input photo carries a source", passed: true, evidence: "every input row carries a non-empty source" },
    { id: "structural.schematic_watermark", label: "Schematic carries the NOT-EVIDENCE watermark", passed: true, evidence: "schematic SVG carries 'ILLUSTRATION — NOT EVIDENCE' ×1" },
    { id: "structural.illustration_degraded", label: "Illustration degraded flag is internally consistent", passed: true, evidence: "degraded=true is consistent with provider 'fake-media'" },
    { id: "review.decision_digest", label: "Approval receipt recomputes from the sealed hashes", passed: true, evidence: "decision_digest recomputes; approval stays bound to the sealed scene" },
  ],
};

// A 1x1 transparent PNG (schematic/illustration <img>/<video> stubs).
const PNG_1x1 = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
  "base64",
);

function json(body: unknown): { contentType: string; body: string } {
  return { contentType: JSON_CT, body: JSON.stringify(body) };
}

/** Install every ClaimScene API mock on a page. Call before `page.goto`. */
export async function installApiMocks(page: Page): Promise<void> {
  await page.route("**/health", (route) => route.fulfill(json(HEALTH)));

  await page.route("**/scenarios", (route) => route.fulfill(json(SCENARIOS)));

  await page.route("**/scenarios/**", (route) =>
    route.fulfill({ contentType: "image/png", body: PNG_1x1 }),
  );

  await page.route("**/cases/**", (route) => {
    const method = route.request().method();
    const { pathname } = new URL(route.request().url());

    if (pathname.endsWith("/cases/extract")) return route.fulfill(json(EXTRACT_RESPONSE));
    if (pathname.endsWith("/cases/preview-schematic")) return route.fulfill(json(PREVIEW_RESPONSE));

    // Async submit + poll (POST /cases/render/jobs, GET /cases/render/jobs/{id})
    // — the studio wizard's RenderStep uses this pair, never the synchronous
    // POST /cases/render below. Both checks are matched by SUBSTRING (not
    // endsWith) and placed ABOVE the generic `method === "GET"` fallback
    // further down: a poll's URL ends in a job id, e.g.
    // ".../cases/render/jobs/job-1", which does NOT satisfy
    // `pathname.endsWith("/cases/render/jobs")` — falling through to that
    // fallback would return the golden MANIFEST body instead of a job-status
    // body, fail RenderJobStatusSchema parsing, and burn the poll loop's
    // consecutive-error budget (a real bug caught before it shipped).
    if (method === "POST" && pathname.endsWith("/cases/render/jobs")) {
      // A brief, deterministic delay so the "Sealing your case" step is
      // observable before it auto-advances to the sealed result — mirrors
      // the synchronous /cases/render delay below.
      return new Promise<void>((resolve) => {
        setTimeout(() => resolve(route.fulfill({ status: 202, ...json({ job_id: "job-1", status: "queued" }) })), 700);
      });
    }
    if (method === "GET" && pathname.includes("/cases/render/jobs/")) {
      // Already "done" on the very first poll — offline/demo generation can
      // finish near-instantly (see usePollRenderJob's first-tick-immediate
      // behaviour), so the journey/a11y/responsive specs never need to wait
      // out multiple poll intervals for the default path.
      return route.fulfill(json({
        job_id: "job-1", status: "done",
        created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:05Z",
        result: RENDER_RESPONSE,
      }));
    }

    // GET /cases/render/estimate — the measured "how long will this take"
    // answer, shown before the visitor commits and again while they wait.
    if (method === "GET" && pathname.endsWith("/cases/render/estimate")) {
      return route.fulfill(json(RENDER_ESTIMATE));
    }

    // GET /cases/{id}/result — a sealed case rebuilt into the render body, the
    // route that makes a case survive a lost tab. Only the golden case exists;
    // every other id 404s exactly as the real server does for an unknown id,
    // an expired one, or one belonging to another account.
    if (method === "GET" && pathname.endsWith("/result")) {
      const caseId = pathname.split("/").slice(-2)[0];
      if (caseId === GOLDEN_CASE_ID) return route.fulfill(json(RENDER_RESPONSE));
      return route.fulfill({
        status: 404, contentType: JSON_CT,
        body: JSON.stringify({ detail: `no case named '${caseId}'` }),
      });
    }

    if (pathname.endsWith("/cases/render")) {
      // A brief, deterministic delay so the "Sealing your case" step is
      // observable before it auto-advances to the sealed result.
      return new Promise<void>((resolve) => {
        setTimeout(() => resolve(route.fulfill(json(RENDER_RESPONSE))), 700);
      });
    }
    if (pathname.endsWith("/verify")) return route.fulfill(json(VERIFY_RECEIPT));
    if (pathname.endsWith("/schematic") || pathname.endsWith("/illustration")) {
      return route.fulfill({ contentType: "image/png", body: PNG_1x1 });
    }
    // GET /cases/{id} — the sealed manifest, served byte-exact so it verifies.
    if (method === "GET") {
      return route.fulfill({ contentType: JSON_CT, body: GOLDEN_REVIEWED_RAW });
    }
    return route.fulfill({ status: 404, contentType: JSON_CT, body: "{}" });
  });

  // Optional per-user multitenancy (GET /me/library, DELETE /me/data). No
  // spec signs in today (isAuthEnabled() is false with no VITE_FIREBASE_*
  // build vars — see lib/auth.ts), so these are never reached by the current
  // suite; registered for completeness/forward-compat should a future spec
  // mock lib/auth as enabled.
  await page.route("**/me/library", (route) => route.fulfill(json({ cases: [] })));
  await page.route("**/me/data", (route) => route.fulfill(json({ deleted: 0 })));
}
