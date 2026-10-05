package com.codeai.intellij.chat

import com.intellij.openapi.actionSystem.ActionManager
import com.intellij.openapi.components.service
import com.intellij.openapi.project.DumbAware
import com.intellij.openapi.project.Project
import com.intellij.openapi.wm.ToolWindow
import com.intellij.openapi.wm.ToolWindowFactory
import com.intellij.ui.content.ContentFactory
import com.intellij.ui.jcef.JBCefApp

/** The Code-AI tool window: the chat, where the VS Code extension has its sidebar view. */
internal class ChatToolWindowFactory : ToolWindowFactory, DumbAware {
    override fun createToolWindowContent(project: Project, toolWindow: ToolWindow) {
        val component =
            if (JBCefApp.isSupported()) {
                val panel = project.service<ChatHost>().createPanel(toolWindow.disposable)
                toolWindow.setTitleActions(
                    listOfNotNull(ActionManager.getInstance().getAction("CodeAi.Restart"))
                )
                panel.component
            } else {
                // Never a blank panel: say what is missing and how to get it.
                MissingBrowser.panel()
            }
        toolWindow.contentManager.addContent(ContentFactory.getInstance().createContent(component, null, false))
    }
}
