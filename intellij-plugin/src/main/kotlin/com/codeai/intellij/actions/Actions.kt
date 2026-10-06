package com.codeai.intellij.actions

import com.codeai.intellij.CodeAiPlugin
import com.codeai.intellij.bridge.BridgeSession
import com.codeai.intellij.chat.ChatHost
import com.intellij.openapi.actionSystem.ActionUpdateThread
import com.intellij.openapi.actionSystem.AnActionEvent
import com.intellij.openapi.components.service
import com.intellij.openapi.project.DumbAwareAction
import com.intellij.openapi.wm.ToolWindowManager

/** Code-AI: Focus Chat. */
internal class FocusChatAction : DumbAwareAction() {
    override fun getActionUpdateThread() = ActionUpdateThread.BGT

    override fun update(event: AnActionEvent) {
        event.presentation.isEnabled = event.project != null
    }

    override fun actionPerformed(event: AnActionEvent) {
        val project = event.project ?: return
        ToolWindowManager.getInstance(project).getToolWindow(CodeAiPlugin.TOOL_WINDOW)?.activate(null)
    }
}

/**
 * Code-AI: Restart. Respawns the backend so edited settings and config take
 * effect without restarting the IDE.
 */
internal class RestartAction : DumbAwareAction() {
    override fun getActionUpdateThread() = ActionUpdateThread.BGT

    override fun update(event: AnActionEvent) {
        event.presentation.isEnabled = event.project != null
    }

    override fun actionPerformed(event: AnActionEvent) {
        BridgeSession.of(event.project ?: return).restart()
    }
}

/** Code-AI: Show Backend Log. Opens what the backend wrote to stderr. */
internal class ShowBackendLogAction : DumbAwareAction() {
    override fun getActionUpdateThread() = ActionUpdateThread.BGT

    override fun update(event: AnActionEvent) {
        event.presentation.isEnabled = event.project != null
    }

    override fun actionPerformed(event: AnActionEvent) {
        BridgeSession.of(event.project ?: return).showLog()
    }
}

/** Code-AI: Toggle Inline Hints. Ghost text at the caret, on or off, without opening the settings. */
internal class ToggleInlineHintsAction : DumbAwareAction() {
    override fun getActionUpdateThread() = ActionUpdateThread.BGT

    override fun update(event: AnActionEvent) {
        event.presentation.isEnabled = event.project != null
    }

    override fun actionPerformed(event: AnActionEvent) {
        (event.project ?: return).service<ChatHost>().toggleInlineHints()
    }
}
