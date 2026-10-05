package com.codeai.intellij.diff

import com.codeai.intellij.util.json
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNotNull
import kotlin.test.assertNull

class EditPlanTest {
    private val file = "def load():\n    timeout = raw['timeout']\n    return timeout\n"

    @Test
    fun `an edit_code call's proposal is the file with the edit applied`() {
        val next = proposedContent(
            "edit_code",
            json("path" to "a.py", "old_text" to "raw['timeout']", "new_text" to "float(raw['timeout'])"),
            file,
        )
        assertEquals("def load():\n    timeout = float(raw['timeout'])\n    return timeout\n", next)
    }

    @Test
    fun `a write_file call's proposal is its content`() {
        assertEquals("x = 1\n", proposedContent("write_file", json("content" to "x = 1\n"), file))
        assertNull(proposedContent("write_file", json(), file))
        assertNull(proposedContent("read_file", json("path" to "a.py"), file))
    }

    @Test
    fun `an edit the tool would refuse has no proposal`() {
        // Not there.
        assertNull(applyEdits(file, listOf(Edit("nope", "x"))))
        // There twice, expected once.
        assertNull(applyEdits("a a", listOf(Edit("a", "b"))))
        // Overlapping.
        assertNull(applyEdits("abcdef", listOf(Edit("abc", "x"), Edit("cde", "y"))))
        assertNull(editsOf(json("old_text" to "", "new_text" to "x")))
    }

    @Test
    fun `several edits apply in file order, as the tool applies them`() {
        val edits = editsOf(
            json(
                "edits" to listOf(
                    mapOf("old" to "return timeout", "new" to "return timeout or 30"),
                    mapOf("old" to "def load():", "new" to "def load(raw):"),
                )
            )
        )
        assertNotNull(edits)
        assertEquals("def load(raw):\n    timeout = raw['timeout']\n    return timeout or 30\n", applyEdits(file, edits))
        assertEquals("b b", applyEdits("a a", listOf(Edit("a", "b", expected = 2.0))))
    }

    @Test
    fun `the expected count comes from the call, and defaults to one`() {
        val twice = editsOf(json("edits" to listOf(mapOf("old" to "a", "new" to "b", "expected_occurrences" to 2))))
        assertEquals(listOf(Edit("a", "b", 2.0)), twice)
        val zero = editsOf(json("edits" to listOf(mapOf("old" to "a", "new" to "b", "expected_occurrences" to 0))))
        assertEquals(listOf(Edit("a", "b", 1.0)), zero)
    }

    @Test
    fun `sandbox writes stay out of the editor, and titles name the file`() {
        assertEquals(false, writesToProject(json("location" to "sandbox")))
        assertEquals(true, writesToProject(json("location" to "project")))
        assertEquals(true, writesToProject(json()))
        assertEquals("config.py (Code-AI, proposed)", diffTitle("src/app/config.py", DiffPhase.PROPOSED))
        assertEquals("config.py (Code-AI)", diffTitle("src\\config.py", DiffPhase.WRITTEN))
    }
}
