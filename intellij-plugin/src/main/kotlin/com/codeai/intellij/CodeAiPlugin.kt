package com.codeai.intellij

import com.intellij.ide.plugins.PluginManagerCore
import com.intellij.openapi.extensions.PluginId
import java.nio.file.Path

/** Facts about the installed plugin itself. */
internal object CodeAiPlugin {
    const val ID = "com.codeai.intellij"

    /** The tool window the chat lives in, as plugin.xml declares it. */
    const val TOOL_WINDOW = "Code-AI"

    /** The plugin's own directory: where a packaged build carries the backend. */
    fun path(): Path? = PluginManagerCore.getPlugin(PluginId.getId(ID))?.pluginPath

    fun version(): String = PluginManagerCore.getPlugin(PluginId.getId(ID))?.version ?: ""
}
