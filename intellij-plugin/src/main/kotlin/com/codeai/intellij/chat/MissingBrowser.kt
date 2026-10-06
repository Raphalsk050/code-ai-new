package com.codeai.intellij.chat

import com.codeai.intellij.Notify
import com.codeai.intellij.web.EmbeddedBrowser
import com.intellij.ide.plugins.PluginManagerCore
import com.intellij.openapi.actionSystem.ActionManager
import com.intellij.openapi.actionSystem.ActionPlaces
import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.application.ApplicationNamesInfo
import com.intellij.openapi.project.Project
import com.intellij.openapi.updateSettings.impl.pluginsAdvertisement.installAndEnable
import com.intellij.ui.dsl.builder.panel
import com.intellij.util.ui.JBUI
import javax.swing.JComponent

/**
 * What the tool window shows in an IDE that cannot draw the panel: which part
 * of the embedded browser is missing, and the IDE's own way of getting it.
 * Android Studio is such an IDE as it is installed, so this is the first thing
 * most of its users see of the plugin.
 */
internal object MissingBrowser {
    /** The IDE's own "Choose Boot Java Runtime for the IDE…" action. */
    private const val CHOOSE_RUNTIME = "ChooseRuntime"

    fun panel(project: Project, state: EmbeddedBrowser.State): JComponent = when (state) {
        EmbeddedBrowser.State.NOT_INSTALLED -> plugin(project)
        EmbeddedBrowser.State.NO_RUNTIME -> runtime()
        else -> stopped()
    }

    /** From 2026.1: the browser is a plugin, and the IDE does not have it. */
    private fun plugin(project: Project): JComponent {
        val ide = ApplicationNamesInfo.getInstance().fullProductName
        val name = EmbeddedBrowser.PLUGIN_NAME
        val off = PluginManagerCore.isPluginInstalled(EmbeddedBrowser.PLUGIN)
        return panel {
            row { label("Code-AI needs the IDE's embedded browser").bold() }
            row {
                text(
                    "The panel is drawn in a browser built into the IDE. It is a plugin of JetBrains' own, " +
                        "<b>$name</b>, " +
                        if (off) "which is switched off here." else "which $ide does not come with."
                )
            }
            row {
                text(
                    (if (off) "Switch it on" else "Install it, as any other plugin from the Marketplace,") +
                        " and restart the IDE: Code-AI opens here afterwards. It is done once."
                )
            }
            row {
                button(if (off) "Enable $name" else "Install $name") {
                    installAndEnable(project, setOf(EmbeddedBrowser.PLUGIN), true) { installed(project) }
                }
            }
            row {
                comment("Also under Settings | Plugins: search for \"$name\".")
            }
        }.apply { border = JBUI.Borders.empty(16) }
    }

    /** The browser plugin went in: it starts with the IDE, so the IDE has to start again. */
    private fun installed(project: Project) {
        Notify.info(
            project,
            "${EmbeddedBrowser.PLUGIN_NAME} is installed. Restart the IDE, and Code-AI opens in its tool window.",
            "Restart" to { ApplicationManager.getApplication().restart() },
        )
    }

    /** Up to 2025.3: the browser is the Java runtime's, and the IDE runs on one without it. */
    private fun runtime(): JComponent {
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

    /** The browser is installed and the IDE will not start it: only the IDE knows why. */
    private fun stopped(): JComponent = panel {
        row { label("Code-AI needs the IDE's embedded browser").bold() }
        row {
            text(
                "${EmbeddedBrowser.PLUGIN_NAME} is installed, and the IDE is not running it. " +
                    "It may be switched off, or suspended by the IDE: a notification says so when it is, " +
                    "and offers the way to bring it back."
            )
        }
        row {
            comment("Help | Show Log has the reason: search it for \"JCEF\". Restart the IDE once it is dealt with.")
        }
    }.apply { border = JBUI.Borders.empty(16) }
}
