package com.codeai.intellij.settings

import com.intellij.openapi.options.BoundConfigurable
import com.intellij.openapi.ui.DialogPanel
import com.intellij.ui.dsl.builder.AlignX
import com.intellij.ui.dsl.builder.bindSelected
import com.intellij.ui.dsl.builder.bindText
import com.intellij.ui.dsl.builder.panel
import com.intellij.util.execution.ParametersListUtil

/**
 * Settings | Tools | Code-AI: the plugin's own settings. The same three the
 * panel's settings screen edits, for whoever looks for them in the IDE.
 */
internal class CodeAiConfigurable : BoundConfigurable("Code-AI") {
    private var command = ""
    private var args = ""
    private var diffInEditor = true

    override fun createPanel(): DialogPanel {
        val settings = CodeAiSettings.get()
        command = settings.command
        args = ParametersListUtil.join(settings.args)
        diffInEditor = settings.diffInEditor
        return panel {
            group("Backend") {
                row("Command:") {
                    textField().bindText(::command).align(AlignX.FILL)
                        .comment(
                            "Executable that launches the agent; the plugin appends <code>bridge</code>. " +
                                "Left at <code>code-ai</code>, the plugin uses the backend it ships with, " +
                                "then a <code>.venv</code> in the project, then <code>code-ai</code> on PATH."
                        )
                }
                row("Arguments:") {
                    textField().bindText(::args).align(AlignX.FILL)
                        .comment(
                            "Inserted before <code>bridge</code>, e.g. <code>--config /path/config.json</code>. " +
                                "To run from source: command <code>python</code>, arguments <code>-m code_ai</code>."
                        )
                }
            }
            group("Edits") {
                row {
                    checkBox("Show the agent's file edits in the diff viewer as they are written")
                        .bindSelected(::diffInEditor)
                        .comment("Off: the edits are shown inside the Code-AI panel instead.")
                }
            }
            row { comment("A changed command or arguments take effect with Code-AI: Restart.") }
        }
    }

    override fun apply() {
        super.apply()
        CodeAiSettings.get().update(
            command = command.trim(),
            args = ParametersListUtil.parse(args),
            diffInEditor = diffInEditor,
        )
    }
}
