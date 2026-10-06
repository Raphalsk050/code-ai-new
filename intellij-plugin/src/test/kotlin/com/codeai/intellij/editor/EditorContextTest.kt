package com.codeai.intellij.editor

import kotlin.test.Test
import kotlin.test.assertEquals

class EditorContextTest {
    @Test
    fun `the active file is named, with the selection when there is one`() {
        assertEquals("", formatEditorContext(null))
        assertEquals(
            "[Editor context] Active file: src/app.ts (typescript)\nThe user currently has this file open (no selection).",
            formatEditorContext(EditorContext("src/app.ts", "typescript")),
        )
        assertEquals(
            "[Editor context] Active file: src/app.ts (typescript)\nUser has selected lines 3-3:\n```typescript\nconst x = 1;\n```",
            formatEditorContext(EditorContext("src/app.ts", "typescript", "const x = 1;", 3, 3)),
        )
    }

    @Test
    fun `the other open files follow, so the model knows what else is on screen`() {
        assertEquals(
            "[Editor context] Active file: src/app.ts (typescript)\n" +
                "The user currently has this file open (no selection).\n" +
                "Other files open in the editor:\n- src/api.ts\n- README.md",
            formatEditorContext(EditorContext("src/app.ts", "typescript", openFiles = listOf("src/api.ts", "README.md"))),
        )
        // An empty list says nothing at all: the text is the one from before the list existed.
        assertEquals(
            formatEditorContext(EditorContext("a.py", "python")),
            formatEditorContext(EditorContext("a.py", "python", openFiles = emptyList())),
        )
    }

    @Test
    fun `open files are context even when none of them has the focus`() {
        assertEquals(
            "[Editor context] Files open in the editor (none of them has the focus):\n- graph.flow.json\n- src/app.ts",
            formatEditorContext(EditorContext("", "", openFiles = listOf("graph.flow.json", "src/app.ts"))),
        )
        assertEquals("", formatEditorContext(EditorContext("", "")))
    }

    @Test
    fun `a long list is cut short and says how much it left out`() {
        val openFiles = List(MAX_OPEN_FILES_NAMED + 12) { "src/f$it.ts" }
        val lines = formatEditorContext(EditorContext("a.ts", "typescript", openFiles = openFiles)).split("\n")
        assertEquals(3 + MAX_OPEN_FILES_NAMED + 1, lines.size)
        assertEquals("- src/f0.ts", lines[3])
        assertEquals("- and 12 more", lines.last())
    }

    @Test
    fun `the list is in tab order, each file once, without the active one`() {
        assertEquals(
            listOf("src/api.ts", "README.md"),
            otherOpenFiles(listOf("src\\app.ts", "src/api.ts", "README.md", "src/api.ts", ""), "src/app.ts"),
        )
        assertEquals(listOf("a.ts", "b.ts"), otherOpenFiles(listOf("a.ts", "b.ts")))
    }

    @Test
    fun `the panel gets the context in the shape the protocol names`() {
        assertEquals(
            """{"path":"src/app.kt","language":"kotlin","selection":"val x = 1","startLine":4,"endLine":4,"openFiles":["README.md"]}""",
            EditorContext("src/app.kt", "kotlin", "val x = 1", 4, 4, listOf("README.md")).toJson().toString(),
        )
        assertEquals("""{"path":"a.kt","language":"kotlin"}""", EditorContext("a.kt", "kotlin").toJson().toString())
    }
}
