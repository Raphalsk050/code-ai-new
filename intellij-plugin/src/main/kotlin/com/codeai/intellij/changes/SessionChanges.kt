package com.codeai.intellij.changes

import com.codeai.intellij.HostThread
import com.codeai.intellij.Notify
import com.codeai.intellij.bridge.BusEvent
import com.codeai.intellij.diff.DIFF_TOOLS
import com.codeai.intellij.diff.DiffSide
import com.codeai.intellij.diff.DiffSpec
import com.codeai.intellij.diff.DiffTabs
import com.codeai.intellij.editor.Workspace
import com.codeai.intellij.util.obj
import com.codeai.intellij.util.string
import com.codeai.intellij.util.text
import com.codeai.intellij.util.truthy
import com.google.gson.JsonObject
import com.intellij.ide.projectView.ProjectView
import com.intellij.openapi.Disposable
import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.command.WriteCommandAction
import com.intellij.openapi.components.Service
import com.intellij.openapi.components.service
import com.intellij.openapi.fileEditor.FileDocumentManager
import com.intellij.openapi.project.Project
import com.intellij.openapi.ui.MessageDialogBuilder
import com.intellij.openapi.vfs.VfsUtil
import com.intellij.openapi.vfs.VirtualFile
import com.intellij.openapi.vfs.VirtualFileManager
import com.intellij.openapi.vfs.newvfs.BulkFileListener
import com.intellij.openapi.vfs.newvfs.events.VFileEvent
import java.io.IOException
import java.nio.file.Files
import java.nio.file.Path
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.ScheduledFuture

/**
 * Every file the agent changed in the conversation, against how it was before
 * the agent first touched it: the Changes section of the panel's rail. Unlike
 * the per-turn diff tabs this spans the whole conversation and outlives the
 * turn, so the user can review or undo it later.
 *
 * The counterpart of the VS Code extension's src/session-changes.ts. The
 * ledger lives on the project's [HostThread].
 */
@Service(Service.Level.PROJECT)
internal class SessionChanges(private val project: Project) : Disposable {
    private val host = project.service<HostThread>()
    private val tabs = project.service<DiffTabs>()
    private val ledger = ChangeLedger()
    private val listeners = CopyOnWriteArrayList<(List<ChangeSummary>) -> Unit>()
    private var pending: ScheduledFuture<*>? = null

    /** Files the project view marks, by path: true for one the agent created. Read from the EDT. */
    private val decorated = ConcurrentHashMap<String, Boolean>()

    init {
        // An edit by hand moves the counts as much as one by the agent.
        project.messageBus.connect(this).subscribe(VirtualFileManager.VFS_CHANGES, object : BulkFileListener {
            override fun after(events: List<VFileEvent>) {
                val paths = events.mapNotNull { it.file?.takeIf(VirtualFile::isInLocalFileSystem)?.path }
                if (paths.isNotEmpty()) host.run { if (paths.any { known(it) }) refresh() }
            }
        })
    }

    /** Be told the new list whenever it may have changed. Called on the host thread. */
    fun onListChanged(listener: (List<ChangeSummary>) -> Unit): Disposable {
        listeners += listener
        return Disposable { listeners -= listener }
    }

    /** Whether the project view marks this file, and as created (true) or changed (false). */
    fun decoration(file: VirtualFile): Boolean? = decorated[key(file.path)]

    fun handle(event: BusEvent) {
        val p = event.payload
        if (event.type == "file.write.before") {
            val place = Workspace.resolve(project, p.text("path")) ?: return
            ledger.snapshot(place.path, place.relative, p.text("content"), p.truthy("existed"))
            return
        }
        if (event.type == "tool.call.completed" && p.text("name") in DIFF_TOOLS) {
            val result = p.obj("result") ?: JsonObject()
            val path = result.string("path")
            if (result.text("location") == "sandbox" || path == null) return
            val place = Workspace.resolve(project, path) ?: return
            ledger.touched(place.path, place.relative)
            refresh()
        }
    }

    /** A new or another conversation: its changes are its own. */
    fun reset() {
        ledger.clear()
        publish(emptyList())
    }

    fun summaries(): List<ChangeSummary> = ledger.summarize(::read)

    /** The change to one file, original on the left, the file on disk on the right. */
    fun open(relative: String) = host.run {
        val entry = ledger.byRelative(relative) ?: return@run
        val before = entry.before
        if (before == null) tabs.openFile(entry.path) else tabs.show(spec(entry, before))
    }

    /** All of them at once, in one tab to step through. */
    fun reviewAll() = host.run {
        val specs = ledger.list().mapNotNull { entry -> entry.before?.let { spec(entry, it) } }
        if (specs.isNotEmpty()) tabs.showAll("Code-AI: changes in this conversation", specs)
    }

    /** Put a file back as it was before the agent touched it, after asking. */
    fun revert(relative: String) = host.run {
        val entry = ledger.byRelative(relative) ?: return@run
        val before = entry.before ?: return@run
        ApplicationManager.getApplication().invokeLater {
            if (project.isDisposed) return@invokeLater
            val action = if (entry.existed) "Revert" else "Delete"
            val question =
                if (entry.existed) {
                    "Revert $relative to how it was before the agent changed it? Later edits to it are lost too."
                } else {
                    "Delete $relative? The agent created it in this conversation."
                }
            val agreed = MessageDialogBuilder.yesNo("Code-AI", question).yesText(action).noText("Cancel").ask(project)
            if (!agreed) return@invokeLater
            try {
                undo(entry, before)
            } catch (e: IOException) {
                Notify.error(project, "Could not revert $relative: ${e.message}")
                return@invokeLater
            }
            host.run {
                ledger.forget(entry.path)
                refresh()
            }
        }
    }

    override fun dispose() {
        pending?.cancel(false)
        listeners.clear()
    }

    /** Through the IDE, so open editors follow and the IDE's local history can bring it back. */
    private fun undo(entry: LedgerEntry, before: String) {
        val file = Workspace.file(entry.path)
        WriteCommandAction.runWriteCommandAction(project, "Code-AI: Revert ${entry.relative}", null, {
            if (entry.existed) {
                if (file != null) {
                    val document = FileDocumentManager.getInstance().getDocument(file)
                    if (document != null) {
                        document.setText(before.replace("\r\n", "\n"))
                        FileDocumentManager.getInstance().saveDocument(document)
                    } else {
                        VfsUtil.saveText(file, before)
                    }
                } else {
                    Files.writeString(Path.of(entry.path), before)
                    Workspace.refresh(entry.path)
                }
            } else {
                file?.delete(this)
            }
        })
    }

    private fun spec(entry: LedgerEntry, before: String) = DiffSpec(
        title = "${entry.relative} (since the conversation started)",
        relative = entry.relative,
        before = before,
        side = DiffSide.OnDisk(entry.path),
        beforeTitle = "Before the conversation",
        sideTitle = "Now",
    )

    private fun known(path: String): Boolean = ledger.get(path) != null || ledger.get(Path.of(path).toString()) != null

    /** Coalesced: a burst of writes reads the files once. */
    private fun refresh() {
        if (pending != null) return
        pending = host.later(REFRESH_MS) {
            pending = null
            publish(summaries())
        }
    }

    private fun publish(list: List<ChangeSummary>) {
        for (listener in listeners) listener(list)
        redecorate(list)
    }

    private fun redecorate(list: List<ChangeSummary>) {
        val next = HashMap<String, Boolean>()
        for (item in list) ledger.byRelative(item.path)?.let { next[key(it.path)] = item.created }
        if (next == decorated) return
        decorated.keys.retainAll(next.keys)
        decorated.putAll(next)
        ApplicationManager.getApplication().invokeLater {
            if (!project.isDisposed) ProjectView.getInstance(project).refresh()
        }
    }

    /** The same file under one spelling, whichever slash it was named with. */
    private fun key(path: String): String = path.replace('\\', '/')

    private fun read(path: String): String? = try {
        Files.readString(Path.of(path))
    } catch (_: IOException) {
        null
    } catch (_: RuntimeException) {
        null
    }

    companion object {
        private const val REFRESH_MS = 120L
    }
}
