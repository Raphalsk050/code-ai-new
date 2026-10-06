package com.codeai.intellij.status

import com.codeai.intellij.bridge.BusEvent
import com.codeai.intellij.util.json
import com.google.gson.JsonObject
import kotlin.test.Test
import kotlin.test.assertEquals

class AgentStatusTest {
    private fun ev(type: String, payload: JsonObject = JsonObject()) = BusEvent(
        json(
            "event_id" to type, "event_type" to type, "event_version" to 1, "session_id" to "s",
            "sequence" to 0, "timestamp" to "", "source" to "core", "payload" to payload,
        )
    )

    @Test
    fun `the status bar shows the context left and what the agent is doing`() {
        var status = AgentStatus()
        assertEquals(Activity.OFFLINE, statusView(status).activity)
        assertEquals("Code-AI", statusView(status).text)

        for (event in listOf(
            ev("status.changed", json("state" to "READY")),
            ev(
                "usage.updated",
                json("active_context_tokens" to 61000, "context_budget" to 128000, "context_threshold" to 0.8),
            ),
        )) {
            status = reduceAgentStatus(status, event)
        }
        assertEquals(
            StatusView(Activity.READY, "Code-AI 48%", "Ready · Context 61k of 128k", warn = false),
            statusView(status),
        )

        status = reduceAgentStatus(status, ev("status.changed", json("state" to "EXECUTING_TOOL")))
        status = reduceAgentStatus(status, ev("tool.call.started", json("name" to "web_search")))
        assertEquals("Running web_search · Context 61k of 128k", statusView(status).tooltip)

        status = reduceAgentStatus(status, ev("tool.approval.requested"))
        assertEquals(Activity.WAITING, statusView(status).activity)

        status = reduceAgentStatus(status, ev("usage.updated", json("active_context_tokens" to 110000)))
        assertEquals(true, statusView(status).warn)
    }

    @Test
    fun `a state that ends the work forgets the tool that was running`() {
        var status = AgentStatus(state = "EXECUTING_TOOL", tool = "run_command")
        status = reduceAgentStatus(status, ev("status.changed", json("state" to "CALLING_MODEL")))
        assertEquals("run_command", status.tool)
        status = reduceAgentStatus(status, ev("status.changed", json("state" to "READY")))
        assertEquals("", status.tool)
        assertEquals(Activity.READY, statusView(status).activity)
    }
}
