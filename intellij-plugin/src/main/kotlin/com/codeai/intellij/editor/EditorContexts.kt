package com.codeai.intellij.editor

import com.intellij.openapi.Disposable
import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.editor.Editor
import com.intellij.openapi.editor.EditorFactory
import com.intellij.openapi.editor.event.SelectionEvent
import com.intellij.openapi.editor.event.SelectionListener
import com.intellij.openapi.fileEditor.FileDocumentManager
import com.intellij.openapi.fileEditor.FileEditorManager
import com.intellij.openapi.fileEditor.FileEditorManagerEvent
import com.intellij.openapi.fileEditor.FileEditorManagerListener
import com.intellij.openapi.project.Project
import com.intellij.openapi.vfs.VirtualFile

/** What the selected editor holds: the file, and the selection in it. */
internal class ActiveSelection(
    val file: VirtualFile,
    val path: String,
    val language: String,
    /** Empty without a selection. */
    val text: String,
    val startOffset: Int,
    val endOffset: Int,
    /** 1-based inclusive line range of the selection. */
    val startLine: Int,
    val endLine: Int,
) {
    val isEmpty: Boolean get() = text.isEmpty()
}

/** Reads the editor: what the user has open and is looking at. */
internal object EditorContexts {
    /**
     * The editor as it is now: the active file and its selection, and the other
     * files open beside it. Null when nothing is open. Callable from any
     * thread; off the EDT it waits for it.
     */
    fun current(project: Project): EditorContext? = onEdt(project) {
        val manager = FileEditorManager.getInstance(project)
        val active = selection(project)
        val open = manager.openFiles.filter { it.isInLocalFileSystem }.map { Workspace.relative(project, it) }
        val others = otherOpenFiles(open, active?.path)
        when {
            active == null -> if (others.isEmpty()) null else EditorContext("", "", openFiles = others)
            active.isEmpty -> EditorContext(active.path, active.language, openFiles = others)
            else -> EditorContext(
                active.path, active.language, active.text, active.startLine, active.endLine, others,
            )
        }
    }

    /** The selected text editor's file and selection. Null when no file has the focus. */
    fun selection(project: Project): ActiveSelection? = onEdt(project) {
        val editor = FileEditorManager.getInstance(project).selectedTextEditor ?: return@onEdt null
        selection(project, editor)
    }

    fun selection(project: Project, editor: Editor): ActiveSelection? {
        val document = editor.document
        // Consoles, diffs and scratch buffers are not files the user is working on.
        val file = FileDocumentManager.getInstance().getFile(document)?.takeIf { it.isInLocalFileSystem } ?: return null
        val caret = editor.caretModel.primaryCaret
        val start = caret.selectionStart
        val end = caret.selectionEnd
        return ActiveSelection(
            file = file,
            path = Workspace.relative(project, file),
            language = LanguageIds.of(file),
            text = if (caret.hasSelection()) caret.selectedText.orEmpty() else "",
            startOffset = start,
            endOffset = end,
            startLine = document.getLineNumber(start) + 1,
            endLine = document.getLineNumber(end) + 1,
        )
    }

    /** The whole text of the selected editor, split at the caret: what an inline hint is asked with. */
    fun aroundCaret(editor: Editor): Pair<String, String> {
        val text = editor.document.immutableCharSequence
        val offset = editor.caretModel.offset.coerceIn(0, text.length)
        return text.subSequence(0, offset).toString() to text.subSequence(offset, text.length).toString()
    }

    /**
     * Call `changed` whenever what [current] answers may have changed: another
     * file took the focus, a tab opened or closed, the selection moved. It is
     * called on the EDT, often in bursts; `selectionMoved` is told the editor
     * when it was the selection of the active one.
     */
    fun watch(project: Project, parent: Disposable, changed: () -> Unit, selectionMoved: (Editor) -> Unit) {
        project.messageBus.connect(parent).subscribe(
            FileEditorManagerListener.FILE_EDITOR_MANAGER,
            object : FileEditorManagerListener {
                override fun fileOpened(source: FileEditorManager, file: VirtualFile) = changed()
                override fun fileClosed(source: FileEditorManager, file: VirtualFile) = changed()
                override fun selectionChanged(event: FileEditorManagerEvent) = changed()
            },
        )
        EditorFactory.getInstance().eventMulticaster.addSelectionListener(
            object : SelectionListener {
                override fun selectionChanged(event: SelectionEvent) {
                    val editor = event.editor
                    if (editor.project != project || project.isDisposed) return
                    if (editor != FileEditorManager.getInstance(project).selectedTextEditor) return
                    changed()
                    selectionMoved(editor)
                }
            },
            parent,
        )
    }

    private fun <T> onEdt(project: Project, read: () -> T?): T? {
        val application = ApplicationManager.getApplication()
        var result: T? = null
        val task = Runnable { if (!project.isDisposed) result = read() }
        if (application.isDispatchThread) task.run() else application.invokeAndWait(task)
        return result
    }
}
