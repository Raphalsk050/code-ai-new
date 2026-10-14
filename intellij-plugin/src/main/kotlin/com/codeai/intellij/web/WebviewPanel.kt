package com.codeai.intellij.web

import com.codeai.intellij.util.obj
import com.codeai.intellij.util.parseJsonObject
import com.codeai.intellij.util.present
import com.codeai.intellij.util.text
import com.google.gson.JsonArray
import com.google.gson.JsonObject
import com.intellij.ide.BrowserUtil
import com.intellij.ide.ui.LafManagerListener
import com.intellij.openapi.Disposable
import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.diagnostic.Logger
import com.intellij.openapi.editor.colors.EditorColorsListener
import com.intellij.openapi.editor.colors.EditorColorsManager
import com.intellij.openapi.util.Disposer
import com.intellij.ui.jcef.JBCefBrowser
import com.intellij.ui.jcef.JBCefBrowserBase
import com.intellij.ui.jcef.JBCefBrowserBuilder
import com.intellij.ui.jcef.JBCefJSQuery
import com.intellij.util.concurrency.AppExecutorUtil
import java.io.IOException
import java.net.URI
import java.nio.file.Files
import java.nio.file.Path
import java.security.SecureRandom
import java.util.concurrent.TimeUnit
import javax.swing.JComponent
import org.cef.browser.CefBrowser
import org.cef.browser.CefFrame
import org.cef.callback.CefCallback
import org.cef.handler.CefLifeSpanHandlerAdapter
import org.cef.handler.CefLoadHandlerAdapter
import org.cef.handler.CefRequestHandler
import org.cef.handler.CefRequestHandlerAdapter
import org.cef.handler.CefResourceHandlerAdapter
import org.cef.handler.CefResourceRequestHandler
import org.cef.handler.CefResourceRequestHandlerAdapter
import org.cef.misc.BoolRef
import org.cef.misc.IntRef
import org.cef.misc.StringRef
import org.cef.network.CefRequest
import org.cef.network.CefResponse

/**
 * One of the plugin's panels in the IDE's embedded browser: the counterpart of
 * a VS Code webview. It runs a bundle built for VS Code, unchanged, and gives
 * it the host that bundle expects.
 *
 * Messages go both ways as JSON. [post] may be called from any thread, before
 * the page is up or while it is reloading: they are kept until the bundle is
 * listening and then delivered in order. [Client] is called on the browser's
 * own threads.
 */
internal class WebviewPanel(
    parent: Disposable,
    /** The bundle to run: `webview.js` or `flow.js`. */
    private val script: String,
    private val client: Client,
    /** The project's folder: pictures in answers and documents are served from it, and nothing else of it. */
    private val workspace: Path? = null,
) : Disposable {
    interface Client {
        /** The state a previous page of this panel saved, as JSON, or null. */
        fun savedState(): String?

        /** The page saved its state (what `getState()` answers after a reload). */
        fun saveState(json: String)

        /** The bundle started listening: everything it needs to draw itself can be posted now. */
        fun ready()

        /** A message from the bundle to its host. */
        fun message(message: JsonObject)
    }

    private val browser: JBCefBrowser = JBCefBrowserBuilder()
        .setEnableOpenDevToolsMenuItem(ApplicationManager.getApplication().isInternal)
        .build()
    private val query: JBCefJSQuery = JBCefJSQuery.create(browser as JBCefBrowserBase)

    /** What to post next, as JSON texts. */
    private val outbox = ArrayList<String>()
    private var ready = false
    private var flushing = false

    @Volatile
    private var disposed = false

    @Volatile
    private var page: ByteArray = ByteArray(0)

    val component: JComponent get() = browser.component

    init {
        Disposer.register(parent, this)
        Disposer.register(this, browser)
        Disposer.register(browser, query)

        query.addHandler { payload ->
            try {
                fromPage(payload)
            } catch (e: Exception) {
                LOG.warn("Code-AI panel message failed", e)
            }
            null
        }
        val jcef = browser.jbCefClient
        jcef.addRequestHandler(Requests(), browser.cefBrowser)
        jcef.addLifeSpanHandler(object : CefLifeSpanHandlerAdapter() {
            // A link asking for a window of its own: the user's browser, not a popup of ours.
            override fun onBeforePopup(browser: CefBrowser, frame: CefFrame, url: String, name: String): Boolean {
                openExternally(url)
                return true
            }
        }, browser.cefBrowser)
        jcef.addLoadHandler(object : CefLoadHandlerAdapter() {
            override fun onLoadStart(browser: CefBrowser, frame: CefFrame, type: CefRequest.TransitionType) {
                // A fresh document (first load, a reload, a renderer that died): its
                // bundle is not listening yet, whatever the last one was doing.
                if (frame.isMain) synchronized(outbox) { ready = false }
            }
        }, browser.cefBrowser)

        val bus = ApplicationManager.getApplication().messageBus.connect(this)
        bus.subscribe(LafManagerListener.TOPIC, LafManagerListener { retheme() })
        bus.subscribe(EditorColorsManager.TOPIC, EditorColorsListener { retheme() })

        browser.setPageBackgroundColor(ThemeVariables.background())
        page = render()
        browser.loadURL(WebviewPage.URL)
    }

    /** Send a message to the bundle, as VS Code's `webview.postMessage` does. */
    fun post(message: JsonObject) {
        val text = message.toString()
        synchronized(outbox) {
            outbox += text
            if (!ready || flushing) return
            flushing = true
        }
        // A reply streaming in is hundreds of events a second: hand them over a
        // frame's worth at a time rather than one script call each.
        SCHEDULER.schedule(::flush, FLUSH_DELAY_MS, TimeUnit.MILLISECONDS)
    }

    /** Give the keyboard to the page. */
    fun focus() {
        browser.component.requestFocusInWindow()
        browser.cefBrowser.setFocus(true)
    }

    fun openDevTools() = browser.openDevtools()

    override fun dispose() {
        disposed = true
        synchronized(outbox) {
            ready = false
            outbox.clear()
        }
    }

    private fun flush() {
        val batch: List<String>
        synchronized(outbox) {
            flushing = false
            if (!ready || outbox.isEmpty()) return
            batch = ArrayList(outbox)
            outbox.clear()
        }
        run("window.__codeAi.receive([${batch.joinToString(",")}]);")
    }

    private fun run(javascript: String) {
        if (disposed) return
        browser.cefBrowser.executeJavaScript(javascript, WebviewPage.URL, 0)
    }

    private fun fromPage(payload: String) {
        val envelope = parseJsonObject(payload) ?: return
        when (envelope.text("kind")) {
            "ready" -> {
                synchronized(outbox) { ready = true }
                client.ready()
                flush()
            }
            "state" -> envelope.present("body")?.let { client.saveState(it.toString()) }
            "message" -> envelope.obj("body")?.let(client::message)
        }
    }

    /** The document, with the look the IDE has now and the state the last page left. */
    private fun render(): ByteArray {
        val nonce = ByteArray(16).also(SecureRandom()::nextBytes).joinToString("") { "%02x".format(it) }
        return WebviewPage.html(
            script = script,
            theme = ThemeVariables.stylesheet(),
            themeKind = ThemeVariables.kind(),
            state = client.savedState(),
            toHost = query.inject("payload"),
            nonce = nonce,
        ).toByteArray(Charsets.UTF_8)
    }

    /** The IDE's theme or colour scheme changed: dress the open page in the new one. */
    private fun retheme() = ApplicationManager.getApplication().invokeLater {
        if (disposed) return@invokeLater
        browser.setPageBackgroundColor(ThemeVariables.background())
        // The page is rebuilt from here whenever it reloads: keep that copy current too.
        page = render()
        val css = JsonArray().apply { add(ThemeVariables.stylesheet()); add(ThemeVariables.kind()) }
        run("window.__codeAi && window.__codeAi.theme.apply(null, $css);")
    }

    private fun openExternally(url: String) {
        // Web pages only: anything else here would be the page reaching into the machine.
        if (EXTERNAL.matches(url)) BrowserUtil.browse(url)
    }

    /** Serves the page and its files from the plugin, and keeps the browser from going anywhere else. */
    private inner class Requests : CefRequestHandlerAdapter() {
        override fun onBeforeBrowse(
            browser: CefBrowser,
            frame: CefFrame,
            request: CefRequest,
            userGesture: Boolean,
            isRedirect: Boolean,
        ): Boolean {
            val url = request.url ?: return true
            if (url.startsWith(WebviewPage.ORIGIN + "/") || url.startsWith("devtools://")) return false
            // A link in an answer or in the settings: it opens in the user's browser.
            openExternally(url)
            return true
        }

        override fun getResourceRequestHandler(
            browser: CefBrowser,
            frame: CefFrame,
            request: CefRequest,
            isNavigation: Boolean,
            isDownload: Boolean,
            requestInitiator: String?,
            disableDefaultHandling: BoolRef,
        ): CefResourceRequestHandler? {
            if (request.url?.startsWith(WebviewPage.ORIGIN + "/") != true) return null
            return object : CefResourceRequestHandlerAdapter() {
                override fun getResourceHandler(browser: CefBrowser, frame: CefFrame, request: CefRequest) =
                    Served(::resource)
            }
        }

        override fun onRenderProcessTerminated(
            browser: CefBrowser,
            status: CefRequestHandler.TerminationStatus,
            errorCode: Int,
            errorString: String?,
        ) {
            // The IDE closing takes the renderer with it: nothing to bring back.
            if (disposed) return
            // The page is gone with its process. Load it again: it picks its
            // state up where it saved it, and asks the host for the rest.
            LOG.warn("The Code-AI panel's renderer stopped ($status): reloading it")
            browser.loadURL(WebviewPage.URL)
        }
    }

    private fun resource(path: String): Resource? = when (path) {
        "/index.html" -> Resource(page, "text/html")
        "/$script" -> bundled(path, "text/javascript")
        "/codicons/codicon.css" -> bundled(path, "text/css")
        "/codicons/codicon.ttf" -> bundled(path, "font/ttf")
        else -> if (path.startsWith(WORKSPACE_PATH)) workspaceImage(path.removePrefix(WORKSPACE_PATH)) else null
    }

    /** An image under the project, and only an image, and only under it: links out of it are followed first. */
    private fun workspaceImage(relative: String): Resource? {
        val root = workspace ?: return null
        val mime = IMAGE_TYPES[relative.substringAfterLast('.', "").lowercase()] ?: return null
        return try {
            val top = root.toRealPath()
            val file = top.resolve(relative).normalize().toRealPath()
            if (!file.startsWith(top) || !Files.isRegularFile(file) || Files.size(file) > MAX_IMAGE_BYTES) {
                null
            } else {
                Resource(Files.readAllBytes(file), mime)
            }
        } catch (_: IOException) {
            null
        } catch (_: RuntimeException) {
            null
        }
    }

    private fun bundled(path: String, mime: String): Resource? =
        WebviewPanel::class.java.getResourceAsStream("/webview$path")?.use { Resource(it.readBytes(), mime) }

    private class Resource(val bytes: ByteArray, val mime: String)

    /**
     * Answers one request from memory.
     *
     * Through the adapter, not the interface: newer browsers ask a handler in
     * a second way first (open, read, skip), and the adapter of each answers
     * that with "ask the first way", which is the one written here.
     */
    private class Served(private val lookup: (path: String) -> Resource?) : CefResourceHandlerAdapter() {
        private var body = ByteArray(0)
        private var mime = "text/plain"
        private var status = 404
        private var sent = 0

        override fun processRequest(request: CefRequest, callback: CefCallback): Boolean {
            val found = try {
                lookup(URI(request.url).path)
            } catch (_: Exception) {
                null
            }
            if (found != null) {
                body = found.bytes
                mime = found.mime
                status = 200
            }
            callback.Continue()
            return true
        }

        override fun getResponseHeaders(response: CefResponse, responseLength: IntRef, redirectUrl: StringRef) {
            response.status = status
            response.mimeType = mime
            if (mime.startsWith("text/")) response.setHeaderByName("Content-Type", "$mime; charset=utf-8", true)
            // Always from the plugin as installed now: never a copy an older version left in a cache.
            response.setHeaderByName("Cache-Control", "no-store", true)
            responseLength.set(body.size)
        }

        override fun readResponse(dataOut: ByteArray, bytesToRead: Int, bytesRead: IntRef, callback: CefCallback): Boolean {
            val count = minOf(bytesToRead, body.size - sent)
            if (count <= 0) {
                bytesRead.set(0)
                return false
            }
            System.arraycopy(body, sent, dataOut, 0, count)
            sent += count
            bytesRead.set(count)
            return true
        }

        override fun cancel() {}
    }

    companion object {
        private val LOG = Logger.getInstance(WebviewPanel::class.java)
        private val SCHEDULER = AppExecutorUtil.getAppScheduledExecutorService()
        private const val FLUSH_DELAY_MS = 8L
        private val EXTERNAL = Regex("^(https?|mailto):.*", RegexOption.IGNORE_CASE)

        /** Where the project's own files are served, under the page's origin. */
        const val WORKSPACE_PATH = "/workspace/"
        private const val MAX_IMAGE_BYTES = 25L * 1024 * 1024
        private val IMAGE_TYPES = mapOf(
            "png" to "image/png",
            "jpg" to "image/jpeg",
            "jpeg" to "image/jpeg",
            "gif" to "image/gif",
            "webp" to "image/webp",
            "bmp" to "image/bmp",
            "svg" to "image/svg+xml",
            "avif" to "image/avif",
            "ico" to "image/x-icon",
        )
    }
}
