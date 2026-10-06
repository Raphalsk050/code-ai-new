package com.codeai.intellij.status

import com.codeai.intellij.bridge.BusEvent
import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.components.Service
import com.intellij.openapi.project.Project
import com.intellij.openapi.wm.WindowManager
import com.intellij.openapi.wm.impl.status.widget.StatusBarWidgetsManager

/**
 * The agent at a glance with the panel closed: context left, and whether it is
 * working or waiting on an approval. Holds what the status bar widget draws.
 */
@Service(Service.Level.PROJECT)
internal class AgentStatusService(private val project: Project) {
    @Volatile
    private var status = AgentStatus()

    init {
        // The widget is only offered once the agent has been used in the project.
        ApplicationManager.getApplication().invokeLater {
            if (!project.isDisposed) {
                project.getService(StatusBarWidgetsManager::class.java)
                    .updateWidget(AgentStatusWidgetFactory::class.java)
            }
        }
    }

    val view: StatusView get() = statusView(status)

    fun handle(event: BusEvent) {
        val next = reduceAgentStatus(status, event)
        if (next == status) return
        status = next
        ApplicationManager.getApplication().invokeLater {
            if (!project.isDisposed) {
                WindowManager.getInstance().getStatusBar(project)?.updateWidget(AgentStatusWidgetFactory.ID)
            }
        }
    }
}
