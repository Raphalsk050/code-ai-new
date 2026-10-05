package com.codeai.intellij.chat

import com.intellij.openapi.actionSystem.ActionManager
import com.intellij.openapi.actionSystem.ActionPlaces
import com.intellij.ui.dsl.builder.panel
import com.intellij.util.ui.JBUI
import javax.swing.JComponent

/**
 * What the tool window shows when the IDE runs on a Java runtime without the
 * embedded browser. Android Studio ships such a runtime; the IDE itself can
 * swap it for one that has the browser, so the panel says how instead of
 * staying blank.
 */
internal object MissingBrowser {
    /** The IDE's own "Choose Boot Java Runtime for the IDE…" action. */
    private const val CHOOSE_RUNTIME = "ChooseRuntime"

    fun panel(): JComponent {
        lateinit var content: JComponent
        content = panel {
            row { label("Code-AI needs the IDE's embedded browser").bold() }
            row {
                text(
                    "The panel is drawn in a browser built into the Java runtime the IDE runs on (JCEF). " +
                        "The runtime this IDE was started with does not include it, which is how " +
                        "Android Studio ships."
                )
            }
            row {
                text(
                    "The IDE can switch to a runtime that has it. Choose one marked <b>JCEF</b>, " +
                        "let the IDE download it and restart: Code-AI opens here afterwards. " +
                        "It is done once, and the same menu puts the original runtime back."
                )
            }
            row {
                val chooser = ActionManager.getInstance().getAction(CHOOSE_RUNTIME)
                button("Choose a Runtime with JCEF…") {
                    ActionManager.getInstance().tryToExecute(chooser, null, content, ActionPlaces.TOOLWINDOW_CONTENT, true)
                }.enabled(chooser != null)
            }
            row {
                comment("Also reachable from Help | Find Action: \"Choose Boot Java Runtime for the IDE\".")
            }
        }.apply { border = JBUI.Borders.empty(16) }
        return content
    }
}
