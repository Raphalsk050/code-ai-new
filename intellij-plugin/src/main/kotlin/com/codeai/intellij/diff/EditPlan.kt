package com.codeai.intellij.diff

import com.codeai.intellij.util.array
import com.codeai.intellij.util.json
import com.codeai.intellij.util.number
import com.codeai.intellij.util.string
import com.codeai.intellij.util.text
import com.google.gson.JsonElement
import com.google.gson.JsonObject

// What a file-writing tool call is about to leave on disk, worked out from its
// arguments the way the tool itself works it out, so the editor can show the
// change before it lands. A port of the VS Code extension's src/edit-plan.ts.

/** The tools whose calls the editor shows as a diff. */
internal val DIFF_TOOLS = setOf("write_file", "edit_code")

internal data class Edit(val old: String, val new: String, val expected: Double = 1.0)

/** The edits of an edit_code call, in either shape the tool accepts; null if malformed. */
internal fun editsOf(args: JsonObject): List<Edit>? {
    val list: List<JsonElement> =
        args.array("edits")?.toList() ?: listOf(json("old" to args.get("old_text"), "new" to args.get("new_text")))
    val edits = mutableListOf<Edit>()
    for (item in list) {
        if (!item.isJsonObject) return null
        val fields = item.asJsonObject
        val old = fields.string("old")
        val new = fields.string("new")
        if (old.isNullOrEmpty() || new == null) return null
        val expected = (if (fields.has("expected_occurrences")) fields.number("expected_occurrences") else 1.0)
            ?.takeUnless { it == 0.0 || it.isNaN() } ?: 1.0
        edits += Edit(old, new, expected)
    }
    return edits.ifEmpty { null }
}

/**
 * `text` with the edits applied, or null where the tool would refuse: text not
 * found, found a different number of times than expected, or edits overlapping.
 */
internal fun applyEdits(text: String, edits: List<Edit>): String? {
    class Span(val start: Int, val end: Int, val text: String)

    val spans = mutableListOf<Span>()
    for (edit in edits) {
        val found = mutableListOf<Int>()
        var at = text.indexOf(edit.old)
        while (at >= 0) {
            found += at
            at = text.indexOf(edit.old, at + edit.old.length)
        }
        if (found.size.toDouble() != edit.expected) return null
        for (start in found) spans += Span(start, start + edit.old.length, edit.new)
    }
    spans.sortBy { it.start }
    val out = StringBuilder()
    var cursor = 0
    for (span in spans) {
        if (span.start < cursor) return null
        out.append(text, cursor, span.start).append(span.text)
        cursor = span.end
    }
    return out.append(text, cursor, text.length).toString()
}

/** The file a write_file or edit_code call leaves behind, given what is there now. */
internal fun proposedContent(tool: String, args: JsonObject, current: String): String? {
    if (tool == "write_file") return args.string("content")
    if (tool != "edit_code") return null
    return editsOf(args)?.let { applyEdits(current, it) }
}

/** Whether a call writes into the project, as opposed to the session sandbox. */
internal fun writesToProject(args: JsonObject): Boolean = args.text("location").trim().lowercase() != "sandbox"

internal enum class DiffPhase { PROPOSED, WRITTEN }

/** The diff tab's title: the file name, and whose change it is. */
internal fun diffTitle(relative: String, phase: DiffPhase): String {
    val name = relative.split('/', '\\').last().ifEmpty { relative }
    return if (phase == DiffPhase.PROPOSED) "$name (Code-AI, proposed)" else "$name (Code-AI)"
}
