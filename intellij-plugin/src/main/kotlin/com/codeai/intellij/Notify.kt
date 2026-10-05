package com.codeai.intellij

import com.intellij.notification.NotificationAction
import com.intellij.notification.NotificationGroupManager
import com.intellij.notification.NotificationType
import com.intellij.openapi.project.Project

/** The plugin's balloons: what the VS Code extension says with a toast. */
internal object Notify {
    private const val GROUP = "Code-AI"

    fun info(project: Project?, text: String, vararg actions: Pair<String, () -> Unit>) =
        show(project, text, NotificationType.INFORMATION, actions)

    fun warning(project: Project?, text: String, vararg actions: Pair<String, () -> Unit>) =
        show(project, text, NotificationType.WARNING, actions)

    fun error(project: Project?, text: String, vararg actions: Pair<String, () -> Unit>) =
        show(project, text, NotificationType.ERROR, actions)

    private fun show(
        project: Project?,
        text: String,
        type: NotificationType,
        actions: Array<out Pair<String, () -> Unit>>,
    ) {
        if (project?.isDisposed == true) return
        val notification = NotificationGroupManager.getInstance()
            .getNotificationGroup(GROUP)
            .createNotification("Code-AI", text, type)
        for ((label, run) in actions) {
            notification.addAction(NotificationAction.createSimpleExpiring(label) { run() })
        }
        notification.notify(project)
    }
}
