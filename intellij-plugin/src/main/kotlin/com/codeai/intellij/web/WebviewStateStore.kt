package com.codeai.intellij.web

import com.google.gson.JsonParser
import com.intellij.openapi.application.PathManager
import com.intellij.openapi.diagnostic.Logger
import com.intellij.openapi.project.Project
import java.io.IOException
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardCopyOption

/**
 * What a panel saved with `setState`: its conversations and preferences, kept
 * per project across restarts, as VS Code keeps a webview's state per
 * workspace. It can run to megabytes, so it lives in a file of its own under
 * the IDE's system directory rather than in the project's settings.
 */
internal class WebviewStateStore(project: Project, name: String) {
    private val file: Path =
        PathManager.getSystemDir().resolve("code-ai").resolve(project.locationHash).resolve("$name-state.json")

    /** The saved state as JSON, or null when there is none worth reading. */
    fun load(): String? = try {
        if (!Files.isRegularFile(file)) {
            null
        } else {
            // A file cut short by a crash is no state at all: better none than a page that cannot start.
            Files.readString(file).takeIf { text -> JsonParser.parseString(text).let { !it.isJsonNull } }
        }
    } catch (e: IOException) {
        LOG.warn("Could not read the Code-AI panel state", e)
        null
    } catch (_: RuntimeException) {
        null
    }

    /** Replace the saved state. Whole or not at all: it is written beside the file, then moved over it. */
    @Synchronized
    fun save(json: String) {
        try {
            Files.createDirectories(file.parent)
            val draft = file.resolveSibling("${file.fileName}.tmp")
            Files.writeString(draft, json)
            try {
                Files.move(draft, file, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE)
            } catch (_: IOException) {
                // No atomic move here, or something is holding the file: settle for a plain one.
                Files.move(draft, file, StandardCopyOption.REPLACE_EXISTING)
            }
        } catch (e: IOException) {
            LOG.warn("Could not save the Code-AI panel state", e)
        }
    }

    private companion object {
        val LOG = Logger.getInstance(WebviewStateStore::class.java)
    }
}
