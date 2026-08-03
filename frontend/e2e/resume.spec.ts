import { test, expect } from "@playwright/test";
import { GOLDEN_CASE_ID, installApiMocks } from "./_mocks";

/**
 * Losing the tab must not lose the case.
 *
 * A render takes minutes. Until the id lived in the URL, a refresh, an
 * accidental close or a browser reload threw away the whole in-flight case
 * even though the work carried on server-side and the sealed case stayed
 * fetchable by id forever. These specs drive the real browser through exactly
 * that: reload mid-render, reopen a sealed case cold, and open links that name
 * nothing.
 *
 * The unhappy paths matter as much as the happy one. An unknown id, an expired
 * one and another tenant's case are the SAME 404 from the server (it cannot
 * see outside the caller's own data), so they must produce the same plain
 * message and the same way forward, never a spinner that runs forever and
 * never a broken screen.
 */

test.beforeEach(async ({ page }) => {
  await installApiMocks(page);
});

async function runWizardToRender(page: import("@playwright/test").Page) {
  await page.goto("/");
  await page.getByRole("button", { name: /Try a sample scenario/i }).click();
  await page.getByRole("button", { name: /Rear-end at a red light/i }).click();
  await page.getByRole("button", { name: /Extract scene/i }).click();
  await expect(page.getByRole("heading", { name: /Review .* adjust the scene/i })).toBeVisible();
  await page.getByRole("button", { name: /Confirm .* render this case/i }).click();
}

test("a sealed case reopens from its own link, cold", async ({ page }) => {
  await page.goto(`/#case/${GOLDEN_CASE_ID}`);
  await expect(page.getByRole("heading", { name: /Case sealed/i })).toBeVisible();
  // The real sealed artifacts are on screen, not a summary of them.
  await expect(page.getByRole("heading", { name: /Incident report · factual/i }))
    .toBeVisible();
  await expect(page.getByText(/keep this link/i)).toBeVisible();
  // Reopening does not rewrite the address.
  expect(new URL(page.url()).hash).toBe(`#case/${GOLDEN_CASE_ID}`);
});

test("the URL names the case during the render, and a reload picks it back up",
  async ({ page }) => {
    await runWizardToRender(page);

    // The submitted job is in the address bar the moment it is accepted.
    await expect.poll(() => new URL(page.url()).hash).toMatch(/^#(job|case)\//);

    // Simulate the tab being lost and reopened at the same address.
    await page.reload();
    await expect(page.getByRole("heading", { name: /Case sealed/i })).toBeVisible();
    expect(new URL(page.url()).hash).toBe(`#case/${GOLDEN_CASE_ID}`);
  });

test("a sealed case survives a reload after it finished", async ({ page }) => {
  await runWizardToRender(page);
  await expect(page.getByRole("heading", { name: /Case sealed/i })).toBeVisible();
  expect(new URL(page.url()).hash).toBe(`#case/${GOLDEN_CASE_ID}`);

  await page.reload();
  await expect(page.getByRole("heading", { name: /Case sealed/i })).toBeVisible();
});

test("an unknown case link says so plainly and offers a fresh start",
  async ({ page }) => {
    await page.goto("/#case/definitely-not-a-real-case");
    await expect(page.getByText(/could not find this case/i)).toBeVisible();
    await expect(page.getByText(/may belong to a different account/i)).toBeVisible();
    // No spinner left running, and nothing technical on screen.
    const body = await page.locator("body").innerText();
    expect(body).not.toMatch(/\b404\b/);
    expect(body).not.toMatch(/undefined/);

    await page.getByRole("button", { name: /Start a new case/i }).click();
    await expect(page.getByRole("heading", { name: /Start a case/i })).toBeVisible();
    expect(new URL(page.url()).hash).toBe("#start");
  });

test("a link that is not a case link is rejected without a request", async ({ page }) => {
  const asked: string[] = [];
  page.on("request", (r) => {
    if (r.url().includes("/cases/")) asked.push(r.url());
  });

  await page.goto("/#case/not a valid id");
  await expect(page.getByText(/does not name a case/i)).toBeVisible();
  expect(asked, "a malformed link must not reach the network").toHaveLength(0);

  await page.getByRole("button", { name: /Start a new case/i }).click();
  await expect(page.getByRole("heading", { name: /Start a case/i })).toBeVisible();
});

test("the resume screens work on a 375px phone", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/#case/definitely-not-a-real-case");
  await expect(page.getByText(/could not find this case/i)).toBeVisible();

  const cta = page.getByRole("button", { name: /Start a new case/i });
  const box = await cta.boundingBox();
  expect(box, "the way forward has a box").not.toBeNull();
  expect(box!.height, `CTA height >=44 (got ${box!.height})`).toBeGreaterThanOrEqual(44);
  expect(box!.width, `CTA width >=44 (got ${box!.width})`).toBeGreaterThanOrEqual(44);

  const { scrollWidth, innerWidth } = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    innerWidth: window.innerWidth,
  }));
  expect(scrollWidth, "no horizontal scrolling on the resume screen")
    .toBeLessThanOrEqual(innerWidth + 1);
});

test("a sealed case on a phone keeps its link readable and its copy button tappable",
  async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto(`/#case/${GOLDEN_CASE_ID}`);
    await expect(page.getByRole("heading", { name: /Case sealed/i })).toBeVisible();

    const copy = page.getByRole("button", { name: /Copy link/i });
    const box = await copy.boundingBox();
    expect(box!.height, `copy button height >=44 (got ${box!.height})`).toBeGreaterThanOrEqual(44);
    expect(box!.width, `copy button width >=44 (got ${box!.width})`).toBeGreaterThanOrEqual(44);

    const { scrollWidth, innerWidth } = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      innerWidth: window.innerWidth,
    }));
    expect(scrollWidth, "a long link must not push the page sideways")
      .toBeLessThanOrEqual(innerWidth + 1);
  });

test("the wait is quoted from measurement, before and during the render",
  async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: /Try a sample scenario/i }).click();
    await page.getByRole("button", { name: /Rear-end at a red light/i }).click();
    await page.getByRole("button", { name: /Extract scene/i }).click();
    await expect(page.getByRole("heading", { name: /Review .* adjust the scene/i })).toBeVisible();

    // Before committing: measured median + slow end + how many cases it is from.
    await expect(page.getByText(/Rendering takes about 4 minutes here/i)).toBeVisible();
    await expect(page.getByText(/last 6 cases/i)).toBeVisible();

    await page.getByRole("button", { name: /Confirm .* render this case/i }).click();
    // During: their own wait, next to the same measured figure.
    await expect(page.getByText(/Waiting 0 s/i)).toBeVisible();
  });
