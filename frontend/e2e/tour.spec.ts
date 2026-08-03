import { test, expect, type Locator, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { installApiMocks } from "./_mocks";

/**
 * The guided tour, driven for real.
 *
 * A judge opens this app cold with about a minute to work out what it is, so
 * the tour is the first thing they touch. jsdom cannot see any of what matters
 * here: that the panel fits a 375px screen, that its controls are thumb-sized,
 * that it is reachable and leavable from the keyboard alone, that it carries no
 * developer vocabulary, and that its closing call to action really does land
 * the visitor in the studio.
 *
 * Deliberately NOT asserted: scroll position or "is the ringed element in the
 * viewport". `html { scroll-behavior: smooth }` is set globally, and this repo
 * has twice been bitten by asserting mid-transition (see the opacity notes in
 * a11y.spec.ts). Everything below settles synchronously: panel text, the step
 * counter, and which element carries `data-tour-active`.
 */

/** Titles + the anchor each step points at, in order. Kept as literals rather
 *  than imported from the component so a copy change has to be made twice, on
 *  purpose: this copy is the product's argument, not incidental strings. */
const STEPS = [
  { title: "Two layers, kept apart", anchor: "what-it-is" },
  { title: "The drawing is the factual layer", anchor: "factual-layer" },
  { title: "The AI picture is an illustration", anchor: "illustration-layer" },
  { title: "You confirm every field", anchor: "you-confirm" },
  { title: "Sealed, and you can check it yourself", anchor: "verify" },
  { title: "What sealed does not mean", anchor: "disclosure" },
] as const;

const openTour = async (page: Page) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await page.getByRole("button", { name: /Take the guided tour/i }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
};

const tour = (page: Page) => page.getByRole("dialog");
const next = (page: Page) => tour(page).getByRole("button", { name: /^Next/i }).click();

async function expectStep(page: Page, i: number) {
  const step = STEPS[i]!;
  await expect(tour(page).getByRole("heading", { name: step.title })).toBeVisible();
  await expect(tour(page).getByText(new RegExp(`step ${i + 1} of ${STEPS.length}`, "i"))).toBeVisible();
  // Exactly one element on the page is ringed, and it is this step's subject.
  const ringed = page.locator("[data-tour-active]");
  await expect(ringed).toHaveCount(1);
  await expect(ringed).toHaveAttribute("data-tour", step.anchor);
}

async function assertNoHorizontalOverflow(page: Page, label: string) {
  const { scrollWidth, innerWidth } = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    innerWidth: window.innerWidth,
  }));
  expect(scrollWidth, `${label}: page must not scroll horizontally (${scrollWidth} vs ${innerWidth})`)
    .toBeLessThanOrEqual(innerWidth + 1);
}

test.beforeEach(async ({ page }) => {
  await installApiMocks(page);
});

test("the tour is opt-in: nothing overlays the page until it is asked for", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.locator("[data-tour-active]")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Take the guided tour/i })).toBeVisible();
});

test("walks every step, ringing what each one is about, and ends in the studio", async ({ page }) => {
  await openTour(page);
  for (let i = 0; i < STEPS.length; i++) {
    await expectStep(page, i);
    if (i < STEPS.length - 1) await next(page);
  }
  // The last step swaps Next for the call to action.
  await expect(tour(page).getByRole("button", { name: /^Next/i })).toHaveCount(0);
  await tour(page).getByRole("button", { name: /^Start a case/i }).click();

  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.locator("[data-tour-active]")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: /Start a case/i })).toBeVisible();
});

test("goes back as well as forward", async ({ page }) => {
  await openTour(page);
  await next(page);
  await next(page);
  await expectStep(page, 2);
  await tour(page).getByRole("button", { name: /^Back/i }).click();
  await expectStep(page, 1);
});

test("is fully operable from the keyboard, and Escape leaves without trapping", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();

  // Reach the trigger by Tab alone, then open it with the keyboard.
  const trigger = page.getByRole("button", { name: /Take the guided tour/i });
  let reached = false;
  for (let i = 0; i < 20 && !reached; i++) {
    await page.keyboard.press("Tab");
    reached = await trigger.evaluate((el) => el === document.activeElement);
  }
  expect(reached, "the tour trigger is reachable by Tab").toBeTruthy();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("dialog")).toBeVisible();

  // Focus lands on the primary action, so Enter alone walks the tour.
  const nextBtn = tour(page).getByRole("button", { name: /^Next/i });
  expect(await nextBtn.evaluate((el) => el === document.activeElement)).toBeTruthy();
  const ring = await nextBtn.evaluate((el) => {
    const cs = getComputedStyle(el);
    return { boxShadow: cs.boxShadow, outlineStyle: cs.outlineStyle, outlineWidth: cs.outlineWidth };
  });
  expect(
    (ring.boxShadow !== "none" && ring.boxShadow !== "") ||
      (ring.outlineStyle !== "none" && ring.outlineWidth !== "0px"),
    `focused control shows a visible ring (got ${JSON.stringify(ring)})`,
  ).toBeTruthy();

  await page.keyboard.press("Enter");
  await expectStep(page, 1);
  // Arrow keys walk it too, without reaching for a control.
  await page.keyboard.press("ArrowRight");
  await expectStep(page, 2);
  await page.keyboard.press("ArrowLeft");
  await expectStep(page, 1);

  // The panel is not a focus trap: Tab keeps moving through the page behind it.
  await page.keyboard.press("Tab");
  const stillInside = await tour(page).evaluate((el) => el.contains(document.activeElement));
  expect(stillInside, "Tab is not trapped inside the tour").toBeFalsy();

  // Escape leaves from anywhere, and focus returns to what opened it.
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(await trigger.evaluate((el) => el === document.activeElement)).toBeTruthy();
});

test("a dismissed tour is remembered and stops nudging", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText(/New here\?/i)).toBeVisible();
  await page.getByRole("button", { name: /Take the guided tour/i }).click();
  await tour(page).getByRole("button", { name: /Leave tour/i }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);

  await page.reload();
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(page.getByText(/New here\?/i)).toHaveCount(0);
  // The invitation itself stays, so it is never lost, only quieter.
  await expect(page.getByRole("button", { name: /Take the guided tour/i })).toBeVisible();
});

test("no step shows developer vocabulary", async ({ page }) => {
  // Same guard as plain-language.spec.ts, applied to surface that spec never
  // reaches because the tour does not open on its own.
  const JARGON = [/\bVLM\b/i, /\bB2 storage\b/i, /\bbackend\b/i, /\bendpoint\b/i, /\blocalhost\b/i, /\bAPI ·/i];
  const BROKEN = [/\[object Object\]/, /\bundefined\b/, /\bNaN\b/];
  await openTour(page);
  for (let i = 0; i < STEPS.length; i++) {
    await expectStep(page, i);
    const text = await tour(page).innerText();
    for (const pattern of [...JARGON, ...BROKEN, /—/]) {
      expect(text, `tour step ${i + 1}: leaked "${pattern.source}"`).not.toMatch(pattern);
    }
    if (i < STEPS.length - 1) await next(page);
  }
});

for (const [label, index] of [["first", 0], ["last", STEPS.length - 1]] as const) {
  test(`the ${label} tour step has no serious/critical a11y violations`, async ({ page }) => {
    // Two audits, not six: step 1 and the last step are the two structural
    // variants of the panel (no Back / Back + call to action). The middle four
    // are the same DOM with different strings.
    await openTour(page);
    for (let i = 0; i < index; i++) await next(page);
    await expectStep(page, index);

    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();
    const bad = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
    if (bad.length) {
      console.log(
        `AXE[tour-${label}] serious/critical:`,
        JSON.stringify(bad.map((v) => ({ id: v.id, impact: v.impact, help: v.help, nodes: v.nodes.map((n) => n.target) })), null, 2),
      );
    }
    expect(bad).toEqual([]);
  });
}

test("mobile (375): every step fits, and every tour control is a >=44px thumb target", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await openTour(page);

  const atLeast44 = async (loc: Locator, name: string) => {
    const box = await loc.boundingBox();
    expect(box, `${name} has a box`).not.toBeNull();
    console.log(`TAP[375] ${name}: ${box!.width.toFixed(1)} x ${box!.height.toFixed(1)}`);
    expect(box!.width, `${name} width >=44 (got ${box!.width})`).toBeGreaterThanOrEqual(44);
    expect(box!.height, `${name} height >=44 (got ${box!.height})`).toBeGreaterThanOrEqual(44);
  };

  await atLeast44(page.getByRole("button", { name: /Take the guided tour/i }), "Take the guided tour");

  for (let i = 0; i < STEPS.length; i++) {
    await expectStep(page, i);
    await assertNoHorizontalOverflow(page, `tour step ${i + 1}@375`);

    // Measure EVERY visible control inside the panel on EVERY step, not a
    // sample: the control set changes between steps (Back appears, Next
    // becomes the call to action).
    const controls = tour(page).locator("button, a[href], [role=button]");
    const n = await controls.count();
    expect(n, `step ${i + 1} has controls`).toBeGreaterThan(0);
    for (let c = 0; c < n; c++) {
      const control = controls.nth(c);
      if (!(await control.isVisible())) continue;
      await atLeast44(control, `step ${i + 1} control "${(await control.innerText()).trim()}"`);
    }

    // The panel itself must sit inside the viewport, not merely avoid scrolling.
    const panel = (await tour(page).boundingBox())!;
    expect(panel.x, `step ${i + 1}: panel left edge on screen`).toBeGreaterThanOrEqual(0);
    expect(panel.x + panel.width, `step ${i + 1}: panel right edge on screen (${panel.x + panel.width})`)
      .toBeLessThanOrEqual(375);

    if (i < STEPS.length - 1) await next(page);
  }
});

test("mobile (375): the landing page still fits with the tour invitation on it", async ({ page }) => {
  // The invitation added a third hero button; three lg buttons on one line
  // overflow a narrow container unless the row is allowed to wrap.
  for (const width of [375, 768, 1280]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await assertNoHorizontalOverflow(page, `landing-with-tour-cta@${width}`);
  }
});
