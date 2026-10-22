package com.codeai.intellij.board

import com.codeai.intellij.chat.MissingBrowser
import com.codeai.intellij.web.EmbeddedBrowser
import com.intellij.openapi.components.service
import com.intellij.openapi.fileEditor.FileEditor
import com.intellij.openapi.fileEditor.FileEditorPolicy
import com.intellij.openapi.fileEditor.FileEditorProvider
import com.intellij.openapi.fileEditor.FileEditorState
import com.intellij.openapi.fileTypes.FileType
import com.intellij.openapi.project.DumbAware
import com.intellij.openapi.project.Project
import com.intellij.openapi.util.IconLoader
import com.intellij.openapi.util.UserDataHolderBase
import com.intellij.openapi.vfs.VirtualFile
import com.intellij.testFramework.LightVirtualFile
import java.beans.PropertyChangeListener
import javax.swing.Icon
import javax.swing.JComponent

/** What the board's tab is opened on: a file of no disk, standing for the project's board. */
internal class BoardFile : LightVirtualFile(TITLE, BoardFileType, "") {
    init {
        isWritable = false
    }

    companion object {
        const val TITLE = "Code-AI Board"
    }
}

internal object BoardFileType : FileType {
    private val boardIcon: Icon by lazy { IconLoader.getIcon("/icons/toolWindow.svg", BoardFileType::class.java) }

    override fun getName() = "Code-AI Board"

    override fun getDescription() = "Code-AI task board"

    override fun getDefaultExtension() = ""

    override fun getIcon(): Icon = boardIcon

    override fun isBinary() = true

    override fun isReadOnly() = true
}

/** Opens [BoardFile] in the board's own editor, and nothing else in it. */
internal class BoardEditorProvider : FileEditorProvider, DumbAware {
    override fun accept(project: Project, file: VirtualFile) = file is BoardFile

    override fun acceptRequiresReadAction() = false

    override fun createEditor(project: Project, file: VirtualFile): FileEditor = BoardEditor(project, file)

    override fun getEditorTypeId() = "code-ai-board"

    override fun getPolicy() = FileEditorPolicy.HIDE_DEFAULT_EDITOR
}

/** The board's tab: the board bundle in the embedded browser, or why it cannot be shown. */
internal class BoardEditor(project: Project, private val file: VirtualFile) : UserDataHolderBase(), FileEditor {
    private val content: JComponent = EmbeddedBrowser.state().let { state ->
        if (state == EmbeddedBrowser.State.READY) project.service<BoardHost>().createPanel(this).component
        else MissingBrowser.panel(project, state)
    }

    override fun getComponent() = content

    override fun getPreferredFocusedComponent() = content

    override fun getName() = BoardFile.TITLE

    override fun getFile() = file

    override fun setState(state: FileEditorState) {}

    override fun isModified() = false

    override fun isValid() = true

    override fun addPropertyChangeListener(listener: PropertyChangeListener) {}

    override fun removePropertyChangeListener(listener: PropertyChangeListener) {}

    override fun dispose() {}
}
