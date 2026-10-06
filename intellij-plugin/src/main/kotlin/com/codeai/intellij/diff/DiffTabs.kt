package com.codeai.intellij.diff

import com.codeai.intellij.editor.Workspace
import com.intellij.diff.DiffContentFactory
import com.intellij.diff.chains.SimpleDiffRequestChain
import com.intellij.diff.contents.DiffContent
import com.intellij.diff.editor.ChainDiffVirtualFile
import com.intellij.diff.editor.DiffEditorTabFilesManager
import com.intellij.diff.requests.DiffRequest
import com.intellij.diff.requests.SimpleDiffRequest
import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.command.WriteCommandAction
import com.intellij.openapi.command.undo.UndoUtil
import com.intellij.openapi.components.Service
import com.intellij.openapi.editor.Document
import com.intellij.openapi.editor.EditorFactory
import com.intellij.openapi.fileEditor.FileEditorManager
import com.intellij.openapi.fileEditor.OpenFileDescriptor
import com.intellij.openapi.fileTypes.FileType
import com.intellij.openapi.fileTypes.FileTypeManager
import com.intellij.openapi.project.Project
import com.intellij.openapi.util.text.StringUtil
import com.intellij.openapi.vfs.VirtualFile

/** What a diff tab compares the "before" text with. */
internal sealed interface DiffSide {
    /** Text that is not on disk (yet): a proposal, by the id it is repainted and closed with. */
    class Proposal(val id: Long, val text: String) : DiffSide

    /** The file itself, so what is on screen is what is on disk and can be edited there. */
    class OnDisk(val path: String) : DiffSide
}

/** One diff to show: a file as it was, against a proposal or against itself now. */
internal class DiffSpec(
    val title: String,
    /** Project-relative path: what the sides are highlighted by. */
    val relative: String,
    val before: String,
    val side: DiffSide,
    val beforeTitle: String,
    val sideTitle: String,
)

/**
 * The agent's changes in the IDE's own diff viewer, as editor tabs.
 *
 * One tab at a time is the plugin's "preview": showing a change takes the
 * place of the one shown before, as VS Code's preview tab does, so a turn that
 * touches twenty files does not leave twenty tabs. The turn's diffs and the
 * conversation's share that one tab. Focus stays where it was.
 *
 * Every method may be called from any thread; the work happens on the EDT, in
 * the order it was asked for.
 */
@Service(Service.Level.PROJECT)
internal class DiffTabs(private val project: Project) {
    private class Shown(val tab: VirtualFile, val proposal: Long?, val document: Document?)

    /** The preview tab on screen. EDT only. */
    private var shown: Shown? = null

    /** Show one change in the preview tab. */
    fun show(spec: DiffSpec) = onEdt {
        closeShown()
        val proposal = spec.side as? DiffSide.Proposal
        val document = proposal?.let { EditorFactory.getInstance().createDocument(StringUtil.convertLineSeparators(it.text)) }
        // Nobody undoes a proposal: each repaint would otherwise be kept as an edit.
        document?.let(UndoUtil::disableUndoFor)
        val request = request(spec, document) ?: return@onEdt
        val tab = ChainDiffVirtualFile(SimpleDiffRequestChain(request), spec.title)
        DiffEditorTabFilesManager.getInstance(project).showDiffFile(tab, false)
        shown = Shown(tab, proposal?.id, document)
    }

    /** Several changes in one tab, to step through: the review of a whole conversation. */
    fun showAll(title: String, specs: List<DiffSpec>) = onEdt {
        val requests = specs.mapNotNull { request(it, null) }
        if (requests.isEmpty()) return@onEdt
        closeShown()
        val tab = ChainDiffVirtualFile(SimpleDiffRequestChain(requests), title)
        DiffEditorTabFilesManager.getInstance(project).showDiffFile(tab, true)
        shown = Shown(tab, null, null)
    }

    /** The proposal on screen grew: repaint it. Ignored when another change took the tab. */
    fun repaint(proposal: Long, text: String) = onEdt {
        val document = shown?.takeIf { it.proposal == proposal }?.document ?: return@onEdt
        val next = StringUtil.convertLineSeparators(text)
        if (document.text != next) WriteCommandAction.runWriteCommandAction(project) { document.setText(next) }
    }

    /** Take a proposal off the screen: its call went nowhere. */
    fun close(proposal: Long) = onEdt {
        if (shown?.proposal == proposal) closeShown()
    }

    /** Open a file in the editor, without taking the keyboard. */
    fun openFile(path: String) = onEdt {
        val file = Workspace.file(path) ?: return@onEdt
        OpenFileDescriptor(project, file).navigate(false)
    }

    private fun closeShown() {
        val previous = shown ?: return
        shown = null
        FileEditorManager.getInstance(project).closeFile(previous.tab)
    }

    private fun request(spec: DiffSpec, proposed: Document?): DiffRequest? {
        val factory = DiffContentFactory.getInstance()
        val type = fileType(spec.relative)
        val before = factory.create(project, StringUtil.convertLineSeparators(spec.before), type)
        val side: DiffContent = when (val right = spec.side) {
            is DiffSide.Proposal -> factory.create(
                project, proposed ?: EditorFactory.getInstance().createDocument(StringUtil.convertLineSeparators(right.text)), type,
            )
            // Gone again since: nothing to compare the file's past with.
            is DiffSide.OnDisk -> factory.create(project, Workspace.file(right.path) ?: return null)
        }
        return SimpleDiffRequest(spec.title, before, side, spec.beforeTitle, spec.sideTitle)
    }

    private fun fileType(relative: String): FileType =
        FileTypeManager.getInstance().getFileTypeByFileName(relative.substringAfterLast('/'))

    private fun onEdt(task: () -> Unit) = ApplicationManager.getApplication().invokeLater {
        if (!project.isDisposed) task()
    }
}
