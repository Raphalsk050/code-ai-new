package com.codeai.intellij.changes

import kotlin.test.Test
import kotlin.test.assertEquals

class ChangeLedgerTest {
    @Test
    fun `lines are counted against the file as it was first seen`() {
        val ledger = ChangeLedger()
        val disk = mapOf(
            "/w/a.cpp" to "one\nTWO\nthree\nfour\n",
            "/w/new.cpp" to "fresh\nfile\n",
            "/w/same.cpp" to "kept\n",
        )
        ledger.snapshot("/w/a.cpp", "a.cpp", "one\ntwo\nthree\n", true)
        // A second write's snapshot is the agent's own earlier edit: ignored.
        ledger.snapshot("/w/a.cpp", "a.cpp", "one\nTWO\nthree\n", true)
        ledger.snapshot("/w/new.cpp", "new.cpp", "", false)
        ledger.snapshot("/w/same.cpp", "same.cpp", "kept\n", true)
        ledger.touched("/w/big.bin", "big.bin")

        assertEquals(
            listOf(
                ChangeSummary("a.cpp", added = 2, removed = 1, created = false, revertable = true),
                ChangeSummary("new.cpp", added = 2, removed = 0, created = true, revertable = true),
                ChangeSummary("big.bin", added = 0, removed = 0, created = false, revertable = false),
            ),
            ledger.summarize { disk[it] },
        )
        assertEquals(false, ledger.byRelative("new.cpp")?.existed)
    }

    @Test
    fun `a created file that is gone again, or a file put back, drops out`() {
        val ledger = ChangeLedger()
        ledger.snapshot("/w/tmp.txt", "tmp.txt", "", false)
        ledger.snapshot("/w/b.txt", "b.txt", "x\n", true)
        assertEquals(emptyList(), ledger.summarize { if (it == "/w/b.txt") "x\n" else null })
        assertEquals(LineCount(added = 2, removed = 1), countLines("a\nb\n", "a\nc\nd\n"))
    }

    @Test
    fun `counts follow a minimal diff, line endings included`() {
        assertEquals(LineCount(0, 0), countLines("", ""))
        assertEquals(LineCount(0, 0), countLines("same\ntext\n", "same\ntext\n"))
        assertEquals(LineCount(3, 0), countLines("", "a\nb\nc"))
        assertEquals(LineCount(0, 2), countLines("a\nb\n", ""))
        // A line that lost its newline is a changed line.
        assertEquals(LineCount(1, 1), countLines("a\nb\n", "a\nb"))
        assertEquals(LineCount(1, 1), countLines("a\r\nb\r\n", "a\nb\r\n"))
        // Moving a line costs one removal and one addition, not a rewrite.
        assertEquals(LineCount(1, 1), countLines("a\nb\nc\nd\n", "b\nc\nd\na\n"))
        assertEquals(LineCount(2, 3), countLines("1\n2\n3\n4\n5\n6\n", "1\nx\n4\ny\n6\n"))
    }
}
