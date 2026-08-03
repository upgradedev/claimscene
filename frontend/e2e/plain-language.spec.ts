import { test, expect, type Page } from "@playwright/test";
import { installApiMocks } from "./_mocks";

/**
 * Guard: the consumer path must not leak developer vocabulary.
 *
 * The people who reach this app have just had a crash. Three separate builds
 * shipped internal wording straight at them and every one was caught by a
 * human reading the screen, never by CI: a header badge reading "API · live"
 * whose tooltip said "real B2 storage + VLM + Genblaze wired", a progress line
 * that said "Running the VLM extraction ladder on your photos", and a source
 * label of "VLM-extracted". Each fix was one string, so the cost was never the
 * repair, it was noticing.
 *
 * This walks the real journey and fails on the vocabulary itself, so the next
 * one breaks a build instead of reaching a claimant.
 *
 * Scope, stated honestly:
 *  - It checks what is VISIBLE (innerText), so it cannot see a tooltip that
 *    only appears on hover. The header badge that started this is caught by
 *    its own unit test in components/Header.test.tsx, which asserts the whole
 *    badge is absent rather than just its tooltip.
 *  - The sealed provenance ledger on the final step is deliberately EXEMPT.
 *    That panel is the forensic audit trail, its readers are adjusters and
 *    investigators, and the extractor's own note ("vlm: unsure whether the
 *    second car is silver or red") is sealed evidence that must be reproduced
 *    verbatim, not prettified. Technical wording is correct there. It is not
 *    correct on the way in.
 *  - It is a vocabulary check, not a readability score. Copy can still be bad
 *    English while passing.
 */

/** Words that mean something to us and nothing to someone filing a claim. */
const JARGON = [
  /\bVLM\b/i,
  /\bB2 storage\b/i,
  /\bbackend\b/i,
  /\bendpoint\b/i,
  /\blocalhost\b/i,
  /\bAPI ·/i,
];

/** Symptoms of a rendering bug that should never reach a screen either. */
const BROKEN_RENDER = [/\[object Object\]/, /\bundefined\b/, /\bNaN\b/];

async function assertPlainLanguage(page: Page, where: string) {
  const text = await page.locator("body").innerText();
  for (const pattern of [...JARGON, ...BROKEN_RENDER]) {
    expect(text, `${where}: leaked "${pattern.source}" into the visible UI`)
      .not.toMatch(pattern);
  }
}

test.beforeEach(async ({ page }) => {
  await installApiMocks(page);
});

test("the consumer path never shows developer vocabulary", async ({ page }) => {
  await page.goto("/");
  await assertPlainLanguage(page, "landing");

  await page.getByRole("button", { name: /Try a sample scenario/i }).click();
  await assertPlainLanguage(page, "step 1 (source)");

  await page.getByRole("button", { name: /Rear-end at a red light/i }).click();
  await page.getByRole("button", { name: /Extract scene/i }).click();

  await expect(page.getByRole("heading", { name: /Review .* adjust the scene/i }))
    .toBeVisible();
  await assertPlainLanguage(page, "step 2 (review and adjust)");
});

test("the upload progress line explains itself without naming a model class",
  async ({ page }) => {
    // The wording people see while waiting is the most-read copy in the app,
    // and it used to name an extraction ladder at them.
    await page.goto("/");
    await page.getByRole("button", { name: /Try a sample scenario/i }).click();
    await page.getByRole("button", { name: /Rear-end at a red light/i }).click();
    await page.getByRole("button", { name: /Extract scene/i }).click();

    await expect(page.getByRole("heading", { name: /Review .* adjust the scene/i }))
      .toBeVisible();
    // The scene source is attributed on the review step, in plain words.
    await expect(page.getByText(/VLM/i)).toHaveCount(0);
  });
