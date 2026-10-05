package com.codeai.intellij.status

import com.codeai.intellij.CodeAiPlugin
import com.intellij.icons.AllIcons
import com.intellij.openapi.components.service
import com.intellij.openapi.components.serviceIfCreated
import com.intellij.openapi.project.Project
import com.intellij.openapi.wm.StatusBar
import com.intellij.openapi.wm.StatusBarWidget
import com.intellij.openapi.wm.StatusBarWidgetFactory
import com.intellij.openapi.wm.ToolWindowManager
import com.intellij.ui.AnimatedIcon
import com.intellij.util.Consumer
import java.awt.event.MouseEvent
import javax.swing.Icon

internal class AgentStatusWidgetFactory : StatusBarWidgetFactory {
    override fun getId(): String = ID

    override fun getDisplayName(): String = "Code-AI"

    /** Like the VS Code extension, nothing in the bar until the agent has been opened here. */
    override fun isAvailable(project: Project): Boolean = project.serviceIfCreated<AgentStatusService>() != null

    override fun createWidget(project: Project): StatusBarWidget = AgentStatusWidget(project)

    companion object {
        const val ID = "CodeAi.AgentStatus"
    }
}

/** Icon and text: what the agent is doing and how much of its context is used. A click opens the chat. */
private class AgentStatusWidget(private val project: Project) :
    StatusBarWidget, StatusBarWidget.MultipleTextValuesPresentation {
    private val view: StatusView get() = project.service<AgentStatusService>().view

    override fun ID(): String = AgentStatusWidgetFactory.ID

    override fun getPresentation(): StatusBarWidget.WidgetPresentation = this

    override fun install(statusBar: StatusBar) {}

    override fun dispose() {}

    override fun getSelectedValue(): String = view.text

    override fun getTooltipText(): String = view.tooltip

    override fun getIcon(): Icon = view.let { now ->
        when {
            now.activity == Activity.OFFLINE -> AllIcons.General.InspectionsPause
            now.activity == Activity.WAITING -> AllIcons.General.Warning
            now.activity == Activity.BUSY -> AnimatedIcon.Default.INSTANCE
            // Past the compaction threshold: the bar says so before the panel has to.
            now.warn -> AllIcons.General.BalloonWarning
            else -> AllIcons.General.InspectionsOK
        }
    }

    override fun getClickConsumer(): Consumer<MouseEvent> = Consumer {
        ToolWindowManager.getInstance(project).getToolWindow(CodeAiPlugin.TOOL_WINDOW)?.activate(null)
    }
}
