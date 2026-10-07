package com.codeai.intellij.bridge

import com.codeai.intellij.util.json
import com.codeai.intellij.util.text
import java.nio.file.Files
import java.nio.file.Path
import java.util.Collections
import java.util.concurrent.CompletableFuture
import java.util.concurrent.ExecutionException
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import kotlin.test.AfterTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertIs
import kotlin.test.assertTrue

class BridgeClientTest {
    private val scheduler = Executors.newSingleThreadScheduledExecutor()
    private val heard = Collections.synchronizedList(mutableListOf<String>())
    private val stderr = StringBuffer()
    private val exit = CompletableFuture<Int>()
    private var client: BridgeClient? = null
    private var process: Process? = null

    /**
     * The fake bridge, on this JVM's own java and class path. [slowMs] is how
     * long it takes to start, and [allowanceMs] how long the client lets it.
     */
    private fun start(slowMs: Long? = null, allowanceMs: Long = 0): BridgeClient {
        // An argument file: the test class path is longer than a Windows command line may be.
        val argFile = Files.createTempFile("code-ai-fake-bridge", ".args")
        argFile.toFile().deleteOnExit()
        val classPath = System.getProperty("java.class.path").replace("\\", "\\\\")
        Files.writeString(argFile, "-cp \"$classPath\"\n")
        val java = Path.of(System.getProperty("java.home"), "bin", "java").toString()
        val launcher = ProcessLauncher { command, _ -> ProcessBuilder(command).start().also { process = it } }
        val listener = object : BridgeClient.Listener {
            override fun onEvent(client: BridgeClient, event: BusEvent) {
                heard += "event:${event.type}:${event.payload}"
            }

            override fun onStderr(client: BridgeClient, chunk: String) {
                stderr.append(chunk)
            }

            override fun onExit(client: BridgeClient, code: Int) {
                heard += "exit:$code"
                exit.complete(code)
            }
        }
        val command = listOf(java, "@$argFile", FakeBridge::class.java.name) + listOfNotNull(slowMs?.toString())
        return BridgeClient.start(launcher, command, null, listener, scheduler, allowanceMs).also { client = it }
    }

    @AfterTest
    fun stop() {
        client?.dispose()
        // One that is still starting does not hear that: end it, and wait for the client to notice.
        process?.let { running ->
            if (!running.waitFor(2, TimeUnit.SECONDS)) running.destroyForcibly()
            exit.get(30, TimeUnit.SECONDS)
        }
        scheduler.shutdownNow()
    }

    private fun <T> CompletableFuture<T>.await(): T = get(30, TimeUnit.SECONDS)

    private fun CompletableFuture<*>.failure(): Throwable =
        assertFailsWith<ExecutionException> { get(30, TimeUnit.SECONDS) }.cause!!

    @Test
    fun `a request resolves with its own result, whatever order the answers come in`() {
        val bridge = start()
        val first = bridge.request("echo", json("text" to "olá, mundo ✓", "n" to 1))
        val second = bridge.request("echo", json("n" to 2))
        assertEquals("olá, mundo ✓", first.await().asJsonObject.text("text"))
        assertEquals("2", second.await().asJsonObject.text("n"))
    }

    @Test
    fun `an error reply rejects with the bridge's message`() {
        val failure = start().request("fail").failure()
        assertIs<BridgeException>(failure)
        assertEquals("nope", failure.message)
    }

    @Test
    fun `a request the bridge never answers times out, naming the method`() {
        val failure = start().request("silent", timeoutMs = 300).failure()
        assertEquals("Bridge request \"silent\" timed out after 0s", failure.message)
    }

    @Test
    fun `what is asked while the bridge starts waits for it, however short its own time`() {
        val bridge = start(slowMs = 1_500, allowanceMs = 30_000)
        val asked = bridge.request("echo", json("n" to 1), timeoutMs = 200)
        assertEquals("1", asked.await().asJsonObject.text("n"))
        assertTrue(bridge.started)
    }

    @Test
    fun `once the bridge has spoken, what was asked while it started has its own time again`() {
        val bridge = start(slowMs = 500, allowanceMs = 60_000)
        val failure = bridge.request("silent", timeoutMs = 300).failure()
        assertEquals("Bridge request \"silent\" timed out after 0s", failure.message)
    }

    @Test
    fun `a bridge that never starts is given up on when its allowance is spent`() {
        val bridge = start(slowMs = 60_000, allowanceMs = 700)
        val failure = bridge.request("echo", timeoutMs = 300).failure()
        assertEquals("Bridge request \"echo\" timed out after 1s", failure.message)
    }

    @Test
    fun `events arrive in order, and noise on stdout is ignored`() {
        val bridge = start()
        bridge.request("emit", json("count" to 50)).await()
        bridge.request("noise").await()
        assertEquals((0 until 50).map { "event:tick:{\"i\":$it}" }, heard.toList())
    }

    @Test
    fun `a bridge that dies fails what was in flight, after its last event`() {
        val bridge = start()
        val waiting = bridge.request("silent", timeoutMs = 60_000)
        bridge.send("die")
        assertEquals(3, exit.await())
        assertEquals("bridge process exited (code 3)", waiting.failure().message)
        assertEquals(listOf("event:last.words:{\"text\":\"going\"}", "exit:3"), heard.toList())
        assertTrue("RuntimeError: boom" in stderr.toString())
        // And nothing sent afterwards waits for an answer that cannot come.
        assertEquals("bridge process exited (code 3)", bridge.request("echo").failure().message)
    }

    @Test
    fun `disposing asks the bridge to shut down`() {
        val bridge = start()
        // Up first: one that is still starting is ended before it reads the request.
        bridge.request("echo").await()
        bridge.dispose()
        assertEquals(0, exit.await())
    }
}
