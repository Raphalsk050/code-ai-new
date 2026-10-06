package com.codeai.intellij.changes

// What the agent changed in this conversation, file by file, measured from the
// file as it was before the agent first touched it. No IDE import: the host
// wraps it, the tests drive it directly. A port of the VS Code extension's
// src/change-ledger.ts.

internal class LedgerEntry(
    /** Absolute path: the key. */
    val path: String,
    /** Project-relative with forward slashes. */
    val relative: String,
    /** The text before the first change; null when it was never captured. */
    val before: String?,
    /** False when the agent created the file. */
    val existed: Boolean,
)

internal data class ChangeSummary(
    val path: String,
    val added: Int,
    val removed: Int,
    val created: Boolean,
    /** The original is known, so the change can be undone. */
    val revertable: Boolean,
)

internal class ChangeLedger {
    private val entries = LinkedHashMap<String, LedgerEntry>()

    /** The text a file held right before a write. The first one per file wins. */
    fun snapshot(path: String, relative: String, content: String, existed: Boolean) {
        val known = entries[path]
        if (known?.before != null) return
        entries[path] = LedgerEntry(path, relative, content, if (known != null) known.existed && existed else existed)
    }

    /** A write landed; keep the file even when no snapshot arrived for it. */
    fun touched(path: String, relative: String) {
        if (path !in entries) entries[path] = LedgerEntry(path, relative, before = null, existed = true)
    }

    fun get(path: String): LedgerEntry? = entries[path]

    fun byRelative(relative: String): LedgerEntry? = entries.values.firstOrNull { it.relative == relative }

    fun forget(path: String) {
        entries.remove(path)
    }

    fun clear() {
        entries.clear()
    }

    fun list(): List<LedgerEntry> = entries.values.toList()

    /**
     * Each file against what is on disk now (`current` returns null for a file
     * that is gone). A file back to how it was drops out.
     */
    fun summarize(current: (path: String) -> String?): List<ChangeSummary> {
        val out = mutableListOf<ChangeSummary>()
        for (entry in entries.values) {
            val now = current(entry.path)
            val before = entry.before
            if (before == null) {
                out += ChangeSummary(entry.relative, added = 0, removed = 0, created = false, revertable = false)
                continue
            }
            val after = now ?: ""
            if (entry.existed && now != null && after == before) continue
            if (!entry.existed && now == null) continue
            val (added, removed) = countLines(before, after)
            out += ChangeSummary(entry.relative, added, removed, created = !entry.existed, revertable = true)
        }
        return out
    }
}

internal data class LineCount(val added: Int, val removed: Int)

/** Lines a diff of the two texts adds and removes. */
internal fun countLines(before: String, after: String): LineCount {
    val old = lines(before)
    val new = lines(after)
    val common = commonLines(old, new)
    return LineCount(added = new.size - common, removed = old.size - common)
}

/** A text's lines, each with the line ending it has: a changed ending is a changed line. */
private fun lines(text: String): List<String> {
    val out = ArrayList<String>()
    var start = 0
    while (start < text.length) {
        val newline = text.indexOf('\n', start)
        val end = if (newline < 0) text.length else newline + 1
        out += text.substring(start, end)
        start = end
    }
    return out
}

/**
 * How many lines the two texts share in order: the length of their longest
 * common subsequence, found by Myers' greedy search, which a minimal diff
 * leaves untouched.
 */
private fun commonLines(a: List<String>, b: List<String>): Int {
    var head = 0
    while (head < a.size && head < b.size && a[head] == b[head]) head++
    var tail = 0
    while (tail < a.size - head && tail < b.size - head && a[a.size - 1 - tail] == b[b.size - 1 - tail]) tail++
    val n = a.size - head - tail
    val m = b.size - head - tail
    if (n == 0 || m == 0) return head + tail

    val max = n + m
    // Furthest x reached on each diagonal k = x - y, offset so k can be negative.
    val furthest = IntArray(2 * max + 2)
    for (d in 0..max) {
        var k = -d
        while (k <= d) {
            val down = k == -d || (k != d && furthest[max + k - 1] < furthest[max + k + 1])
            var x = if (down) furthest[max + k + 1] else furthest[max + k - 1] + 1
            var y = x - k
            while (x < n && y < m && a[head + x] == b[head + y]) {
                x++
                y++
            }
            furthest[max + k] = x
            // d edits turn one middle into the other: what they left alone is shared.
            if (x >= n && y >= m) return head + tail + (max - d) / 2
            k += 2
        }
    }
    return head + tail
}
