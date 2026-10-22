package com.codeai.intellij.board

import com.codeai.intellij.bridge.BusEvent
import com.codeai.intellij.util.array
import com.codeai.intellij.util.json
import com.codeai.intellij.util.obj
import com.codeai.intellij.util.text
import com.google.gson.JsonArray
import com.google.gson.JsonObject

/**
 * The board tab's wire, both ways: the VS Code extension's src/board-panel.ts,
 * as pure functions. The tab is the same bundle (webview/board), so the
 * messages are the same (src/board-schema.ts).
 */
internal object BoardMessages {
    /** What the tab is told of a bus event, or null when the event is not the board's. */
    fun forPanel(event: BusEvent): JsonObject? {
        val payload = event.payload
        return when (event.type) {
            "board.changed" -> json(
                "type" to "boardSnapshot",
                "board" to (payload.obj("board") ?: JsonObject()),
                "moved" to (payload.array("moved") ?: JsonArray()),
            )
            "board.activity" -> json(
                "type" to "boardActivity",
                "task" to payload.text("task"),
                "subtask" to payload.text("subtask"),
                "agent" to payload.text("agent"),
                "text" to payload.text("text"),
            )
            "board.review.requested" -> json(
                "type" to "boardReview",
                "tasks" to (payload.array("tasks") ?: JsonArray()),
                "message" to payload.text("message"),
            )
            "board.run.completed" -> json(
                "type" to "boardRunDone",
                "status" to payload.text("status"),
                "done" to (payload.array("done") ?: JsonArray()),
                "failed" to (payload.array("failed") ?: JsonArray()),
            )
            else -> null
        }
    }

    /** The bridge method a message from the tab asks for, with its params; null for one the host answers itself. */
    fun request(message: JsonObject): Pair<String, JsonObject>? = when (message.text("type")) {
        "boardEdit" -> "boardEdit" to json("operations" to (message.array("operations") ?: JsonArray()))
        "boardPlan" -> "boardPlan" to json("tasks" to (message.array("tasks") ?: JsonArray()))
        "boardPlay" -> "boardPlay" to json("tasks" to (message.array("tasks") ?: JsonArray()))
        "boardStop" -> "boardStop" to JsonObject()
        else -> null
    }

    fun notice(level: String, text: String): JsonObject = json("type" to "boardNotice", "level" to level, "text" to text)

    fun snapshot(board: JsonObject, language: String): JsonObject =
        json("type" to "boardSnapshot", "board" to board, "moved" to JsonArray(), "language" to language)
}
