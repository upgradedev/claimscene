import { test, expect, type Locator, type Page } from "@playwright/test";
import { installApiMocks } from "./_mocks";

/**
 * Responsive + tap-target + focus-visibility gate.
 *
 *  - No horizontal overflow at 375 / 768 / 1280 on the landing page and through
 *    the wizard.
 *  - Primary controls are ≥44×44px on mobile (WCAG 2.5.5), and every other
 *    interactive control clears the 24px AA floor (WCAG 2.5.8). The 12-position
 *    clock (impact + damage) is also ≥44×44 -- it is the review step's most
 *    consequential input, entered on a phone -- via a transparent 44x44 hit
 *    target around a small visual dot (see ClockPicker.tsx). Per-field
 *    dropdowns stay covered by the 24px AA floor only.
 *  - Both hero CTAs show a visible focus ring under keyboard focus.
 */

const WIDTHS = [375, 768, 1280] as const;

async function assertNoHorizontalOverflow(page: Page, label: string) {
  const { scrollWidth, innerWidth } = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    innerWidth: window.innerWidth,
  }));
  // A 1px allowance absorbs sub-pixel rounding of the fixed-grid background.
  expect(scrollWidth, `${label}: page must not scroll horizontally`).toBeLessThanOrEqual(innerWidth + 1);
}

test.beforeEach(async ({ page }) => {
  await installApiMocks(page);
});

for (const width of WIDTHS) {
  test(`no horizontal overflow at ${width}px (landing → source → review → sealed)`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });

    await page.goto("/");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await assertNoHorizontalOverflow(page, `landing@${width}`);

    await page.getByRole("button", { name: /Try a sample scenario/i }).click();
    await expect(page.getByRole("heading", { name: /Start a case/i })).toBeVisible();
    await assertNoHorizontalOverflow(page, `source@${width}`);

    await page.getByRole("button", { name: /Rear-end at a red light/i }).click();
    await page.getByRole("button", { name: /Extract scene/i }).click();
    await expect(page.getByRole("heading", { name: /Review .* adjust the scene/i })).toBeVisible();
    await assertNoHorizontalOverflow(page, `review@${width}`);

    await page.getByRole("button", { name: /Confirm .* render this case/i }).click();
    await expect(page.getByRole("heading", { name: /Case sealed/i })).toBeVisible();
    await assertNoHorizontalOverflow(page, `result@${width}`);
  });
}

test("primary controls are >=44px tap targets on mobile (375)", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 900 });

  const atLeast44 = async (loc: Locator, name: string) => {
    await expect(loc, `${name} present`).toBeVisible();
    const box = await loc.boundingBox();
    expect(box, `${name} has a box`).not.toBeNull();
    expect(box!.height, `${name} height >=44 (got ${box!.height})`).toBeGreaterThanOrEqual(44);
    expect(box!.width, `${name} width >=44 (got ${box!.width})`).toBeGreaterThanOrEqual(44);
  };

  await page.goto("/");
  await atLeast44(page.getByRole("link", { name: /ClaimScene home/i }), "logo home link");
  await atLeast44(page.getByRole("button", { name: /^Start a case/i }), "Start a case CTA");
  await atLeast44(page.getByRole("button", { name: /Try a sample scenario/i }), "sample CTA");
  await atLeast44(page.getByRole("link", { name: /github\.com\/upgradedev\/claimscene/i }), "footer repo link");

  await page.getByRole("button", { name: /Try a sample scenario/i }).click();
  await atLeast44(page.getByRole("button", { name: /Browse files/i }), "Browse files");
  await atLeast44(page.getByRole("button", { name: /Extract scene/i }), "Extract scene CTA");
});

test("review step: every 12-position clock radio is a >=44px tap target on mobile (375)", async ({ page }) => {
  // The most consequential input in the case (impact point / damage zone),
  // entered on a phone at a roadside -- flagged as under the comfortable
  // minimum at the old 24px size. ClockPicker.tsx now wraps a small visual
  // dot in a 44x44 hit target; this asserts the real rendered box for EVERY
  // position on EVERY clock on the step, not just a sample.
  await page.setViewportSize({ width: 375, height: 900 });
  await page.goto("/");
  await page.getByRole("button", { name: /Try a sample scenario/i }).click();
  await page.getByRole("button", { name: /Rear-end at a red light/i }).click();
  await page.getByRole("button", { name: /Extract scene/i }).click();
  await expect(page.getByRole("heading", { name: /Review .* adjust the scene/i })).toBeVisible();

  const groups = page.getByRole("radiogroup");
  const groupCount = await groups.count();
  expect(groupCount, "at least one clock renders").toBeGreaterThan(0);

  let radiosChecked = 0;
  for (let g = 0; g < groupCount; g++) {
    const radios = groups.nth(g).getByRole("radio");
    const radioCount = await radios.count();
    expect(radioCount, `clock #${g} has 12 positions`).toBe(12);
    for (let i = 0; i < radioCount; i++) {
      const radio = radios.nth(i);
      const box = await radio.boundingBox();
      expect(box, `clock #${g} position #${i} has a box`).not.toBeNull();
      // 0.5px tolerance mirrors the 23.5-for-24 floor below: `h-11` (2.75rem)
      // is exactly 44px, but a percentage-positioned, translate(-50%,-50%)
      // element can report a bounding box a fraction of a px short (observed:
      // 43.9998779296875) from sub-pixel rounding during compositing, not a
      // real size difference.
      expect(box!.width, `clock #${g} position #${i} width >=44 (got ${box!.width})`).toBeGreaterThanOrEqual(43.5);
      expect(box!.height, `clock #${g} position #${i} height >=44 (got ${box!.height})`).toBeGreaterThanOrEqual(43.5);
      radiosChecked += 1;
    }
  }
  expect(radiosChecked, "checked every clock position on the step").toBeGreaterThanOrEqual(24);

  await assertNoHorizontalOverflow(page, "review-with-grown-clocks@375");
});

test("secondary interactive controls clear the 24px AA floor on mobile (375)", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 900 });
  await page.goto("/");
  await page.getByRole("button", { name: /Try a sample scenario/i }).click();
  await expect(page.getByRole("heading", { name: /Start a case/i })).toBeVisible();

  const controls = page.locator("a[href], button, select, [role=radio], [role=button]");
  const n = await controls.count();
  let checked = 0;
  for (let i = 0; i < n; i++) {
    const c = controls.nth(i);
    if (!(await c.isVisible())) continue;
    const box = await c.boundingBox();
    if (!box) continue;
    // Skip sr-only / offscreen-until-focused elements (e.g. the skip link),
    // which collapse to a 1px box until they receive focus.
    if (box.height <= 1.5 || box.width <= 1.5) continue;
    checked += 1;
    expect(box.height, `control #${i} height >=24`).toBeGreaterThanOrEqual(23.5);
    expect(box.width, `control #${i} width >=24`).toBeGreaterThanOrEqual(23.5);
  }
  expect(checked, "audited at least a handful of controls").toBeGreaterThan(3);
});

test("both hero CTAs show a visible focus ring under keyboard focus", async ({ page }) => {
  const ringVisible = async (loc: Locator, name: string) => {
    // Tab from the top so Chromium's :focus-visible heuristic engages (a
    // programmatic .focus() would not trigger it).
    await page.goto("/");
    let focused = false;
    for (let i = 0; i < 16 && !focused; i++) {
      await page.keyboard.press("Tab");
      focused = await loc.evaluate((el) => el === document.activeElement).catch(() => false);
    }
    expect(focused, `${name} reachable by keyboard`).toBeTruthy();
    const s = await loc.evaluate((el) => {
      const cs = getComputedStyle(el);
      return { boxShadow: cs.boxShadow, outlineStyle: cs.outlineStyle, outlineWidth: cs.outlineWidth };
    });
    const visible =
      (s.boxShadow !== "none" && s.boxShadow !== "") ||
      (s.outlineStyle !== "none" && s.outlineWidth !== "0px");
    expect(visible, `${name} focus ring visible (got ${JSON.stringify(s)})`).toBeTruthy();
  };

  await ringVisible(page.getByRole("button", { name: /^Start a case/i }), "Start a case");
  await ringVisible(page.getByRole("button", { name: /Try a sample scenario/i }), "Try a sample scenario");
});

test("placement panel: marker + rotate handle are >=44px thumb targets and stay draggable-safe on mobile (375)", async ({
  page,
}) => {
  await page.setViewportSize({ width: 375, height: 900 });
  await page.goto("/");
  await page.getByRole("button", { name: /Try a sample scenario/i }).click();
  await page.getByRole("button", { name: /Rear-end at a red light/i }).click();
  await page.getByRole("button", { name: /Extract scene/i }).click();
  await expect(page.getByRole("heading", { name: /Review .* adjust the scene/i })).toBeVisible();

  const panel = page.getByRole("group", { name: /vehicle placement canvas/i });
  const marker = panel.getByRole("button", { name: /^veh_a:/i });
  const atLeast44 = async (loc: Locator, name: string) => {
    const box = await loc.boundingBox();
    expect(box, `${name} has a box`).not.toBeNull();
    expect(box!.width, `${name} width >=44 (got ${box!.width})`).toBeGreaterThanOrEqual(44);
    expect(box!.height, `${name} height >=44 (got ${box!.height})`).toBeGreaterThanOrEqual(44);
  };
  await atLeast44(marker, "vehicle marker");

  // Selecting reveals the rotate handle — also a thumb target, and both must
  // ignore native touch-scroll gestures (touch-action: none) so a drag isn't
  // hijacked by the page scrolling under the reviewer's thumb.
  await marker.click();
  const handle = page.getByRole("button", { name: /^Rotate veh_a:/i });
  await atLeast44(handle, "rotate handle");

  for (const loc of [marker, handle]) {
    const touchAction = await loc.evaluate((el) => getComputedStyle(el).touchAction);
    expect(touchAction).toBe("none");
  }

  await assertNoHorizontalOverflow(page, "review-with-vehicle-selected@375");
});

test("dragging a vehicle on the placement panel introduces no horizontal overflow on mobile (375)", async ({
  page,
}) => {
  await page.setViewportSize({ width: 375, height: 900 });
  await page.goto("/");
  await page.getByRole("button", { name: /Try a sample scenario/i }).click();
  await page.getByRole("button", { name: /Rear-end at a red light/i }).click();
  await page.getByRole("button", { name: /Extract scene/i }).click();
  await expect(page.getByRole("heading", { name: /Review .* adjust the scene/i })).toBeVisible();

  const panel = page.getByRole("group", { name: /vehicle placement canvas/i });
  const marker = panel.getByRole("button", { name: /^veh_a:/i });
  const box = (await marker.boundingBox())!;
  const center = { x: box.x + box.width / 2, y: box.y + box.height / 2 };
  await page.mouse.move(center.x, center.y);
  await page.mouse.down();
  await page.mouse.move(center.x + 60, center.y, { steps: 6 });
  await page.mouse.up();

  await assertNoHorizontalOverflow(page, "review-after-drag@375");
});
