package com.codeai.intellij.board

import com.codeai.intellij.bridge.BusEvent
import com.codeai.intellij.util.array
import com.codeai.intellij.util.json
import com.codeai.intellij.util.obj
import com.codeai.intellij.util.text
import com.google.gson.JsonObject
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull

class BoardMessagesTest {
    private fun ev(type: String, payload: JsonObject = JsonObject()) = BusEvent(
        json(
            "event_id" to type, "event_type" to type, "event_version" to 1, "session_id" to "s",
            "sequence" to 0, "timestamp" to "", "source" to "board", "payload" to payload,
        )
    )

    @Test
    fun `board events reach the tab as the VS Code host sends them`() {
        val board = json("lanes" to listOf(json("id" to "todo")), "tasks" to emptyList<Any>())
        val moved = listOf(json("task" to "T1", "from" to "in_progress", "to" to "done"))
        val snapshot = BoardMessages.forPanel(ev("board.changed", json("board" to board, "moved" to moved)))!!
        assertEquals("boardSnapshot", snapshot.text("type"))
        assertEquals("todo", snapshot.obj("board")!!.array("lanes")!![0].asJsonObject.text("id"))
        assertEquals("done", snapshot.array("moved")!![0].asJsonObject.text("to"))

        val review = BoardMessages.forPanel(
            ev("board.review.requested", json("tasks" to listOf("T1"), "message" to "check it"))
        )!!
        assertEquals("boardReview", review.text("type"))
        assertEquals("check it", review.text("message"))

        val done = BoardMessages.forPanel(
            ev("board.run.completed", json("status" to "completed", "done" to listOf("T1"), "failed" to listOf("T2")))
        )!!
        assertEquals("boardRunDone", done.text("type"))
        assertEquals("T2", done.array("failed")!![0].asString)

        val activity = BoardMessages.forPanel(
            ev("board.activity", json("task" to "T1", "subtask" to "T1.2", "agent" to "Turing", "text" to "edit"))
        )!!
        assertEquals("T1.2", activity.text("subtask"))

        assertNull(BoardMessages.forPanel(ev("model.stream.delta")))
    }

    @Test
    fun `the tab's messages become the board's bridge methods`() {
        val edit = BoardMessages.request(
            json("type" to "boardEdit", "operations" to listOf(json("op" to "add_task", "title" to "Login")))
        )!!
        assertEquals("boardEdit", edit.first)
        assertEquals("add_task", edit.second.array("operations")!![0].asJsonObject.text("op"))

        val play = BoardMessages.request(json("type" to "boardPlay", "tasks" to listOf("T3")))!!
        assertEquals("boardPlay" to "T3", play.first to play.second.array("tasks")!![0].asString)
        assertEquals("boardPlan", BoardMessages.request(json("type" to "boardPlan"))!!.first)
        assertEquals("boardStop", BoardMessages.request(json("type" to "boardStop"))!!.first)
        // Ready is the host's own business: it answers with the board.
        assertNull(BoardMessages.request(json("type" to "boardReady")))
    }
}
