/**
 * Whether a backend that just died should be started again.
 *
 * Up to `limit` deaths inside `windowMs` are restarted. Past that it is failing
 * on its own (a broken config, a missing DLL) and a restart loop would only
 * hide why, so it is left down for the user.
 */
export function afterCrash(
  crashes: readonly number[],
  now: number,
  limit = 3,
  windowMs = 120_000
): { restart: boolean; crashes: number[] } {
  const recent = [...crashes.filter((at) => now - at < windowMs), now];
  return { restart: recent.length <= limit, crashes: recent };
}
