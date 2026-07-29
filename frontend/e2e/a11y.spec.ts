import { test, expect, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { installApiMocks } from "./_mocks";

/**
 * Accessibility gate: zero serious/critical axe violations on the landing page
 * and on every wizard step. WCAG 2.1 A + AA rule sets. The disclosure banner's
 * "NOT EVIDENCE" line and the muted body-text token are the load-bearing
 * contrast fixes this locks in.
 */

async function auditSeriousOrCritical(page: Page, label: string) {
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  const bad = results.violations.filter(
    (v) => v.impact === "serious" || v.impact === "critical",
  );
  if (bad.length) {
    console.log(
      `AXE[${label}] serious/critical:`,
      JSON.stringify(
        bad.map((v) => ({ id: v.id, impact: v.impact, help: v.help, nodes: v.nodes.map((n) => n.target) })),
        null,
        2,
      ),
    );
  }
  return bad;
}

test.beforeEach(async ({ page }) => {
  await installApiMocks(page);
});

test("landing page has no serious/critical a11y violations", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  expect(await auditSeriousOrCritical(page, "landing")).toEqual([]);
});

test("source step has no serious/critical a11y violations", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /Try a sample scenario/i }).click();
  await expect(page.getByRole("heading", { name: /Start a case/i })).toBeVisible();
  expect(await auditSeriousOrCritical(page, "source")).toEqual([]);
});

test("review step has no serious/critical a11y violations", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /Try a sample scenario/i }).click();
  await page.getByRole("button", { name: /Rear-end at a red light/i }).click();
  await page.getByRole("button", { name: /Extract scene/i }).click();
  await expect(page.getByRole("heading", { name: /Review .* adjust the scene/i })).toBeVisible();
  expect(await auditSeriousOrCritical(page, "review")).toEqual([]);
});

test("review step with a vehicle selected (rotate handle visible) has no serious/critical a11y violations", async ({
  page,
}) => {
  // The default "review step" audit above never selects a vehicle, so the
  // placement panel's rotate handle (and its dashed AI-proposed ghosts) are
  // never in the tree it audits. Select one so those surfaces are covered too.
  await page.goto("/");
  await page.getByRole("button", { name: /Try a sample scenario/i }).click();
  await page.getByRole("button", { name: /Rear-end at a red light/i }).click();
  await page.getByRole("button", { name: /Extract scene/i }).click();
  await expect(page.getByRole("heading", { name: /Review .* adjust the scene/i })).toBeVisible();

  const panel = page.getByRole("group", { name: /vehicle placement canvas/i });
  const marker = panel.getByRole("button", { name: /^veh_a:/i });
  await marker.click();
  await expect(page.getByRole("button", { name: /^Rotate veh_a:/i })).toBeVisible();

  expect(await auditSeriousOrCritical(page, "review-selected")).toEqual([]);
});

test("render step has no serious/critical a11y violations", async ({ page }) => {
  // Hold the render step open so it can be audited (it normally auto-advances).
  // The studio wizard submits an async render job (POST /cases/render/jobs),
  // not the synchronous POST /cases/render — hold THAT route instead, then
  // fall back to the default mock (installApiMocks, registered in
  // beforeEach) so the submit still eventually resolves normally.
  await page.route("**/cases/render/jobs", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    await new Promise((r) => setTimeout(r, 4000));
    await route.fallback();
  });
  await page.goto("/");
  await page.getByRole("button", { name: /Try a sample scenario/i }).click();
  await page.getByRole("button", { name: /Rear-end at a red light/i }).click();
  await page.getByRole("button", { name: /Extract scene/i }).click();
  await page.getByRole("button", { name: /Confirm .* render this case/i }).click();
  await expect(page.getByRole("heading", { name: /Sealing your case/i })).toBeVisible();
  expect(await auditSeriousOrCritical(page, "render")).toEqual([]);
});

test("sealed result step has no serious/critical a11y violations", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /Try a sample scenario/i }).click();
  await page.getByRole("button", { name: /Rear-end at a red light/i }).click();
  await page.getByRole("button", { name: /Extract scene/i }).click();
  await page.getByRole("button", { name: /Confirm .* render this case/i }).click();
  await expect(page.getByRole("heading", { name: /Case sealed/i })).toBeVisible();
  // Reveal the post-Verify surfaces too, so they're audited.
  await page.getByRole("button", { name: /Verify in browser/i }).click();
  await expect(page.getByText("Verified ✓")).toBeVisible();
  expect(await auditSeriousOrCritical(page, "result")).toEqual([]);
});
