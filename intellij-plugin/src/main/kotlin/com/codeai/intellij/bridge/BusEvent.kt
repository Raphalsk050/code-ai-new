package com.codeai.intellij.bridge

import com.codeai.intellij.util.json
import com.codeai.intellij.util.obj
import com.codeai.intellij.util.text
import com.google.gson.JsonObject
import java.time.Instant

/**
 * One event of the backend's bus, as the bridge forwards it. Event names are
 * the versioned contract shared with the Python core and the panel; the
 * envelope is kept whole so it reaches the panel exactly as it arrived.
 */
internal class BusEvent(val json: JsonObject) {
    val type: String get() = json.text("event_type")
    val source: String get() = json.text("source")

    /** Read-only: the same object goes on to every subscriber. */
    val payload: JsonObject get() = json.obj("payload") ?: NO_PAYLOAD

    private companion object {
        val NO_PAYLOAD = JsonObject()
    }
}

/** The source this plugin signs its own events with, as the VS Code extension does. */
internal const val HOST_SOURCE = "extension"

internal fun syntheticEvent(type: String, payload: JsonObject): BusEvent = BusEvent(
    json(
        "event_id" to "synthetic-${System.currentTimeMillis()}",
        "event_type" to type,
        "event_version" to 1,
        "session_id" to "host",
        "sequence" to 0,
        "timestamp" to Instant.now().toString(),
        "source" to HOST_SOURCE,
        "payload" to payload,
    )
)

internal fun syntheticStatus(state: String): BusEvent = syntheticEvent("status.changed", json("state" to state))

internal fun syntheticNotice(message: String): BusEvent = syntheticEvent("warning", json("message" to message))

internal fun syntheticError(message: String): BusEvent = syntheticEvent("error", json("message" to message))
