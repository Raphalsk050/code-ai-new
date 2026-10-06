package com.codeai.intellij.bridge

internal data class CrashDecision(val restart: Boolean, val crashes: List<Long>)

/**
 * Whether a backend that just died should be started again.
 *
 * Up to `limit` deaths inside `windowMs` are restarted. Past that it is failing
 * on its own (a broken config, a missing library) and a restart loop would only
 * hide why, so it is left down for the user.
 */
internal fun afterCrash(
    crashes: List<Long>,
    now: Long,
    limit: Int = 3,
    windowMs: Long = 120_000,
): CrashDecision {
    val recent = crashes.filter { now - it < windowMs } + now
    return CrashDecision(recent.size <= limit, recent)
}
