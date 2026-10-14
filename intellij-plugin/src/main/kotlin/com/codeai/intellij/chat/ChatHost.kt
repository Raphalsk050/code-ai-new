package com.codeai.intellij.chat

import com.codeai.intellij.HostThread
import com.codeai.intellij.InlineHints
import com.codeai.intellij.Notify
import com.codeai.intellij.bridge.BridgeClient
import com.codeai.intellij.bridge.BridgeSession
import com.codeai.intellij.bridge.BridgeSubscriber
import com.codeai.intellij.bridge.BusEvent
import com.codeai.intellij.bridge.HOST_SOURCE
import com.codeai.intellij.bridge.StartReason
import com.codeai.intellij.bridge.syntheticError
import com.codeai.intellij.bridge.syntheticEvent
import com.codeai.intellij.bridge.syntheticNotice
import com.codeai.intellij.changes.ChangeSummary
import com.codeai.intellij.changes.SessionChanges
import com.codeai.intellij.diff.EditDiffs
import com.codeai.intellij.editor.ActiveSelection
import com.codeai.intellij.editor.EditorContexts
import com.codeai.intellij.editor.Workspace
import com.codeai.intellij.editor.formatEditorContext
import com.codeai.intellij.settings.CodeAiSettings
import com.codeai.intellij.status.AgentStatusService
import com.codeai.intellij.util.array
import com.codeai.intellij.util.json
import com.codeai.intellij.util.number
import com.codeai.intellij.util.obj
import com.codeai.intellij.util.present
import com.codeai.intellij.util.string
import com.codeai.intellij.util.strings
import com.codeai.intellij.util.text
import com.codeai.intellij.util.toJson
import com.codeai.intellij.util.truthy
import com.codeai.intellij.web.WebviewPanel
import com.codeai.intellij.web.WebviewStateStore
import com.google.gson.JsonArray
import com.google.gson.JsonElement
import com.google.gson.JsonObject
import com.intellij.ide.BrowserUtil
import com.intellij.openapi.Disposable
import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.components.Service
import com.intellij.openapi.components.service
import com.intellij.openapi.diagnostic.Logger
import com.intellij.openapi.fileEditor.FileDocumentManager
import com.intellij.openapi.fileEditor.FileEditorManager
import com.intellij.openapi.project.Project
import com.intellij.openapi.util.Disposer
import com.intellij.openapi.util.io.FileUtil
import com.intellij.openapi.vfs.LocalFileSystem
import com.intellij.openapi.vfs.VirtualFileManager
import com.intellij.util.concurrency.AppExecutorUtil
import java.io.File
import java.io.IOException
import java.util.concurrent.CompletableFuture
import java.util.concurrent.ScheduledFuture

/**
 * The chat panel's host: what stands between the panel and the backend, and
 * what the panel asks the IDE for. The counterpart of the VS Code extension's
 * `ChatViewProvider` (src/extension.ts), message for message: the panel is the
 * same bundle and the wire contract is src/protocol.ts.
 *
 * One per project. It outlives the panel, so the backend keeps running with
 * the tool window closed and the panel re-attaches when it is opened again.
 * Everything here runs on the project's [HostThread].
 */
@Service(Service.Level.PROJECT)
internal class ChatHost(private val project: Project) : Disposable {
    private val host = project.service<HostThread>()
    private val bridge = BridgeSession.of(project)
    private val diffs = project.service<EditDiffs>()
    private val changes = project.service<SessionChanges>()
    private val status = project.service<AgentStatusService>()
    private val inline = project.service<InlineHints>()
    private val saved = WebviewStateStore(project, "chat")

    @Volatile
    private var panel: WebviewPanel? = null

    /** The thread on screen, so a backend that restarts can pick it up again. */
    private var activeConversation: String? = null

    /** Messages sent while the backend was down, delivered once it is back. */
    private val pendingSubmits = ArrayList<JsonObject>()

    /**
     * Messages whose turn has not ended. A backend that dies takes its turn
     * with it, so these go out again once the replacement is up.
     */
    private var inFlight = ArrayList<JsonObject>()

    /** Times the running turn was sent again since a model response last landed. */
    private var resends = 0

    /** The backend's last word on its own state, for a panel that opens later. */
    private var lastStatus: BusEvent? = null

    /** Who is signed in on the releases host: the chat's user avatar. */
    private var profile: JsonObject? = null

    /** A picture sign-in window is open; a second click waits for it. */
    private var pictureSignIn = false
    private var profileScheduled = false

    private var mode = "agent"
    private var autoRunRefactor = false
    private var contextPush: ScheduledFuture<*>? = null
    private var explainTimer: ScheduledFuture<*>? = null

    /** The selection whose explanation is on screen or on its way: not asked for twice. */
    private var explained: String? = null
    private var refactorTimer: ScheduledFuture<*>? = null
    private var refactorSeq = 0
    private var refactorTarget: JsonObject? = null

    init {
        Disposer.register(this, bridge.subscribe(object : BridgeSubscriber {
            override fun onEvent(event: BusEvent) {
                // Subscribed here rather than by the panel, so edits show with it closed.
                diffs.handle(event)
                changes.handle(event)
                status.handle(event)
                onBridgeEvent(event)
            }

            override fun onRestart() {
                explained = null
                // The restart re-read the config, so the hint state may have changed.
                refreshInlineSettings()
            }

            override fun onStarted(client: BridgeClient, reason: StartReason) = resume(client, reason)
        }))
        Disposer.register(this, changes.onListChanged(::postSessionChanges))
        ApplicationManager.getApplication().messageBus.connect(this).subscribe(
            CodeAiSettings.CHANGED,
            CodeAiSettings.Listener {
                host.run {
                    postEditorDiff()
                    postEditorSettings()
                }
            },
        )
        // Keep the panel in sync with what the user is looking at, so it can show
        // a context chip and let the user opt out before sending.
        EditorContexts.watch(
            project,
            this,
            changed = ::scheduleContextPush,
            selectionMoved = { editor ->
                val selection = EditorContexts.selection(project, editor)
                host.run { onSelectionMoved(selection) }
            },
        )
    }

    /** The chat, in the IDE's embedded browser. The panel belongs to `parent`. */
    fun createPanel(parent: Disposable): WebviewPanel {
        val created = WebviewPanel(parent, "webview.js", object : WebviewPanel.Client {
            override fun savedState(): String? = saved.load()

            override fun saveState(json: String) = saved.save(json)

            override fun ready() = host.run { onPanelReady() }

            override fun message(message: JsonObject) = host.run { onPanelMessage(message) }
        })
        panel = created
        Disposer.register(created) { if (panel === created) panel = null }
        // Now, not when the page is up: the backend starts while the page loads,
        // and is there to take the first things the page asks for.
        bridge.ensureStarted()
        return created
    }

    override fun dispose() {
        contextPush?.cancel(false)
        explainTimer?.cancel(false)
        refactorTimer?.cancel(false)
    }

    // -- the panel ------------------------------------------------------------

    private fun post(message: JsonObject) {
        panel?.post(message)
    }

    private fun postEvent(event: BusEvent) = post(json("type" to "event", "event" to event.json))

    /**
     * The panel's bundle is up and listening. It renders first and
     * unconditionally: backend trouble is shown as events inside it, never as
     * a blank panel.
     */
    private fun onPanelReady() {
        // A page that loads with the backend already up never saw it say so.
        if (bridge.client != null) lastStatus?.let(::postEvent)
        pushContext()
        postEditorDiff()
        postEditorSettings()
        postSessionChanges(changes.summaries())
        postProfile()
        bridge.ensureStartedNow()
        // Learn whether inline hints are on, so they are right straight away.
        refreshInlineSettings()
    }

    private fun onBridgeEvent(event: BusEvent) {
        trackTurn(event)
        if (event.type == "status.changed") lastStatus = event
        // An MCP sign-in: the page opens in the user's browser. Web pages only:
        // anything else here would be the backend reaching into the machine.
        if (event.type == "mcp.auth.url") {
            val url = event.payload.text("url")
            if (WEB_URL.matches(url)) BrowserUtil.browse(url)
        }
        // The agent works on the files with its own hands: when a turn ends,
        // have the IDE look at the disk rather than wait for the window to be
        // focused again.
        if (event.type == "status.changed" && event.source != HOST_SOURCE &&
            event.payload.text("state") in TURN_ENDED
        ) {
            VirtualFileManager.getInstance().asyncRefresh(null)
        }
        postEvent(event)
    }

    private fun onPanelMessage(message: JsonObject) {
        val client = bridge.client
        when (message.text("type")) {
            "submit" -> submit(message, client)
            "newConversation" -> {
                activeConversation = message.text("id")
                changes.reset()
                client?.send("newConversation", json("conversation_id" to message.text("id")))
            }
            "listConversations" -> client?.request("listConversations")?.then(::postConversations)
            "loadConversation" -> {
                val id = message.text("id")
                activeConversation = id
                changes.reset()
                if (client != null) {
                    client.request("loadConversation", json("conversation_id" to id)).then(
                        { result ->
                            val r = result.asObject()
                            post(
                                json(
                                    "type" to "conversationLoaded",
                                    "id" to (r.string("id") ?: id),
                                    "messages" to (r.array("messages") ?: JsonArray()),
                                )
                            )
                        },
                        { error -> recoverFromMissingConversation(client, id, error) },
                    )
                }
            }
            "deleteConversation" -> {
                if (message.text("id") == activeConversation) activeConversation = null
                client?.send("deleteConversation", json("conversation_id" to message.text("id")))
            }
            "answerNow" ->
                client?.request("answerNow", json("text" to message.text("text")))?.then(
                    { },
                    { error -> postEvent(syntheticError("could not hand the message over: ${asError(error)}")) },
                )
            "deleteMessage" ->
                client?.request(
                    "deleteMessage",
                    json(
                        "text" to message.text("text"),
                        "from_end" to (message.number("fromEnd")?.toInt() ?: 0),
                        "count" to (message.number("count")?.toInt() ?: 1),
                    ),
                )?.then(
                    { answer ->
                        // Off the screen either way; the agent's memory of it is another matter.
                        if (!answer.asObject().truthy("deleted")) {
                            postEvent(
                                syntheticNotice(
                                    "That message had already been folded into the conversation's summary: " +
                                        "it is off the list, but the summary still holds what it said."
                                )
                            )
                        }
                    },
                    { error -> postEvent(syntheticError("could not delete the message: ${asError(error)}")) },
                )
            "getSettings" -> client?.request("getSettings")?.then(
                { settings -> post(json("type" to "settings", "settings" to settings)) },
                { error ->
                    LOG.warn("getSettings failed: $error")
                    // The settings screen renders nothing until this answer lands,
                    // so a rejection swallowed here is a screen that loads for ever.
                    postEvent(syntheticError("could not read the settings: ${asError(error)}"))
                },
            )
            "updateSettings" ->
                client?.request("updateSettings", json("updates" to (message.obj("updates") ?: JsonObject())))?.then(
                    { result ->
                        // Keep the inline-hints state in step with edits made from the panel.
                        applyInlineSettings(result.asObject().obj("settings"))
                        post(json("type" to "settingsUpdated", "result" to result))
                    },
                    { error -> Notify.error(project, "Failed to save settings: ${asError(error)}") },
                )
            "listModels" -> client?.request("listModels", timeoutMs = AI_REQUEST_TIMEOUT_MS)?.then(
                { result ->
                    post(json("type" to "modelsListed", "models" to (result.asObject().array("models") ?: JsonArray())))
                },
                { error -> post(json("type" to "modelsError", "message" to asError(error))) },
            )
            "mcp" -> runMcp(message, client)
            "mcpElicitation" -> {
                val id = message.text("id")
                client?.request(
                    "mcpElicitationAnswer",
                    json(
                        "id" to id,
                        "action" to message.text("action"),
                        "content" to (message.obj("content") ?: JsonObject()),
                    ),
                )?.then(
                    { result ->
                        val r = result.asObject()
                        post(
                            json(
                                "type" to "mcpElicitationResult",
                                "id" to id,
                                "ok" to r.truthy("ok"),
                                "message" to r.text("message"),
                            )
                        )
                    },
                    { error ->
                        post(json("type" to "mcpElicitationResult", "id" to id, "ok" to false, "message" to error))
                    },
                )
            }
            "cancel" -> client?.send("cancel")
            "compact" -> client?.send("compact")
            "resolveApproval" -> client?.send(
                "resolveApproval",
                json(
                    "call_id" to message.text("call_id"),
                    "scope" to message.text("scope"),
                    "reason" to message.text("reason"),
                ),
            )
            "setPermissionMode" -> client?.send("setPermissionMode", json("mode" to message.text("mode")))
            "setMode" -> {
                mode = message.text("mode", "agent")
                autoRunRefactor = message.truthy("autoRunRefactor")
                if (mode != "explain") explained = null
            }
            "analyzeRefactor" -> runAnalyzeRefactor()
            "planRefactor" -> runPlanRefactor(message.text("id"), message.array("improvements") ?: JsonArray())
            "answerQuestions" -> client?.send("answerQuestion", json("answer" to message.text("text")))
            "setPlannerMode" -> client?.send("setPlannerMode", json("mode" to message.text("mode")))
            "replan" -> runCommand("replan", JsonObject(), client)
            "startPlan" -> runCommand("startPlan", JsonObject(), client)
            "goal" -> {
                val method = when (message.text("action")) {
                    "define" -> "goalDefine"
                    "start" -> "goalStart"
                    "stop" -> "goalStop"
                    else -> "goalStatus"
                }
                runCommand(method, json("objective" to message.text("objective")), client)
            }
            "terminal" -> {
                val request = message.obj("request") ?: JsonObject()
                when (request.text("action")) {
                    "start" -> runCommand("terminalStart", json("command" to request.text("command")), client)
                    "write" -> runCommand("terminalWrite", json("text" to request.text("text")), client)
                    "ctrl" -> runCommand("terminalControl", json("key" to request.text("key")), client)
                    "kill" -> runCommand("terminalKill", JsonObject(), client)
                    else -> runCommand("terminalStatus", JsonObject(), client)
                }
            }
            "indexRefresh" -> runCommand(
                "indexRefresh",
                json("full" to message.truthy("full"), "subtree" to message.text("subtree")),
                client,
            )
            "summarizeConversation" -> if (client != null) {
                // Fire and forget: the card updates on the next list, and a summary
                // that fails is not worth interrupting the user for.
                client.request(
                    "summarizeConversation",
                    json("conversation_id" to message.text("id")),
                    AI_REQUEST_TIMEOUT_MS,
                ).then({ client.request("listConversations").then(::postConversations) })
            }
            "runAsset" -> runCommand(
                "runAsset",
                json("token" to message.text("token"), "argument" to message.text("argument")),
                client,
            )
            "report" -> runCommand(message.text("method"), JsonObject(), client, reply = "report")
            "searchFiles" -> searchWorkspaceFiles(message.text("query"))
            "listSlashCommands" -> client?.request("listSlashCommands")?.then({ result ->
                post(
                    json("type" to "slashCommands", "commands" to (result.asObject().array("commands") ?: JsonArray()))
                )
            })
            "restartBridge" -> bridge.restart()
            "openDiff" -> diffs.open(message.text("path"))
            "openChange" -> changes.open(message.text("path"))
            "revertChange" -> changes.revert(message.text("path"))
            "reviewChanges" -> changes.reviewAll()
            "updateEditorSettings" -> updateEditorSettings(message.obj("updates") ?: JsonObject())
            "installUpdate", "signInForUpdates" -> explainUpdates()
            "avatarSignIn" -> signInForPicture()
            "reloadWindow" -> ApplicationManager.getApplication().invokeLater {
                ApplicationManager.getApplication().restart()
            }
        }
    }

    // -- sending a turn -------------------------------------------------------

    private fun submit(message: JsonObject, client: BridgeClient?) {
        // The agent reads the files from disk: what was typed and not yet saved
        // has to be there, or it works on a file the user no longer has.
        ApplicationManager.getApplication().invokeAndWait {
            FileDocumentManager.getInstance().saveAllDocuments()
        }
        val excluded = message.present("includeContext")?.let { it.isJsonPrimitive && !it.asBoolean } == true
        val params = json(
            "text" to message.text("text"),
            "context" to if (excluded) "" else formatEditorContext(EditorContexts.current(project)),
            "images" to (message.array("images") ?: JsonArray()),
            // Default false: a second message is usually a second question, and
            // steering silently folds it into the answer to the first.
            "steer" to (message.present("steer")?.let { it.isJsonPrimitive && it.asBoolean } == true),
        )
        if (client != null) {
            inFlight += params
            client.send("submitUserMessage", params)
            return
        }
        // Dropping it here is what would make one crash look like a dead panel.
        pendingSubmits += params
        postEvent(
            syntheticNotice(
                "The Code-AI backend is not running; starting it, and your message goes out as soon as it is up."
            )
        )
        bridge.ensureStarted()
    }

    /**
     * A new backend process knows nothing of the thread on screen. Point it
     * back at that thread first, then send what was typed while it was down, in
     * that order, so the message lands in the conversation it was written for.
     */
    private fun resume(client: BridgeClient, reason: StartReason) {
        // Alongside the first requests, not ahead of the conversation's reload.
        if (profile?.text("avatar").isNullOrEmpty() && !profileScheduled) {
            profileScheduled = true
            host.later(PROFILE_DELAY_MS) {
                profileScheduled = false
                loadProfile()
            }
        }
        // Taken now: the new backend announces READY as it starts, which would
        // otherwise clear the turn this is about to send again.
        val interrupted = if (reason == StartReason.CRASH) ArrayList(inFlight) else ArrayList()
        inFlight = ArrayList()
        val send = {
            if (interrupted.isNotEmpty() && resends < MAX_TURN_RESENDS) {
                resends += 1
                for (params in interrupted) {
                    postEvent(
                        syntheticEvent(
                            "turn.resending",
                            json(
                                "text" to params.text("text"),
                                "attempt" to resends,
                                "max_attempts" to MAX_TURN_RESENDS,
                            ),
                        )
                    )
                    inFlight += params
                    client.send("submitUserMessage", params)
                }
            } else if (interrupted.isNotEmpty()) {
                postEvent(
                    syntheticError(
                        "the backend restarted mid-answer $MAX_TURN_RESENDS times in a row; the message was not sent again"
                    )
                )
            }
            val pending = ArrayList(pendingSubmits)
            pendingSubmits.clear()
            for (params in pending) {
                inFlight += params
                client.send("submitUserMessage", params)
            }
        }
        val id = activeConversation
        if (id == null) {
            send()
        } else {
            client.request("loadConversation", json("conversation_id" to id, "adopt" to true)).then(
                { send() },
                { error ->
                    LOG.warn("Could not resume the conversation: $error")
                    send()
                },
            )
        }
    }

    /** Follow whether a turn is still running, for [resume]. */
    private fun trackTurn(event: BusEvent) {
        if (event.source == HOST_SOURCE) return
        if (event.type == "model.response.completed") {
            resends = 0
        } else if (event.type == "status.changed" && event.payload.text("state") in TURN_ENDED) {
            inFlight = ArrayList()
        }
    }

    /**
     * A conversation the user picked is not in the store. That is not an error
     * they can act on: the archive is per-project, so a panel that kept its
     * history across a move - or across a switch from a virtualenv to the
     * bundled backend, which reads a different store - lists entries this
     * process never had. Drop the dead entry, start a fresh conversation, and
     * say so in the panel rather than leaving a dialog over an empty transcript.
     */
    private fun recoverFromMissingConversation(client: BridgeClient, id: String, error: String) {
        if (!error.contains("No saved conversation", ignoreCase = true)) {
            Notify.error(project, "Could not open conversation: ${asError(error)}")
            return
        }
        client.send("newConversation")
        postEvent(syntheticNotice("That conversation is no longer saved ($id). Started a new one."))
        // Re-read the archive so the entry the user just clicked stops being listed.
        client.request("listConversations").then(::postConversations, {})
    }

    private fun postConversations(result: JsonElement) = post(
        json(
            "type" to "conversationsList",
            "conversations" to (result.asObject().array("conversations") ?: JsonArray()),
        )
    )

    // -- commands and MCP -----------------------------------------------------

    /**
     * Fire a bridge method whose only answer is a status line, and put that
     * line in the chat. Panel buttons (replan, goal, terminal, index) all go
     * through here so a failure is never silent.
     */
    private fun runCommand(method: String, params: JsonObject, client: BridgeClient?, reply: String = "commandResult") {
        if (client == null) {
            post(json("type" to reply, "ok" to false, "text" to "Code-AI: the bridge is not running."))
            return
        }
        client.request(method, params, AI_REQUEST_TIMEOUT_MS).then(
            { result ->
                val r = result.asObject()
                val text = r.text("message")
                if (text.isNotBlank()) {
                    val failed = r.present("ok")?.let { it.isJsonPrimitive && !it.asBoolean } == true
                    post(json("type" to reply, "ok" to !failed, "text" to text))
                }
            },
            { error -> post(json("type" to reply, "ok" to false, "text" to asError(error))) },
        )
    }

    /** Run one MCP action, then hand the panel the list as it stands afterwards. */
    private fun runMcp(action: JsonObject, client: BridgeClient?) {
        if (client == null) {
            post(json("type" to "mcpError", "message" to "Code-AI: the bridge is not running."))
            return
        }
        val name = json("name" to action.text("name"))
        val step: CompletableFuture<JsonElement>? = when (action.text("action")) {
            "reconnect" -> client.request("mcpReconnect", name, MCP_REQUEST_TIMEOUT_MS)
            "approve" -> client.request("mcpApprove", name, MCP_REQUEST_TIMEOUT_MS)
            "setEnabled" -> client.request(
                "mcpSetEnabled",
                json("name" to action.text("name"), "enabled" to action.truthy("enabled")),
                MCP_REQUEST_TIMEOUT_MS,
            )
            "save" -> client.request(
                "mcpSave",
                json("server" to (action.obj("server") ?: JsonObject()), "previous" to action.text("previous")),
                MCP_REQUEST_TIMEOUT_MS,
            )
            "remove" -> client.request("mcpRemove", name, MCP_REQUEST_TIMEOUT_MS)
            "signIn" -> client.request("mcpSignIn", name, MCP_REQUEST_TIMEOUT_MS)
            "signOut" -> client.request("mcpSignOut", name, MCP_REQUEST_TIMEOUT_MS)
            else -> null
        }
        // The list either way: a refused edit still leaves the panel showing what is true.
        val list = {
            client.request("mcpList").then(
                { result -> post(json("type" to "mcpServers", "list" to result)) },
                { error -> post(json("type" to "mcpError", "message" to error)) },
            )
        }
        if (step == null) {
            list()
        } else {
            step.then(
                { list() },
                { error ->
                    post(json("type" to "mcpError", "message" to error))
                    list()
                },
            )
        }
    }

    /**
     * Answer the composer's `@` picker from the IDE's own index of the project,
     * which already knows the files and what the project leaves out.
     */
    private fun searchWorkspaceFiles(query: String) {
        AppExecutorUtil.getAppExecutorService().execute {
            val paths = try {
                Workspace.search(project, query)
            } catch (e: RuntimeException) {
                LOG.warn("searchFiles failed", e)
                emptyList()
            }
            post(json("type" to "fileMatches", "query" to query, "paths" to paths))
        }
    }

    // -- the editor -----------------------------------------------------------

    private fun scheduleContextPush() {
        // A selection being dragged changes many times a second: one push when it rests.
        host.run {
            if (contextPush != null) return@run
            contextPush = host.later(CONTEXT_PUSH_DELAY_MS) {
                contextPush = null
                pushContext()
            }
        }
    }

    private fun pushContext() {
        if (panel == null) return
        post(json("type" to "editorContext", "context" to EditorContexts.current(project)?.toJson()))
    }

    private fun onSelectionMoved(selection: ActiveSelection?) {
        if (mode == "explain") {
            explainTimer?.cancel(false)
            // Keep the last explanation when the selection collapses (a click),
            // so one already on screen stays there.
            if (selection != null && !selection.isEmpty) {
                explainTimer = host.later(EXPLAIN_DELAY_MS) { startExplain(selection) }
            }
        } else if (mode == "refactor" && autoRunRefactor) {
            refactorTimer?.cancel(false)
            refactorTimer = host.later(REFACTOR_DELAY_MS) { runAnalyzeRefactor() }
        }
    }

    /** Explain the selection, in the panel. */
    private fun startExplain(selection: ActiveSelection) {
        val client = bridge.client ?: return
        if (mode != "explain" || selection.text.isBlank()) return
        val key = "${selection.file.url}:${selection.startOffset}:${selection.endOffset}"
        if (explained == key) return // already computing, or on screen
        explained = key
        val target = json(
            "path" to selection.path,
            "language" to selection.language,
            "startLine" to selection.startLine,
            "endLine" to selection.endLine,
        )
        post(json("type" to "explainStatus", "status" to "analyzing", "target" to target))
        client.request(
            "explainCode",
            json("code" to selection.text, "path" to selection.path, "language" to selection.language),
            AI_REQUEST_TIMEOUT_MS,
        ).then(
            { result ->
                val markdown = result.asObject().text("markdown").ifEmpty { "_No explanation available._" }
                post(json("type" to "explainResult", "markdown" to markdown, "target" to target))
            },
            { error ->
                if (explained == key) explained = null
                post(json("type" to "explainError", "message" to asError(error)))
            },
        )
    }

    /** Analyze the active selection for architectural improvements. */
    private fun runAnalyzeRefactor() {
        val client = bridge.client
        val selection = EditorContexts.selection(project)
        if (client == null || selection == null || selection.isEmpty) {
            if (mode == "refactor" && !autoRunRefactor) {
                post(json("type" to "refactorError", "message" to "Select code in the editor to analyze."))
            }
            return
        }
        val target = json("code" to selection.text, "path" to selection.path, "language" to selection.language)
        refactorTarget = target
        val seq = ++refactorSeq
        post(json("type" to "refactorStatus", "status" to "analyzing"))
        client.request("analyzeRefactor", target, AI_REQUEST_TIMEOUT_MS).then(
            { result ->
                if (seq == refactorSeq) {
                    post(
                        json(
                            "type" to "refactorResult",
                            "improvements" to (result.asObject().array("improvements") ?: JsonArray()),
                            "path" to selection.path,
                            "language" to selection.language,
                        )
                    )
                }
            },
            { error -> if (seq == refactorSeq) post(json("type" to "refactorError", "message" to asError(error))) },
        )
    }

    /** Generate a Markdown plan for the chosen improvements and open it. */
    private fun runPlanRefactor(id: String, improvements: JsonArray) {
        val client = bridge.client
        val target = refactorTarget
        if (client == null || target == null) {
            post(json("type" to "refactorError", "message" to "Nothing to plan: analyze a selection first."))
            return
        }
        post(json("type" to "refactorPlanning", "id" to id))
        val params = target.deepCopy().also { it.add("improvements", improvements) }
        client.request("planRefactor", params, AI_REQUEST_TIMEOUT_MS).then(
            { result ->
                val markdown = result.asObject().text("markdown").ifEmpty { "_The model returned an empty plan._" }
                openMarkdown(target.text("path"), markdown)
                post(json("type" to "refactorPlanned", "id" to id, "markdown" to markdown))
            },
            { error -> post(json("type" to "refactorError", "message" to asError(error))) },
        )
    }

    /** Write the plan to a temporary Markdown file and open it in the editor, with its preview. */
    private fun openMarkdown(sourcePath: String, markdown: String) {
        val base = if (sourcePath.isEmpty()) "selection" else File(sourcePath).name.replace(Regex("""\W+"""), "-")
        val file = File(FileUtil.getTempDirectory(), "code-ai-refactor-$base-${System.currentTimeMillis()}.md")
        try {
            file.writeText(markdown)
        } catch (e: IOException) {
            Notify.error(project, "Could not write the refactor plan: ${e.message}")
            return
        }
        ApplicationManager.getApplication().invokeLater {
            if (project.isDisposed) return@invokeLater
            LocalFileSystem.getInstance().refreshAndFindFileByIoFile(file)?.let {
                FileEditorManager.getInstance(project).openFile(it, true)
            }
        }
    }

    // -- settings ---------------------------------------------------------------

    /** Where edits are reviewed: the IDE's diff, or the panel's own view. */
    private fun postEditorDiff() = post(json("type" to "editorDiff", "enabled" to diffs.enabled))

    /** The plugin's own settings, as the panel's settings screen edits them. */
    private fun postEditorSettings() {
        val settings = CodeAiSettings.get()
        post(
            json(
                "type" to "editorSettings",
                "settings" to json(
                    "diffInEditor" to settings.diffInEditor,
                    "command" to settings.command,
                    "args" to settings.args,
                ),
            )
        )
    }

    private fun updateEditorSettings(updates: JsonObject) {
        CodeAiSettings.get().update(
            command = updates.string("command"),
            args = if (updates.has("args")) updates.strings("args") else null,
            diffInEditor = updates.present("diffInEditor")?.takeIf { it.isJsonPrimitive }?.asBoolean,
        )
        // Even when nothing changed: the screen is waiting for what it saved.
        postEditorSettings()
        postEditorDiff()
    }

    private fun postSessionChanges(files: List<ChangeSummary>) = post(
        json(
            "type" to "sessionChanges",
            "files" to files.map {
                json(
                    "path" to it.path,
                    "added" to it.added,
                    "removed" to it.removed,
                    "created" to it.created,
                    "revertable" to it.revertable,
                )
            },
        )
    )

    /** Pull the inline-hints enabled state and model from the backend's settings. */
    private fun refreshInlineSettings() {
        bridge.client?.request("getSettings")?.then(
            { settings -> applyInlineSettings(settings.asObject()) },
            { error -> LOG.warn("refreshInlineSettings failed: $error") },
        )
    }

    private fun applyInlineSettings(settings: JsonObject?) {
        if (settings == null) return
        inline.enabled = settings.truthy("inline_hints_enabled")
        inline.model = settings.text("inline_model").ifEmpty { settings.text("model") }.trim()
    }

    /** Flip inline hints on or off, persisting through the backend's settings. */
    fun toggleInlineHints() = host.run {
        val client = bridge.client
        if (client == null) {
            Notify.warning(project, "The backend is not running yet: open the Code-AI tool window first.")
            return@run
        }
        client.request("updateSettings", json("updates" to json("inline_hints_enabled" to !inline.enabled))).then(
            { result ->
                applyInlineSettings(result.asObject().obj("settings"))
                // Keep an open settings screen in step.
                post(json("type" to "settingsUpdated", "result" to result))
                val model = if (inline.model.isEmpty()) "" else " (model: ${inline.model})"
                Notify.info(project, if (inline.enabled) "Inline hints are on$model." else "Inline hints are off.")
            },
            { error -> Notify.error(project, "Could not toggle inline hints: ${asError(error)}") },
        )
    }

    // -- the user's picture, and updates ----------------------------------------

    /**
     * The user's picture on the releases host, through the backend: the same
     * login and host its update check uses. Without one the icon stays.
     */
    private fun loadProfile() {
        bridge.client?.request("getProfile", timeoutMs = PROFILE_TIMEOUT_MS)?.then(
            { found -> takeProfile(found.asObject()) },
            // An older backend has no getProfile: the icon stays, and that is all.
            { error -> LOG.info("Code-AI profile lookup failed: $error") },
        )
    }

    /**
     * A host in private mode shows the picture to browser sessions only: open a
     * browser window the user signs in to. On a click, never on its own.
     */
    private fun signInForPicture() {
        val client = bridge.client
        if (client == null || pictureSignIn) return
        pictureSignIn = true
        // Long enough to get through a company sign-in by hand.
        client.request("signInForPicture", timeoutMs = PICTURE_SIGN_IN_TIMEOUT_MS).then(
            { found ->
                pictureSignIn = false
                takeProfile(found.asObject())
            },
            { error ->
                pictureSignIn = false
                takeProfile((profile ?: JsonObject()).deepCopy().also {
                    it.addProperty("problem", error)
                    it.addProperty("signIn", true)
                })
            },
        )
    }

    private fun takeProfile(found: JsonObject) {
        profile = json(
            "login" to found.text("login"),
            "name" to found.text("name"),
            "avatar" to found.text("avatar"),
            "problem" to found.text("problem"),
            "signIn" to found.truthy("signIn"),
        ).also {
            // The backend log has it too; this is for whoever reads the IDE's.
            if (it.text("problem").isNotEmpty()) LOG.info("No GitHub picture: ${it.text("problem")}")
        }
        postProfile()
    }

    private fun postProfile() {
        val known = profile ?: return
        post(known.deepCopy().also { it.addProperty("type", "profile") })
    }

    /**
     * The backend's updater fetches the package VS Code installs, and nothing
     * here installs the plugin's own from a release yet: say how the plugin
     * is updated instead of failing half-way.
     */
    private fun explainUpdates() {
        Notify.info(
            project,
            "This plugin is updated by installing a newer package: Settings | Plugins | Install Plugin from Disk.",
        )
    }

    // -- helpers ----------------------------------------------------------------

    /** Continue on the host thread with a request's result, or with why it failed. */
    private fun CompletableFuture<JsonElement>.then(
        onResult: (JsonElement) -> Unit,
        onError: (String) -> Unit = { LOG.warn("Code-AI bridge request failed: $it") },
    ) {
        whenComplete { result, failure ->
            host.run {
                if (failure != null) onError((failure.cause ?: failure).message ?: failure.toString())
                else onResult(result)
            }
        }
    }

    private fun JsonElement.asObject(): JsonObject = if (isJsonObject) asJsonObject else JsonObject()

    /** A failure as the VS Code extension words it, so the panel reads the same in both. */
    private fun asError(message: String): String = "Error: $message"

    companion object {
        private val LOG = Logger.getInstance(ChatHost::class.java)

        // Stopping a stdio server waits on its process; approving one starts it.
        private const val MCP_REQUEST_TIMEOUT_MS = 60_000L

        // AI calls (explain, refactor) go through the model, which the bridge
        // bounds at `budgets.model_timeout()` (180s by default). The client must
        // wait at least that long plus network and startup slack.
        private const val AI_REQUEST_TIMEOUT_MS = 300_000L
        private const val PROFILE_TIMEOUT_MS = 60_000L
        private const val PICTURE_SIGN_IN_TIMEOUT_MS = 6 * 60_000L
        private const val PROFILE_DELAY_MS = 5_000L
        private const val CONTEXT_PUSH_DELAY_MS = 80L
        private const val EXPLAIN_DELAY_MS = 400L
        private const val REFACTOR_DELAY_MS = 550L

        // A turn cut short by a dying backend is sent again, like any failed
        // step: up to 20 times, counted afresh whenever a model response lands.
        private const val MAX_TURN_RESENDS = 20

        // Backend states that end a turn (synthetic DISCONNECTED does not: that
        // is the crash the turn has to survive).
        private val TURN_ENDED = setOf("READY", "FAILED", "CLOSED")
        private val WEB_URL = Regex("^https?://.*", RegexOption.IGNORE_CASE)
    }
}
