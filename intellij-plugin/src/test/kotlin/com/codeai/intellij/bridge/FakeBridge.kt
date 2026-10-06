package com.codeai.intellij.bridge

import com.codeai.intellij.util.json
import com.codeai.intellij.util.obj
import com.codeai.intellij.util.parseJsonObject
import com.codeai.intellij.util.text
import com.google.gson.JsonObject
import java.io.PrintStream
import kotlin.system.exitProcess

/**
 * A stand-in for `code-ai bridge`, run as a real child process so the client
 * is tested over real pipes: it answers a handful of made-up methods. Given a
 * number, it takes that many milliseconds to start, and says so when it has.
 */
object FakeBridge {
    @JvmStatic
    fun main(args: Array<String>) {
        val out = PrintStream(System.out, true, Charsets.UTF_8)
        fun reply(id: Any?, vararg fields: Pair<String, Any?>) = out.println(json("jsonrpc" to "2.0", "id" to id, *fields))
        fun event(type: String, payload: JsonObject) = out.println(
            json("jsonrpc" to "2.0", "method" to "event", "params" to json("event_type" to type, "payload" to payload))
        )
        System.err.println("fake bridge up")
        args.firstOrNull()?.toLongOrNull()?.let { slow ->
            Thread.sleep(slow)
            event("status.changed", json("state" to "READY"))
        }
        for (line in generateSequence { readlnOrNull() }) {
            val message = parseJsonObject(line) ?: continue
            val id = message.get("id")
            val params = message.obj("params") ?: JsonObject()
            when (message.text("method")) {
                "echo" -> reply(id, "result" to params)
                "fail" -> reply(id, "error" to json("code" to -32000, "message" to "nope"))
                "silent" -> Unit
                "emit" -> {
                    repeat(params.get("count").asInt) { event("tick", json("i" to it)) }
                    reply(id, "result" to json("ok" to true))
                }
                "noise" -> {
                    out.println("this line is not JSON")
                    reply(id, "result" to json("ok" to true))
                }
                "die" -> {
                    event("last.words", json("text" to "going"))
                    System.err.println("Traceback (most recent call last):\n  File \"x.py\", line 1\nRuntimeError: boom")
                    exitProcess(3)
                }
                "shutdown" -> exitProcess(0)
            }
        }
    }
}
