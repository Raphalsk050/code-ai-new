package com.codeai.intellij.board

import com.codeai.intellij.HostThread
import com.codeai.intellij.bridge.BridgeClient
import com.codeai.intellij.bridge.BridgeSession
import com.codeai.intellij.bridge.BridgeSubscriber
import com.codeai.intellij.bridge.BusEvent
import com.codeai.intellij.bridge.StartReason
import com.codeai.intellij.util.array
import com.codeai.intellij.util.json
import com.codeai.intellij.util.obj
import com.codeai.intellij.util.present
import com.codeai.intellij.util.text
import com.codeai.intellij.web.WebviewPanel
import com.codeai.intellij.web.WebviewStateStore
import com.google.gson.JsonElement
import com.google.gson.JsonObject
import com.intellij.openapi.Disposable
import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.components.Service
import com.intellij.openapi.components.service
import com.intellij.openapi.fileEditor.FileEditorManager
import com.intellij.openapi.project.Project
import com.intellij.openapi.util.Disposer
import java.nio.file.Path
import java.util.concurrent.CompletableFuture

/**
 * The task board's host: an editor tab of its own, as in VS Code
 * (src/board-panel.ts), over the one board the backend keeps for the project.
 *
 * The tab holds nothing but a copy. Its edits go to the backend as operations
 * and every change comes back as `board.changed`, so the tab, the chat's agent
 * and the terminal never disagree. One per project; it listens with the tab
 * closed, because planned work asking for the user's Play opens it.
 */
@Service(Service.Level.PROJECT)
internal class BoardHost(private val project: Project) : Disposable {
    private val host = project.service<HostThread>()
    private val bridge = BridgeSession.of(project)
    private val saved = WebviewStateStore(project, "board")

    /** The tab's file: one per project, so opening it again brings the same tab forward. */
    val file: BoardFile by lazy { BoardFile() }

    @Volatile
    private var panel: WebviewPanel? = null

    /** The user's language, as the backend reports it: the tab follows it. */
    private var language = ""

    init {
        Disposer.register(this, bridge.subscribe(object : BridgeSubscriber {
            override fun onEvent(event: BusEvent) = onBridgeEvent(event)

            override fun onStarted(client: BridgeClient, reason: StartReason) {
                // A backend that came back may hold a board the tab has not seen.
                if (reason != StartReason.FIRST) pushBoard()
            }
        }))
    }

    /** The board, in the IDE's embedded browser. The panel belongs to `parent`, the editor. */
    fun createPanel(parent: Disposable): WebviewPanel {
        val created = WebviewPanel(parent, "board.js", workspace = project.basePath?.let { Path.of(it) }, client = object : WebviewPanel.Client {
            override fun savedState(): String? = saved.load()

            override fun saveState(json: String) = saved.save(json)

            override fun ready() {}

            override fun message(message: JsonObject) = host.run { onPanelMessage(message) }
        })
        panel = created
        Disposer.register(created) { if (panel === created) panel = null }
        bridge.ensureStarted()
        return created
    }

    /** Open the tab, or bring it forward. */
    fun open(focus: Boolean) = ApplicationManager.getApplication().invokeLater {
        if (!project.isDisposed) FileEditorManager.getInstance(project).openFile(file, focus)
    }

    override fun dispose() {}

    // -- backend -> tab -------------------------------------------------------

    private fun onBridgeEvent(event: BusEvent) {
        // The agent or the planners are done: the board comes forward for the
        // user to check it and press Play, without taking the keyboard.
        if (event.type == "board.review.requested") open(focus = false)
        BoardMessages.forPanel(event)?.let(::post)
    }

    private fun post(message: JsonObject) {
        panel?.post(message)
    }

    private fun pushBoard() {
        if (panel == null) return
        bridge.ensureStartedNow()
        val readBoard = {
            bridge.request("boardGet", timeoutMs = REQUEST_TIMEOUT_MS).then(
                { result ->
                    val board = result.asObject().obj("board")
                    if (board == null) post(BoardMessages.notice("error", "The backend did not send the board."))
                    else post(BoardMessages.snapshot(board, language))
                },
                { error -> post(BoardMessages.notice("error", "The board could not be read: $error")) },
            )
        }
        if (language.isNotEmpty()) {
            readBoard()
            return
        }
        bridge.request("getSettings", timeoutMs = REQUEST_TIMEOUT_MS).then(
            { settings ->
                language = settings.asObject().text("language")
                readBoard()
            },
            { readBoard() },
        )
    }

    // -- tab -> backend -------------------------------------------------------

    private fun onPanelMessage(message: JsonObject) {
        if (message.text("type") == "boardReady") {
            pushBoard()
            return
        }
        val (method, params) = BoardMessages.request(message) ?: return
        bridge.ensureStartedNow()
        bridge.request(method, params, REQUEST_TIMEOUT_MS).then(
            { answer ->
                val result = answer.asObject()
                val refused = result.present("ok")?.let { it.isJsonPrimitive && !it.asBoolean } == true
                if (refused) {
                    post(BoardMessages.notice("warn", result.text("message", "The board refused that.")))
                    // A refused edit leaves the board as it was: put the tab back on it.
                    result.obj("board")?.let { post(BoardMessages.snapshot(it, language)) }
                    return@then
                }
                result.array("created")?.takeIf { it.size() > 0 }?.let { post(json("type" to "boardCreated", "ids" to it)) }
                val text = result.text("message")
                if (text.isNotBlank() && method != "boardEdit") post(BoardMessages.notice("info", text))
            },
            { error -> post(BoardMessages.notice("error", error)) },
        )
    }

    // -- helpers ----------------------------------------------------------------

    /** Continue on the host thread with a request's result, or with why it failed. */
    private fun CompletableFuture<JsonElement>.then(onResult: (JsonElement) -> Unit, onError: (String) -> Unit) {
        whenComplete { result, failure ->
            host.run {
                if (failure != null) onError((failure.cause ?: failure).message ?: failure.toString())
                else onResult(result)
            }
        }
    }

    private fun JsonElement.asObject(): JsonObject = if (isJsonObject) asJsonObject else JsonObject()

    private companion object {
        const val REQUEST_TIMEOUT_MS = 30_000L
    }
}
