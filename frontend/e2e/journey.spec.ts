import { test, expect, type Page } from "@playwright/test";
import { installApiMocks } from "./_mocks";

/**
 * The full reviewer journey: landing → source → review-adjust → render →
 * sealed case, asserting that EVERY step both mounts AND actually becomes
 * visible (opacity:1).
 *
 * The `opacity:1` assertion is the load-bearing one. The shipped bug wrapped
 * the steps in `<AnimatePresence mode="wait">`: the incoming step mounts but
 * stalls at its `initial` opacity:0 when the outgoing exit never completes, so
 * the wizard dead-ends at a blank step 2. Playwright's `toBeVisible()` does NOT
 * treat opacity:0 as hidden, so only `toHaveCSS('opacity','1')` catches it —
 * on the buggy build this specific assertion times out (red); on the fix it
 * resolves (green).
 */

/** The animated wrapper for a wizard step, addressed by its aria-label. */
const stepRegion = (page: Page, n: 1 | 2 | 3 | 4) =>
  page.getByRole("group", { name: new RegExp(`Step ${n} of 4`) });

test.beforeEach(async ({ page }) => {
  await installApiMocks(page);
});

test("reviewer completes the whole journey and every step becomes visible", async ({ page }) => {
  await page.goto("/");

  // ── Landing ────────────────────────────────────────────────────────────────
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Two layers");
  await expect(page.getByText("NOT EVIDENCE").first()).toBeVisible();
  await page.getByRole("button", { name: /Try a sample scenario/i }).click();

  // ── Step 1: source ───────────────────────────────────────────────────────────
  await expect(stepRegion(page, 1)).toHaveCSS("opacity", "1");
  await expect(page.getByRole("heading", { name: /Start a case/i })).toBeVisible();
  const scenarioCard = page.getByRole("button", { name: /Rear-end at a red light/i });
  await expect(scenarioCard).toBeVisible();
  await scenarioCard.click();
  await page.getByRole("button", { name: /Extract scene/i }).click();

  // ── Step 2: review-adjust — THE regression discriminator ──────────────────────
  // On the buggy build this line times out: the review step is mounted but
  // frozen at opacity:0 behind an exit that never finishes.
  await expect(stepRegion(page, 2)).toHaveCSS("opacity", "1");
  await expect(page.getByRole("heading", { name: /Review .* adjust the scene/i })).toBeVisible();
  await expect(page.getByText(/AI proposes, you confirm/i)).toBeVisible();

  // The constrained-vocab controls: closed-set dropdowns …
  const selects = page.getByRole("combobox");
  expect(await selects.count()).toBeGreaterThan(2);
  await expect(selects.first()).toBeVisible();
  // … and the 12-position impact clock (never free-text coordinates).
  const clock = page.getByRole("radiogroup").first();
  await expect(clock).toBeVisible();
  expect(await clock.getByRole("radio").count()).toBe(12);
  // The live factual-layer schematic redraws in the review panel.
  await expect(page.getByAltText(/top-down accident schematic/i)).toBeVisible();

  // Prove the vocabulary control is genuinely interactive (correct the AI).
  await selects.first().selectOption({ index: 1 });

  await page.getByRole("button", { name: /Confirm .* render this case/i }).click();

  // ── Step 3: render (sealing) ──────────────────────────────────────────────────
  await expect(stepRegion(page, 3)).toHaveCSS("opacity", "1");
  await expect(page.getByRole("heading", { name: /Sealing your case/i })).toBeVisible();

  // ── Step 4: sealed case ───────────────────────────────────────────────────────
  await expect(stepRegion(page, 4)).toHaveCSS("opacity", "1");
  await expect(page.getByRole("heading", { name: /Case sealed/i })).toBeVisible();
  // Both layers are on screen: the factual schematic + the disclosed illustration.
  await expect(page.getByText(/Factual layer . schematic/i)).toBeVisible();
  await expect(page.getByText("AI ILLUSTRATION — NOT EVIDENCE")).toBeVisible();
  await expect(page.getByRole("heading", { level: 3, name: /Incident report/i })).toBeVisible();

  // ── ProvenancePanel: the review-ledger (sealed AI→human approval receipt) ─────
  await expect(page.getByText(/review ledger . AI proposed, you confirmed/i)).toBeVisible();
  await expect(page.getByText(/1\s+human-corrected/i)).toBeVisible();
  await page.getByText(/corrected field.*before/i).click(); // expand the diff
  await expect(page.getByText("vehicles.veh_b.color")).toBeVisible();

  // ── Verify in browser: recompute the seal + fetch the named-check receipt ─────
  await page.getByRole("button", { name: /Verify in browser/i }).click();
  await expect(page.getByText("Verified ✓")).toBeVisible();
  await expect(page.getByText(/server-side checks/i)).toBeVisible();
  await expect(page.getByText(/all passed/i)).toBeVisible();
  const checkRows = page.getByRole("listitem").filter({ hasText: /match the sealed hash|recomputes|carries|consistent|source/i });
  expect(await checkRows.count()).toBeGreaterThanOrEqual(14);
});

test("extract shows a live progress affordance (elapsed timer + indeterminate bar)", async ({ page }) => {
  // Hold the extract in flight so the progress panel is observable (the shared
  // mock otherwise resolves instantly).
  await page.route("**/cases/extract", async (route) => {
    await new Promise((r) => setTimeout(r, 2500));
    await route.fallback();
  });

  await page.goto("/");
  await page.getByRole("button", { name: /Try a sample scenario/i }).click();
  await page.getByRole("button", { name: /Rear-end at a red light/i }).click();
  await page.getByRole("button", { name: /Extract scene/i }).click();

  // The affordance: a polite status region, expected-duration copy, an
  // indeterminate progressbar, and an elapsed timer that ticks up.
  const status = page.getByRole("status").filter({ hasText: /ground-truth scene|Extracting the scene/i });
  await expect(status).toBeVisible();
  await expect(page.getByRole("progressbar", { name: /Extraction in progress/i })).toBeVisible();
  await expect(status.getByText(/^\d+s$/)).toBeVisible();

  // It resolves into the review step.
  await expect(page.getByRole("group", { name: /Step 2 of 4/ })).toHaveCSS("opacity", "1");
});
