// When to look for a newer release. No `vscode` in here, so the policy is
// tested on its own. The backend keeps the same numbers for the terminal
// (updates.next_check_delay).

/** The first look: once the window has settled, not while it is still opening. */
export const FIRST_CHECK_MS = 5_000;
/** After a look that got an answer, with or without a release in it. */
export const CHECK_EVERY_MS = 60 * 60 * 1000;
/** After one that did not: soon, then less and less often. */
const RETRY_AFTER_MS = [30_000, 2 * 60_000, 10 * 60_000, 30 * 60_000];

/** How long until the next look, after `failures` looks in a row got no answer. */
export function nextCheckDelay(failures: number): number {
  if (failures <= 0) return CHECK_EVERY_MS;
  return RETRY_AFTER_MS[Math.min(failures, RETRY_AFTER_MS.length) - 1];
}
