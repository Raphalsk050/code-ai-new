package com.codeai.intellij.bridge

import com.codeai.intellij.CodeAiPlugin
import com.codeai.intellij.HostThread
import com.codeai.intellij.Notify
import com.codeai.intellij.settings.CodeAiSettings
import com.codeai.intellij.util.parseJsonObject
import com.codeai.intellij.util.strings
import com.google.gson.JsonElement
import com.google.gson.JsonObject
import com.intellij.execution.configurations.GeneralCommandLine
import com.intellij.openapi.Disposable
import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.application.PathManager
import com.intellij.openapi.application.ReadAction
import com.intellij.openapi.components.Service
import com.intellij.openapi.components.service
import com.intellij.openapi.diagnostic.Logger
import com.intellij.openapi.fileEditor.FileEditorManager
import com.intellij.openapi.project.Project
import com.intellij.openapi.roots.ProjectRootManager
import com.intellij.openapi.util.SystemInfo
import com.intellij.openapi.vfs.LocalFileSystem
import com.intellij.util.concurrency.AppExecutorUtil
import java.io.IOException
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardOpenOption
import java.nio.file.attribute.PosixFilePermission
import java.util.concurrent.CompletableFuture
import java.util.concurrent.CopyOnWriteArraySet
import java.util.concurrent.ScheduledFuture

/** A view of the backend: whoever needs its events registers as one. */
internal interface BridgeSubscriber {
    /** A bus event arrived from the backend (or a synthetic one from here). */
    fun onEvent(event: BusEvent)

    /** The bridge was replaced; a view holding per-session state should drop it. */
    fun onRestart() {}

    /**
     * A backend process was just spawned: the first one, one the user asked
     * for, or one replacing a backend that died.
     */
    fun onStarted(client: BridgeClient, reason: StartReason) {}
}

internal enum class StartReason { FIRST, RESTART, CRASH }

/**
 * One backend process per project window, shared by every view that needs it.
 * The session owns the lifecycle and fans events out to whoever registered, so
 * a view is just a subscriber and the backend keeps running with the panel
 * closed. The counterpart of the VS Code extension's src/bridge-session.ts.
 *
 * Everything here runs on the project's [HostThread]; subscribers are called
 * on it too.
 */
@Service(Service.Level.PROJECT)
internal class BridgeSession(private val project: Project) : Disposable {
    private val host = project.service<HostThread>()
    private val subscribers = CopyOnWriteArraySet<BridgeSubscriber>()

    @Volatile
    private var bridge: BridgeClient? = null

    /** The last command warning shown, so a restart does not repeat it. */
    private var warnedAbout: String? = null

    /** The tail of the backend's stderr, to quote when it dies without explaining itself. */
    private var stderrTail = ArrayDeque<String>()

    /** When the backend died unasked, recently: what bounds the automatic restarts. */
    private var crashes = listOf<Long>()
    private var pendingRestart: ScheduledFuture<*>? = null

    /** Why the next backend is being started. */
    private var startReason = StartReason.FIRST

    /** Everything the backend wrote to stderr, for the user to read. */
    private val logFile: Path =
        PathManager.getLogDir().resolve("code-ai").resolve("backend-${project.locationHash}.log")

    /** The live client, or null while the backend is down. */
    val client: BridgeClient? get() = bridge

    fun subscribe(subscriber: BridgeSubscriber): Disposable {
        subscribers.add(subscriber)
        return Disposable { subscribers.remove(subscriber) }
    }

    /** Spawn the backend if it is not already up. Safe to call from every view, on any thread. */
    fun ensureStarted() = host.run { ensureStartedNow() }

    /**
     * The same, at once, for a caller already on the host thread: [client] is
     * there when this returns, so what the caller sends next is not sent to
     * nobody. The process is still starting; its pipe holds what is written.
     */
    fun ensureStartedNow() {
        if (bridge != null) return
        cancelPendingRestart()
        spawn()
    }

    fun send(method: String, params: JsonObject = JsonObject()) {
        bridge?.send(method, params)
    }

    fun request(
        method: String,
        params: JsonObject = JsonObject(),
        timeoutMs: Long = BridgeClient.DEFAULT_TIMEOUT_MS,
    ): CompletableFuture<JsonElement> =
        bridge?.request(method, params, timeoutMs)
            ?: CompletableFuture.failedFuture(BridgeException("The Code-AI backend is not running."))

    /**
     * Tear down the running bridge and spawn a fresh one. This re-reads the
     * on-disk config and the plugin's settings, so edits take effect without
     * restarting the IDE.
     */
    fun restart() = host.run {
        val previous = bridge
        bridge = null
        cancelPendingRestart()
        // Asked for by the user: whatever crashed before starts from a clean slate.
        crashes = emptyList()
        previous?.dispose()
        startReason = StartReason.RESTART
        broadcast(syntheticStatus("RECONNECTING"))
        broadcast(syntheticNotice("Restarting Code-AI to apply your settings…"))
        for (subscriber in subscribers) guarded { subscriber.onRestart() }
        spawn()
    }

    fun broadcast(event: BusEvent) {
        for (subscriber in subscribers) guarded { subscriber.onEvent(event) }
    }

    /** Open what the backend wrote to stderr in the editor. */
    fun showLog() = ApplicationManager.getApplication().invokeLater {
        if (project.isDisposed) return@invokeLater
        val file = LocalFileSystem.getInstance().refreshAndFindFileByNioFile(logFile)
        if (file == null) Notify.info(project, "The Code-AI backend has not written anything yet.")
        else FileEditorManager.getInstance(project).openFile(file, true)
    }

    override fun dispose() {
        pendingRestart?.cancel(false)
        bridge?.dispose()
        bridge = null
        subscribers.clear()
    }

    private fun cancelPendingRestart() {
        pendingRestart?.cancel(false)
        pendingRestart = null
    }

    private inline fun guarded(call: () -> Unit) {
        try {
            call()
        } catch (e: Exception) {
            LOG.warn("Code-AI bridge subscriber failed", e)
        }
    }

    /** Record a chunk of backend stderr, and keep the tail of it. */
    private fun note(chunk: String) {
        try {
            Files.createDirectories(logFile.parent)
            Files.writeString(logFile, chunk, StandardOpenOption.CREATE, StandardOpenOption.APPEND)
        } catch (_: IOException) {
            // A log that cannot be written must not take the session down.
        }
        for (line in chunk.split('\n')) {
            if (line.isNotBlank()) stderrTail.addLast(line.trimEnd('\r'))
        }
        while (stderrTail.size > STDERR_TAIL) stderrTail.removeFirst()
    }

    /**
     * The one line worth showing from a dead backend's stderr.
     *
     * A Python traceback puts the exception last, which is the line that names
     * the problem; the frames above it are noise in a balloon. The full text is
     * in the log file either way.
     */
    private fun lastWords(): String {
        val lines = stderrTail.filterNot { TRACEBACK_FRAME.containsMatchIn(it) }
        val last = (lines.ifEmpty { stderrTail }).lastOrNull() ?: ""
        return last.trim().take(300)
    }

    /**
     * Spawn the bridge child process. Reads the settings fresh on every call,
     * so a [restart] picks up edits. Failures are surfaced as synthetic events
     * inside the already-rendered views rather than left as a blank panel.
     */
    private fun spawn() {
        if (project.isDisposed) return
        val settings = CodeAiSettings.get()
        val extraArgs = settings.args
        val cwd = project.basePath
        val resolved = resolveCommand(settings.command, CodeAiPlugin.path()?.toString(), workspaceRoots(), Disk)
        if (resolved.source == CommandSource.BUNDLED) ensureExecutable(Path.of(resolved.command))
        // Once per setting per session. Every restart re-reads the settings, and
        // a warning that repeats on each one trains the reader to dismiss it.
        resolved.ignoredConfig?.let { ignored ->
            if (warnedAbout != ignored) {
                warnedAbout = ignored
                Notify.warning(project, ignored)
                broadcast(syntheticNotice(ignored))
            }
        }

        // The agent works on the folder the IDE has open, not on whatever
        // workspace the config file last recorded - which is also where the
        // conversation archive lives, so getting this wrong loses the history
        // too. An explicit --workspace in the arguments still wins.
        val workspaceArgs = if (cwd != null && "--workspace" !in extraArgs) listOf("--workspace", cwd) else emptyList()
        val command = listOf(resolved.command) + workspaceArgs + extraArgs + "bridge"

        resetLog(command)
        stderrTail = ArrayDeque()
        val client = try {
            BridgeClient.start(Launcher, command, cwd?.let(Path::of), Relay(), SCHEDULER)
        } catch (e: Exception) {
            val detail = "the backend did not start: ${e.message} - ${spawnFailureHint(resolved, SystemInfo.isWindows)}"
            note("$detail\n")
            Notify.error(project, detail.replaceFirstChar { it.uppercase() }, "Show log" to { showLog() })
            // Without this the panel sits on STARTING for ever and a dismissed
            // balloon is the only trace the failure ever left.
            broadcast(syntheticStatus("DISCONNECTED"))
            broadcast(syntheticError(detail))
            return
        }
        bridge = client
        val reason = startReason
        startReason = StartReason.FIRST
        for (subscriber in subscribers) guarded { subscriber.onStarted(client, reason) }
    }

    /** The backend's callbacks, moved from its reader threads onto the host thread. */
    private inner class Relay : BridgeClient.Listener {
        override fun onEvent(client: BridgeClient, event: BusEvent) = host.run {
            // A backend that was replaced may still be saying its goodbyes.
            if (bridge === client) broadcast(event)
        }

        override fun onStderr(client: BridgeClient, chunk: String) = host.run { note(chunk) }

        override fun onExit(client: BridgeClient, code: Int) = host.run { exited(client, code) }
    }

    private fun exited(client: BridgeClient, code: Int) {
        if (bridge !== client) return // a restart already replaced this bridge
        bridge = null
        // Leave the busy state so the "working" heartbeat stops, then show why.
        broadcast(syntheticStatus("DISCONNECTED"))
        // A backend that died on startup already said why on stderr. Quoting it
        // here is the difference between a bug report and a guess.
        val said = lastWords()
        broadcast(syntheticError(if (said.isEmpty()) "bridge exited ($code)" else "bridge exited ($code): $said"))
        // Left down, every message after this would go nowhere: start it again,
        // unless it keeps dying on its own.
        val decision = afterCrash(crashes, System.currentTimeMillis())
        crashes = decision.crashes
        if (decision.restart) {
            broadcast(syntheticNotice("The Code-AI backend stopped; starting it again…"))
            pendingRestart = host.later(AUTO_RESTART_DELAY_MS) {
                pendingRestart = null
                if (bridge != null) return@later
                for (subscriber in subscribers) guarded { subscriber.onRestart() }
                startReason = StartReason.CRASH
                spawn()
            }
            return
        }
        Notify.error(
            project,
            "Code-AI backend stopped" + if (said.isEmpty()) " (exit $code)" else ": $said",
            "Restart" to { restart() },
            "Show log" to { showLog() },
        )
    }

    private fun workspaceRoots(): List<String> {
        val roots = LinkedHashSet<String>()
        // Run from the repository (`./gradlew runIde`), the repository's own
        // virtualenv holds the backend being worked on: it comes before the
        // project's, as the extension run from source finds the one beside it.
        System.getProperty(DEV_ROOT)?.takeIf { it.isNotBlank() }?.let(roots::add)
        project.basePath?.let(roots::add)
        try {
            ReadAction.compute<Unit, RuntimeException> {
                if (!project.isDisposed) {
                    ProjectRootManager.getInstance(project).contentRoots.filter { it.isInLocalFileSystem }
                        .mapTo(roots) { it.path }
                }
            }
        } catch (e: RuntimeException) {
            LOG.debug("Could not read the content roots", e)
        }
        return roots.toList()
    }

    /** Start each run's log afresh, headed by what was launched, and keep it from growing for ever. */
    private fun resetLog(command: List<String>) {
        try {
            Files.createDirectories(logFile.parent)
            val header = "--- ${java.time.LocalDateTime.now().withNano(0)} ${command.joinToString(" ")}\n"
            if (Files.exists(logFile) && Files.size(logFile) > LOG_LIMIT_BYTES) Files.writeString(logFile, header)
            else Files.writeString(logFile, header, StandardOpenOption.CREATE, StandardOpenOption.APPEND)
        } catch (_: IOException) {
            // As in note().
        }
    }

    private object Disk : ResolveHost {
        override val windows: Boolean get() = SystemInfo.isWindows
        override fun exists(file: String): Boolean = try {
            Files.exists(Path.of(file))
        } catch (_: RuntimeException) {
            false
        }
    }

    /**
     * The IDE's own way of starting a process: it hands the child the user's
     * shell environment, which a GUI application on macOS does not have by
     * itself, so `code-ai` on PATH and the tools the agent runs are found.
     */
    private object Launcher : ProcessLauncher {
        override fun launch(command: List<String>, workDir: Path?): Process =
            GeneralCommandLine(command)
                .withWorkingDirectory(workDir)
                .withCharset(Charsets.UTF_8)
                .withParentEnvironmentType(GeneralCommandLine.ParentEnvironmentType.CONSOLE)
                .createProcess()
    }

    companion object {
        private val LOG = Logger.getInstance(BridgeSession::class.java)

        /** How many stderr lines to keep, so a traceback survives whole. */
        private const val STDERR_TAIL = 40

        /** Pause before starting a backend that died, so a crash cannot spin the CPU. */
        private const val AUTO_RESTART_DELAY_MS = 1_000L
        private const val LOG_LIMIT_BYTES = 2L * 1024 * 1024
        private val TRACEBACK_FRAME = Regex("""^\s*(File "|\s+\S)""")

        /** A folder whose `.venv` is searched for the backend first: the build sets it for the sandbox IDE. */
        private const val DEV_ROOT = "code.ai.dev.root"
        private val SCHEDULER = AppExecutorUtil.getAppScheduledExecutorService()

        fun of(project: Project): BridgeSession = project.service()

        /**
         * Make a bundled binary executable. A plugin is installed from a zip,
         * and the bit does not always survive the round trip (it never does
         * when the zip was produced on Windows), so the first launch on Linux
         * or macOS would fail with "permission denied". The macOS build is a
         * folder, and its manifest names every file that needs the bit.
         */
        internal fun ensureExecutable(file: Path) {
            if (SystemInfo.isWindows) return
            val files = mutableListOf(file)
            try {
                val manifest = parseJsonObject(Files.readString(file.resolveSibling("code-ai-bundle.json")))
                manifest?.strings("executables")?.mapTo(files) { file.resolveSibling(it) }
            } catch (_: IOException) {
                // No manifest: the one-file build.
            }
            for (target in files) {
                try {
                    val permissions = Files.getPosixFilePermissions(target)
                    if (PosixFilePermission.OWNER_EXECUTE !in permissions) {
                        Files.setPosixFilePermissions(
                            target,
                            permissions + setOf(
                                PosixFilePermission.OWNER_READ,
                                PosixFilePermission.OWNER_WRITE,
                                PosixFilePermission.OWNER_EXECUTE,
                                PosixFilePermission.GROUP_READ,
                                PosixFilePermission.GROUP_EXECUTE,
                                PosixFilePermission.OTHERS_READ,
                                PosixFilePermission.OTHERS_EXECUTE,
                            ),
                        )
                    }
                } catch (_: IOException) {
                    // Read-only install or a vanished file: let the spawn report the real error.
                } catch (_: UnsupportedOperationException) {
                    // Not a POSIX file system.
                }
            }
        }
    }
}
