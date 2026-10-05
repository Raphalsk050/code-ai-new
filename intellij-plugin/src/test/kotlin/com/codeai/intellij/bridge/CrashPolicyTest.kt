package com.codeai.intellij.bridge

import kotlin.test.Test
import kotlin.test.assertEquals

class CrashPolicyTest {
    @Test
    fun `a backend that dies once is started again`() {
        val decision = afterCrash(emptyList(), 1_000)
        assertEquals(true, decision.restart)
        assertEquals(listOf(1_000L), decision.crashes)
    }

    @Test
    fun `one that keeps dying is left down for the user`() {
        var crashes = emptyList<Long>()
        val restarts = listOf(0L, 1L, 2L, 3L).map { minute ->
            val decision = afterCrash(crashes, minute * 10_000)
            crashes = decision.crashes
            decision.restart
        }
        assertEquals(listOf(true, true, true, false), restarts)
    }

    @Test
    fun `old crashes stop counting once the window has passed`() {
        val decision = afterCrash(listOf(0, 1_000, 2_000), 200_000)
        assertEquals(true, decision.restart)
        assertEquals(listOf(200_000L), decision.crashes)
    }
}
