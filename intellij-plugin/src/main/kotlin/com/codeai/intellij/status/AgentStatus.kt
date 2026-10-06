package com.codeai.intellij.status

import com.codeai.intellij.bridge.BusEvent
import com.codeai.intellij.util.number
import com.codeai.intellij.util.present
import com.codeai.intellij.util.text
import kotlin.math.min
import kotlin.math.roundToLong

// What the status bar says about the agent when the panel is closed: the
// context it has left and whether it is working or waiting. No IDE import. A
// port of the VS Code extension's src/agent-status.ts.

internal data class AgentStatus(
    val state: String = "STARTING",
    val used: Double? = null,
    val budget: Double? = null,
    val threshold: Double = 0.82,
    val tool: String = "",
    val waiting: Boolean = false,
)

private val BUSY = setOf("CALLING_MODEL", "EXECUTING_TOOL", "COMPRESSING_CONTEXT", "CANCELLING")
private val OFFLINE = setOf("STARTING", "RECONNECTING", "DISCONNECTED")

internal fun reduceAgentStatus(status: AgentStatus, event: BusEvent): AgentStatus {
    val p = event.payload
    return when (event.type) {
        "status.changed" -> {
            val state = p.text("state", status.state)
            status.copy(state = state, tool = if (p.present("state") != null && state in BUSY) status.tool else "")
        }
        "usage.updated" -> status.copy(
            used = p.number("active_context_tokens") ?: status.used,
            budget = p.number("context_budget") ?: status.budget,
            threshold = p.number("context_threshold") ?: status.threshold,
        )
        "tool.call.started" -> status.copy(tool = p.text("name"))
        "tool.call.completed", "tool.call.failed" -> status.copy(tool = "")
        "tool.approval.requested" -> status.copy(waiting = true)
        "tool.approval.resolved", "user.message" -> status.copy(waiting = false)
        "conversation.reset" -> status.copy(used = null, waiting = false, tool = "")
        else -> status
    }
}

/** What the agent is up to, for the widget to pick an icon by. */
internal enum class Activity { READY, OFFLINE, WAITING, BUSY }

internal data class StatusView(
    val activity: Activity,
    val text: String,
    val tooltip: String,
    /** Past the compaction threshold: the widget draws attention to itself. */
    val warn: Boolean,
)

private fun tokens(n: Double): String =
    if (n < 1000) n.roundToLong().toString() else "${(n / 1000).roundToLong()}k"

internal fun statusView(status: AgentStatus): StatusView {
    val used = status.used
    val budget = status.budget?.takeUnless { it == 0.0 }
    val ratio = if (used != null && budget != null) used / budget else null
    val percent = if (ratio == null) "" else " ${min(999, Math.round(ratio * 100))}%"
    val context =
        if (ratio == null || used == null || budget == null) "Context not measured yet"
        else "Context ${tokens(used)} of ${tokens(budget)}"
    var activity = Activity.READY
    var doing = "Ready"
    if (status.state in OFFLINE) {
        activity = Activity.OFFLINE
        doing = if (status.state == "DISCONNECTED") "Offline" else "Connecting"
    } else if (status.waiting) {
        activity = Activity.WAITING
        doing = "Waiting for your approval"
    } else if (status.state in BUSY) {
        activity = Activity.BUSY
        doing = when {
            status.tool.isNotEmpty() -> "Running ${status.tool}"
            status.state == "COMPRESSING_CONTEXT" -> "Compacting context"
            else -> "Working"
        }
    }
    return StatusView(
        activity = activity,
        text = "Code-AI$percent",
        tooltip = "$doing · $context",
        warn = ratio != null && ratio >= status.threshold,
    )
}
