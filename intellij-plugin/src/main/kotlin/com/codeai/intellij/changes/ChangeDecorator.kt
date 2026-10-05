package com.codeai.intellij.changes

import com.intellij.ide.projectView.PresentationData
import com.intellij.ide.projectView.ProjectViewNode
import com.intellij.ide.projectView.ProjectViewNodeDecorator
import com.intellij.openapi.components.serviceIfCreated
import com.intellij.openapi.vcs.FileStatus
import com.intellij.ui.SimpleTextAttributes

/**
 * A letter in the project view on every file the agent changed in the
 * conversation: `M`, or `A` for one it created, in the version control colours.
 * What the VS Code extension shows in the explorer.
 */
internal class ChangeDecorator : ProjectViewNodeDecorator {
    override fun decorate(node: ProjectViewNode<*>, data: PresentationData) {
        val project = node.project ?: return
        val file = node.virtualFile?.takeUnless { it.isDirectory } ?: return
        // Nothing to mark until the agent has been used in this project.
        val created = project.serviceIfCreated<SessionChanges>()?.decoration(file) ?: return
        val color = (if (created) FileStatus.ADDED else FileStatus.MODIFIED).color
        // The name is drawn from the fragments once there are any: keep it first.
        if (data.coloredText.isEmpty()) {
            data.addText(data.presentableText ?: file.name, SimpleTextAttributes.REGULAR_ATTRIBUTES)
        }
        data.addText(
            if (created) "  A" else "  M",
            SimpleTextAttributes(SimpleTextAttributes.STYLE_BOLD, color),
        )
        data.tooltip =
            if (created) "Created by Code-AI in this conversation" else "Changed by Code-AI in this conversation"
    }
}
