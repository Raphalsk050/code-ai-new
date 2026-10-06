package com.codeai.intellij.settings

import com.codeai.intellij.bridge.DEFAULT_COMMAND
import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.components.PersistentStateComponent
import com.intellij.openapi.components.Service
import com.intellij.openapi.components.State
import com.intellij.openapi.components.Storage
import com.intellij.openapi.components.service
import com.intellij.util.messages.Topic
import com.intellij.util.xmlb.annotations.XCollection

/**
 * The plugin's own settings, as opposed to the backend's: the counterpart of
 * the VS Code extension's `code-ai.*` configuration. Kept per IDE, not per
 * project, as that extension writes them to the user's settings.
 */
@Service(Service.Level.APP)
@State(name = "CodeAiSettings", storages = [Storage("code-ai.xml")])
internal class CodeAiSettings : PersistentStateComponent<CodeAiSettings.Values> {
    class Values {
        /** Executable that launches the agent; the plugin appends `bridge`. */
        var command: String = DEFAULT_COMMAND

        /** Extra arguments inserted before `bridge`. */
        @XCollection(style = XCollection.Style.v2)
        var args: MutableList<String> = mutableListOf()

        /** Show the agent's file edits in the IDE's diff view as they are written. */
        var diffInEditor: Boolean = true
    }

    private var values = Values()

    override fun getState(): Values = values

    override fun loadState(state: Values) {
        values = state
    }

    val command: String get() = values.command
    val args: List<String> get() = values.args.toList()
    val diffInEditor: Boolean get() = values.diffInEditor

    /** Change what was given, leave the rest, and tell whoever shows these. */
    fun update(command: String? = null, args: List<String>? = null, diffInEditor: Boolean? = null) {
        val before = Triple(values.command, values.args.toList(), values.diffInEditor)
        command?.let { values.command = it }
        args?.let { values.args = it.toMutableList() }
        diffInEditor?.let { values.diffInEditor = it }
        if (before == Triple(values.command, values.args.toList(), values.diffInEditor)) return
        ApplicationManager.getApplication().messageBus.syncPublisher(CHANGED).settingsChanged()
    }

    fun interface Listener {
        fun settingsChanged()
    }

    companion object {
        val CHANGED: Topic<Listener> = Topic.create("Code-AI settings", Listener::class.java)

        fun get(): CodeAiSettings = service()
    }
}
