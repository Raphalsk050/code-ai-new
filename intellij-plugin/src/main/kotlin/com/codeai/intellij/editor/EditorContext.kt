package com.codeai.intellij.editor

import com.codeai.intellij.util.json
import com.google.gson.JsonObject

// What the user is looking at in the editor, as the panel and the model are
// told about it. No IDE import: EditorContexts gathers it, this file words it,
// and the tests run it bare. The VS Code extension words it the same way
// (vscode-extension/src/editor-context.ts): keep the two in step.

/** A snapshot of the editor, attached to a turn. */
internal data class EditorContext(
    /** Project-relative path of the active file, e.g. `src/app.ts`; "" when no file has the focus. */
    val path: String,
    /** The language id VS Code gives the file, e.g. `kotlin`. */
    val language: String,
    /** Selected text, if any. */
    val selection: String? = null,
    /** 1-based inclusive line range of the selection. */
    val startLine: Int? = null,
    val endLine: Int? = null,
    /** The other files open in the editor, project-relative, in tab order. */
    val openFiles: List<String> = emptyList(),
) {
    /** As the panel reads it: the `EditorContext` of the shared protocol. */
    fun toJson(): JsonObject = json("path" to path, "language" to language).also { out ->
        if (!selection.isNullOrEmpty() && startLine != null && endLine != null) {
            out.addProperty("selection", selection)
            out.addProperty("startLine", startLine)
            out.addProperty("endLine", endLine)
        }
        if (openFiles.isNotEmpty()) out.add("openFiles", com.codeai.intellij.util.toJson(openFiles))
    }
}

/** How many of the other open files are named to the model; the rest are counted. */
internal const val MAX_OPEN_FILES_NAMED = 30

/**
 * The other files open in the editor, as the context lists them: in tab order,
 * each once, without the active one, with forward slashes.
 */
internal fun otherOpenFiles(open: List<String>, active: String? = null): List<String> {
    val skip = active?.replace('\\', '/')
    return open.map { it.replace('\\', '/') }.filter { it.isNotEmpty() && it != skip }.distinct()
}

/** Render an [EditorContext] into the preamble the model receives. */
internal fun formatEditorContext(context: EditorContext?): String {
    if (context == null) return ""
    val lines = mutableListOf<String>()
    if (context.path.isNotEmpty()) {
        lines += "[Editor context] Active file: ${context.path} (${context.language})"
        if (!context.selection.isNullOrEmpty() && context.startLine != null && context.endLine != null) {
            lines += "User has selected lines ${context.startLine}-${context.endLine}:"
            lines += "```" + context.language
            lines += context.selection
            lines += "```"
        } else {
            lines += "The user currently has this file open (no selection)."
        }
    }
    val open = context.openFiles
    if (open.isNotEmpty()) {
        lines += if (context.path.isNotEmpty()) "Other files open in the editor:"
        else "[Editor context] Files open in the editor (none of them has the focus):"
        open.take(MAX_OPEN_FILES_NAMED).mapTo(lines) { "- $it" }
        if (open.size > MAX_OPEN_FILES_NAMED) lines += "- and ${open.size - MAX_OPEN_FILES_NAMED} more"
    }
    return lines.joinToString("\n")
}
