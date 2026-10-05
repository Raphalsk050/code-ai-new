package com.codeai.intellij.diff

import com.codeai.intellij.HostThread
import com.codeai.intellij.bridge.BusEvent
import com.codeai.intellij.editor.Workspace
import com.codeai.intellij.settings.CodeAiSettings
import com.codeai.intellij.util.number
import com.codeai.intellij.util.obj
import com.codeai.intellij.util.string
import com.codeai.intellij.util.text
import com.codeai.intellij.util.truthy
import com.google.gson.JsonObject
import com.intellij.openapi.components.Service
import com.intellij.openapi.components.service
import com.intellij.openapi.diagnostic.Logger
import com.intellij.openapi.project.Project
import java.io.IOException
import java.nio.file.Files
import java.nio.file.Path
import java.util.concurrent.ScheduledFuture
import java.util.concurrent.atomic.AtomicLong

/**
 * The agent's file edits, in the IDE's own diff view.
 *
 * While a write streams in, or waits for approval, the diff shows the file as
 * it was when the turn started against the proposal, repainting as the model
 * types. Once the tool has written the file the right side becomes the file
 * itself, so what is on screen is what is on disk and can be edited there. A
 * refused or failed call takes its proposal away again. Focus stays in the chat.
 *
 * The counterpart of the VS Code extension's src/edit-diff.ts. Events are
 * handled on the project's [HostThread].
 */
@Service(Service.Level.PROJECT)
internal class EditDiffs(private val project: Project) {
    /** A file the agent is changing this turn. */
    private class FileEdit(val place: com.codeai.intellij.editor.Place) {
        /** The file as it was before the agent first touched it this turn. */
        var before: String? = null

        /** The call the proposal on screen belongs to. */
        var callId = ""
        var proposing = false
        var written = false

        /** What the proposal on screen is known by, and holds. */
        var proposal = PROPOSALS.incrementAndGet()
        var proposed = ""
        var repaint: ScheduledFuture<*>? = null
    }

    /** A write_file or edit_code call whose arguments are still streaming in. */
    private class Stream(val file: FileEdit, val tool: String) {
        /** The streamed source: write_file's content, or edit_code's new text. */
        var text = ""

        /** edit_code: what the edit replaces, once the backend has sent it. */
        var oldText: String? = null

        /** edit_code: the file the edit applies to, read once. */
        var current: String? = null
    }

    private val host = project.service<HostThread>()
    private val tabs = project.service<DiffTabs>()
    private var files = LinkedHashMap<String, FileEdit>()

    /** Writing calls streaming in, by their index in the model's response. */
    private val streams = HashMap<Int, Stream>()

    /** The last written change shown this turn, put back when a proposal goes away. */
    private var lastWritten: FileEdit? = null

    val enabled: Boolean get() = CodeAiSettings.get().diffInEditor

    fun handle(event: BusEvent) {
        if (!enabled) return
        val p = event.payload
        try {
            when (event.type) {
                "user.message" -> {
                    // A new turn: the next change to a file is measured from what it is now.
                    for (file in files.values) file.repaint?.cancel(false)
                    files = LinkedHashMap()
                    streams.clear()
                    lastWritten = null
                }
                "model.request.started" -> streams.clear()
                // A broken attempt's calls are being thrown away with its text.
                "model.request.retrying" -> if (p.truthy("discarded")) dropUnrun()
                "status.changed" -> if (p.text("state") in TURN_OVER) dropUnrun()
                "tool.call.progress" -> onProgress(p)
                "tool.approval.requested" -> onApproval(p)
                "tool.call.requested" -> onRequested(p)
                "file.write.before" -> onSnapshot(p)
                "tool.call.completed" -> onCompleted(p)
                "tool.call.failed" -> onFailed(p)
            }
        } catch (e: RuntimeException) {
            LOG.warn("Code-AI edit diff failed", e)
        }
    }

    /** Show the change to a file the agent touched, or the file itself when there is none. */
    fun open(value: String) = host.run {
        val place = Workspace.resolve(project, value) ?: return@run
        val file = files[place.path]
        if (file?.before != null) show(file, if (file.proposing) DiffPhase.PROPOSED else DiffPhase.WRITTEN)
        else tabs.openFile(place.path)
    }

    // --- events ------------------------------------------------------------

    private fun onProgress(p: JsonObject) {
        val tool = p.text("name")
        if (!p.truthy("writes") || tool !in DIFF_TOOLS) return
        val index = (p.number("index") ?: 0.0).toInt()
        if (p.truthy("call_started")) streams.remove(index)
        var stream = streams[index]
        if (stream == null) {
            // Arguments are still arriving, so the file is untouched yet: safe to read.
            val file = p.string("path")?.let { file(it, readNow = true) } ?: return
            stream = Stream(file, tool)
            streams[index] = stream
            // A new call: until the backend names it, it has not run.
            if (!file.proposing) file.callId = ""
        }
        p.string("old_text")?.let { stream.oldText = it }
        val key = if (tool == "write_file") "content" else "new_text"
        val delta = p.string("code_delta")
        if (p.text("code_key") != key || delta == null) return
        val offset = (p.number("code_offset") ?: 0.0).toInt()
        when (offset) {
            0 -> stream.text = delta
            stream.text.length -> stream.text += delta
            else -> return // an update went missing: hold the last coherent text
        }
        if (tool == "write_file") propose(stream.file, stream.text) else proposeEdit(stream)
    }

    /** The file with an edit_code call's replacement applied as far as it has arrived. */
    private fun proposeEdit(stream: Stream) {
        val old = stream.oldText ?: return
        val current = stream.current ?: readDisk(stream.file.place.path).also { stream.current = it }
        applyEdits(current, listOf(Edit(old, stream.text)))?.let { propose(stream.file, it) }
    }

    /** Take down proposals for calls that were streamed but never ran. */
    private fun dropUnrun() {
        streams.clear()
        for (file in files.values.toList()) {
            if (file.proposing && file.callId.isEmpty()) settle(file, file.written)
        }
    }

    private fun onApproval(p: JsonObject) {
        val tool = p.text("tool_name")
        val args = p.obj("arguments") ?: JsonObject()
        val path = args.string("path")
        if (tool !in DIFF_TOOLS || !writesToProject(args) || path == null) return
        // The call waits for the user, so the file cannot change under the read.
        val file = file(path, readNow = true) ?: return
        file.callId = p.text("call_id")
        proposedContent(tool, args, readDisk(file.place.path))?.let { propose(file, it) }
    }

    private fun onRequested(p: JsonObject) {
        val tool = p.text("name")
        val args = p.obj("arguments") ?: JsonObject()
        val path = args.string("path")
        if (tool !in DIFF_TOOLS || !writesToProject(args) || path == null) return
        // No read here: without an approval to wait for, the tool may already be
        // writing. The backend's own snapshot (file.write.before) covers it.
        val file = file(path, readNow = false) ?: return
        file.callId = p.text("tool_call_id")
        // A streamed write may have been throttled short of its last characters.
        val content = args.string("content")
        if (tool == "write_file" && file.proposing && content != null) propose(file, content)
    }

    private fun onSnapshot(p: JsonObject) {
        val file = file(p.text("path"), readNow = false) ?: return
        // The turn's first snapshot of a file is its "before"; later ones are
        // the agent's own earlier edits.
        if (file.before == null) file.before = p.text("content")
    }

    private fun onCompleted(p: JsonObject) {
        if (p.text("name") !in DIFF_TOOLS) return
        val result = p.obj("result") ?: JsonObject()
        val path = result.string("path")
        if (result.text("location") == "sandbox" || path == null) return
        val file = file(path, readNow = false) ?: return
        // The IDE did not write this file: it has to be told it changed.
        Workspace.refresh(file.place.path)
        // Without knowing what was there, a diff would claim the whole file is new.
        if (file.before == null) return
        file.written = true
        settle(file, showWritten = true)
    }

    private fun onFailed(p: JsonObject) {
        if (p.text("name") !in DIFF_TOOLS) return
        val id = p.text("tool_call_id")
        for (file in files.values.toList()) {
            if (file.proposing && file.callId == id) settle(file, file.written)
        }
    }

    // --- files ---------------------------------------------------------------

    private fun file(value: String, readNow: Boolean): FileEdit? {
        val place = Workspace.resolve(project, value) ?: return null
        val file = files.getOrPut(place.path) { FileEdit(place) }
        if (readNow && file.before == null) file.before = readDisk(place.path)
        return file
    }

    private fun readDisk(path: String): String = try {
        Files.readString(Path.of(path))
    } catch (_: IOException) {
        "" // not there yet: the write creates it
    } catch (_: RuntimeException) {
        "" // not text this can decode
    }

    // --- the diff tabs ---------------------------------------------------------

    private fun propose(file: FileEdit, text: String) {
        val opening = !file.proposing
        file.proposing = true
        file.proposed = text
        if (opening) {
            show(file, DiffPhase.PROPOSED)
            return
        }
        if (file.repaint != null) return
        // A streaming write repaints its proposal at most this often.
        file.repaint = host.later(REPAINT_MS) {
            file.repaint = null
            if (file.proposing) tabs.repaint(file.proposal, file.proposed)
        }
    }

    private fun show(file: FileEdit, phase: DiffPhase) {
        val before = file.before ?: return
        if (phase == DiffPhase.WRITTEN) lastWritten = file
        tabs.show(
            DiffSpec(
                title = diffTitle(file.place.relative, phase),
                relative = file.place.relative,
                before = before,
                side = if (phase == DiffPhase.PROPOSED) DiffSide.Proposal(file.proposal, file.proposed)
                else DiffSide.OnDisk(file.place.path),
                beforeTitle = "Before this turn",
                sideTitle = if (phase == DiffPhase.PROPOSED) "Proposed by Code-AI" else "Now",
            )
        )
    }

    /**
     * The proposal is over: the file was written (show it as it is on disk) or
     * the call went nowhere (take the proposal off the screen).
     */
    private fun settle(file: FileEdit, showWritten: Boolean) {
        file.repaint?.cancel(false)
        file.repaint = null
        val stale = file.proposal
        val wasProposing = file.proposing
        file.proposing = false
        file.callId = ""
        // A fresh name for the next proposal, so a tab left over can never show it.
        file.proposal = PROPOSALS.incrementAndGet()
        file.proposed = ""
        if (showWritten) show(file, DiffPhase.WRITTEN)
        if (!wasProposing) return
        tabs.close(stale)
        // The proposal had taken the preview tab of the change shown before it.
        val previous = lastWritten
        if (!showWritten && previous != null && previous !== file) show(previous, DiffPhase.WRITTEN)
    }

    companion object {
        private val LOG = Logger.getInstance(EditDiffs::class.java)
        private val PROPOSALS = AtomicLong()
        private const val REPAINT_MS = 150L

        // Backend states that end a turn: a proposal still open then belongs to
        // a call that never ran.
        private val TURN_OVER = setOf("READY", "FAILED", "CLOSED")
    }
}
