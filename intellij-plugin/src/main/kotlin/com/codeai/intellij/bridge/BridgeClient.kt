package com.codeai.intellij.bridge

import com.codeai.intellij.util.json
import com.codeai.intellij.util.obj
import com.codeai.intellij.util.parseJsonObject
import com.codeai.intellij.util.present
import com.codeai.intellij.util.string
import com.codeai.intellij.util.text
import com.google.gson.JsonElement
import com.google.gson.JsonNull
import com.google.gson.JsonObject
import java.io.IOException
import java.io.Writer
import java.nio.file.Path
import java.util.concurrent.CompletableFuture
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.ScheduledExecutorService
import java.util.concurrent.ScheduledFuture
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import kotlin.concurrent.thread
import kotlin.math.roundToLong

/** A request the bridge refused, timed out on, or died under. */
internal class BridgeException(message: String) : Exception(message)

/** Starts the backend process: the IDE's own launcher in the plugin, a plain one in tests. */
internal fun interface ProcessLauncher {
    @Throws(Exception::class)
    fun launch(command: List<String>, workDir: Path?): Process
}

/**
 * Thin JSON-RPC 2.0 client over a child process' stdio: one JSON object per
 * line in each direction. The counterpart of the VS Code extension's
 * src/bridge-client.ts.
 *
 * The listener is called from this client's own reader threads, events in the
 * order the bridge wrote them and the exit after the last of them.
 */
internal class BridgeClient private constructor(
    private val process: Process,
    private val listener: Listener,
    private val scheduler: ScheduledExecutorService,
) {
    interface Listener {
        /** A bus event forwarded by the bridge. */
        fun onEvent(client: BridgeClient, event: BusEvent)

        /** Diagnostics the bridge wrote to stderr. */
        fun onStderr(client: BridgeClient, chunk: String)

        /** The bridge process is gone. */
        fun onExit(client: BridgeClient, code: Int)
    }

    private class Pending(val future: CompletableFuture<JsonElement>, val timeout: ScheduledFuture<*>)

    private val stdin: Writer = process.outputStream.bufferedWriter(Charsets.UTF_8)
    private val nextId = AtomicInteger(1)
    private val pending = ConcurrentHashMap<Int, Pending>()

    /** Why the bridge is gone, once it is: every later request is refused with it. */
    @Volatile
    private var dead: String? = null

    private fun start() {
        val stdout = thread(name = "Code-AI bridge stdout", isDaemon = true) {
            try {
                process.inputStream.bufferedReader(Charsets.UTF_8).forEachLine(::onLine)
            } catch (_: IOException) {
                // The pipe closed under the read: the exit below reports it.
            }
        }
        thread(name = "Code-AI bridge stderr", isDaemon = true) {
            try {
                val reader = process.errorStream.reader(Charsets.UTF_8)
                val buffer = CharArray(4096)
                while (true) {
                    val count = reader.read(buffer)
                    if (count < 0) break
                    if (count > 0) listener.onStderr(this, String(buffer, 0, count))
                }
            } catch (_: IOException) {
                // As above.
            }
        }
        thread(name = "Code-AI bridge exit", isDaemon = true) {
            val code = try {
                process.waitFor()
            } catch (_: InterruptedException) {
                -1
            }
            // What the bridge wrote before dying is still in the pipe: let it
            // through first, so the exit is the last thing anyone hears.
            stdout.join(STDOUT_DRAIN_MS)
            fail("bridge process exited (code $code)")
            listener.onExit(this, code)
        }
    }

    /** Mark the bridge dead and reject every in-flight request right away. */
    private fun fail(reason: String) {
        dead = reason
        for (id in pending.keys.toList()) {
            val waiting = pending.remove(id) ?: continue
            waiting.timeout.cancel(false)
            waiting.future.completeExceptionally(BridgeException(reason))
        }
    }

    private fun onLine(raw: String) {
        val line = raw.trim()
        if (line.isEmpty()) return
        val message = parseJsonObject(line) ?: return // ignore non-JSON noise on stdout
        if (message.string("method") == "event") {
            val params = message.obj("params") ?: return
            listener.onEvent(this, BusEvent(params))
            return
        }
        val id = message.present("id")?.takeIf { it.isJsonPrimitive && it.asJsonPrimitive.isNumber }?.asInt ?: return
        val waiting = pending.remove(id) ?: return
        waiting.timeout.cancel(false)
        val error = message.present("error")
        if (error != null) {
            val reason = (error as? JsonObject)?.text("message")?.ifEmpty { null } ?: "bridge error"
            waiting.future.completeExceptionally(BridgeException(reason))
        } else {
            waiting.future.complete(message.get("result") ?: JsonNull.INSTANCE)
        }
    }

    /** Send a request whose answer nobody waits for. */
    fun send(method: String, params: JsonObject = JsonObject()) {
        write(json("jsonrpc" to "2.0", "id" to nextId.getAndIncrement(), "method" to method, "params" to params))
    }

    /** Send a request; the future completes with its `result`, or fails on error or timeout. */
    fun request(
        method: String,
        params: JsonObject = JsonObject(),
        timeoutMs: Long = DEFAULT_TIMEOUT_MS,
    ): CompletableFuture<JsonElement> {
        val future = CompletableFuture<JsonElement>()
        dead?.let { reason ->
            future.completeExceptionally(BridgeException(reason))
            return future
        }
        val id = nextId.getAndIncrement()
        val timeout = scheduler.schedule({
            if (pending.remove(id) != null) {
                val seconds = (timeoutMs / 1000.0).roundToLong()
                future.completeExceptionally(
                    BridgeException("Bridge request \"$method\" timed out after ${seconds}s")
                )
            }
        }, timeoutMs, TimeUnit.MILLISECONDS)
        pending[id] = Pending(future, timeout)
        // It died between the check above and the entry going in: nobody else
        // will ever settle this one.
        dead?.let { reason ->
            if (pending.remove(id) != null) {
                timeout.cancel(false)
                future.completeExceptionally(BridgeException(reason))
            }
            return future
        }
        write(json("jsonrpc" to "2.0", "id" to id, "method" to method, "params" to params))
        return future
    }

    private fun write(message: JsonObject) {
        try {
            synchronized(stdin) {
                stdin.write(message.toString())
                stdin.write("\n")
                stdin.flush()
            }
        } catch (_: IOException) {
            // stdin is closed: the process is going, and its exit says so.
        }
    }

    /** Ask the bridge to shut down, and end the process if it has not by then. */
    fun dispose() {
        write(json("jsonrpc" to "2.0", "method" to "shutdown", "params" to JsonObject()))
        scheduler.schedule({ process.destroy() }, SHUTDOWN_GRACE_MS, TimeUnit.MILLISECONDS)
    }

    companion object {
        const val DEFAULT_TIMEOUT_MS = 20_000L
        private const val SHUTDOWN_GRACE_MS = 500L
        private const val STDOUT_DRAIN_MS = 2_000L

        /** Start the bridge. Throws when the process cannot be started at all. */
        @Throws(Exception::class)
        fun start(
            launcher: ProcessLauncher,
            command: List<String>,
            workDir: Path?,
            listener: Listener,
            scheduler: ScheduledExecutorService,
        ): BridgeClient = BridgeClient(launcher.launch(command, workDir), listener, scheduler).also { it.start() }
    }
}
