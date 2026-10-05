package com.codeai.intellij.web

import com.intellij.ide.plugins.PluginManagerCore
import com.intellij.openapi.application.ApplicationInfo
import com.intellij.openapi.extensions.PluginId
import com.intellij.ui.jcef.JBCefApp

/**
 * The IDE's embedded browser (JCEF), which the plugin's panels are drawn in,
 * and whether the IDE at hand has one.
 *
 * Up to 2025.3 the browser's classes are part of every IDE, and the browser
 * itself part of the Java runtime the IDE runs on: the one Android Studio
 * ships has none. From 2026.1 the browser is a plugin of JetBrains' own, Web
 * Browser (JCEF), with its own Chromium inside, and from 2026.2 the classes
 * are that plugin's too. JetBrains' IDEs come with it; Android Studio does
 * not, and gets it from the Marketplace. plugin.xml names it as an optional
 * dependency, which is what lets this plugin see its classes when they are
 * its own.
 */
internal object EmbeddedBrowser {
    /** JetBrains' plugin that carries the browser from 2026.1 on. */
    val PLUGIN: PluginId = PluginId.getId("com.intellij.modules.jcef")

    /** The first release, as builds are numbered, whose browser is that plugin: 2026.1. */
    private const val FIRST_WITH_PLUGIN = 261

    /** That plugin's name, as the IDE's plugin list shows it. */
    const val PLUGIN_NAME = "Web Browser (JCEF)"

    enum class State {
        /** A panel can be shown. */
        READY,

        /** The IDE has no browser: the plugin it comes in is not installed, or is switched off. */
        NOT_INSTALLED,

        /** The browser is the Java runtime's, and the runtime the IDE was started with has none. */
        NO_RUNTIME,

        /** The browser is installed and does not run here. */
        NOT_RUNNING,
    }

    fun state(): State = when {
        present() && Api.supported() -> State.READY
        ApplicationInfo.getInstance().build.baselineVersion < FIRST_WITH_PLUGIN -> State.NO_RUNTIME
        pluginLoaded() -> State.NOT_RUNNING
        else -> State.NOT_INSTALLED
    }

    private fun pluginLoaded(): Boolean =
        PluginManagerCore.getPlugin(PLUGIN) != null && !PluginManagerCore.isDisabled(PLUGIN)

    /**
     * Whether the browser's classes are there. Asked by name: in an IDE
     * without them a mention of one is an error, wherever it is run into.
     */
    private fun present(): Boolean = try {
        Class.forName("com.intellij.ui.jcef.JBCefApp", false, EmbeddedBrowser::class.java.classLoader)
        true
    } catch (_: ClassNotFoundException) {
        false
    } catch (_: LinkageError) {
        false
    }

    /** What names the browser's classes: a class of its own, loaded only once they are known to be there. */
    private object Api {
        fun supported(): Boolean = JBCefApp.isSupported()
    }
}
