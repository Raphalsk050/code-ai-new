package com.codeai.intellij.web

import com.google.gson.JsonArray
import com.google.gson.JsonParser
import com.intellij.openapi.application.PathManager
import com.intellij.openapi.diagnostic.Logger
import com.intellij.openapi.project.Project
import java.io.IOException
import java.net.URLEncoder
import java.nio.charset.StandardCharsets
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardCopyOption
import java.util.concurrent.ScheduledFuture

/**
 * The panel's conversations, one file each, kept by the host: the counterpart
 * of the VS Code extension's src/transcript-store.ts. A save carries only what
 * changed, from the first item that differs, where the panel's own state used
 * to carry the whole history on every message. Used from the project's host
 * thread only, like everything else in the chat host.
 *
 * @param later runs a task after a delay on that same thread
 */
internal class TranscriptStore(
    project: Project,
    private val later: (Long, () -> Unit) -> ScheduledFuture<*>?,
) {
    private val dir: Path =
        PathManager.getSystemDir().resolve("code-ai").resolve(project.locationHash).resolve("transcripts")
    private val items = HashMap<String, JsonArray>()
    private val due = HashMap<String, ScheduledFuture<*>>()

    private fun file(id: String): Path = dir.resolve(URLEncoder.encode(id, StandardCharsets.UTF_8) + ".json")

    /** A conversation's items, or null when there is nothing kept for it. */
    fun load(id: String): JsonArray? {
        items[id]?.let { return it }
        val file = file(id)
        return try {
            if (!Files.isRegularFile(file)) {
                null
            } else {
                JsonParser.parseString(Files.readString(file))
                    .takeIf { it.isJsonArray }
                    ?.asJsonArray
                    ?.also { items[id] = it }
            }
        } catch (e: IOException) {
            LOG.warn("Could not read a Code-AI conversation", e)
            null
        } catch (_: RuntimeException) {
            // Cut short by a crash: nothing kept, and the backend rebuilds it.
            null
        }
    }

    /**
     * Replace everything from `from` on with `changed`. False when fewer than
     * `from` items are kept (a write lost to a crash): the panel sends it all.
     */
    fun save(id: String, from: Int, changed: JsonArray): Boolean {
        val kept = if (from == 0) JsonArray() else load(id) ?: return false
        if (kept.size() < from) return false
        while (kept.size() > from) kept.remove(kept.size() - 1)
        kept.addAll(changed)
        items[id] = kept
        schedule(id)
        return true
    }

    /** Conversations an older panel kept in its own state, taken over where none is kept yet. */
    fun adopt(conversations: JsonArray) {
        for (entry in conversations) {
            if (!entry.isJsonObject) continue
            val conversation = entry.asJsonObject
            val id = conversation.get("id")?.takeIf { it.isJsonPrimitive }?.asString ?: continue
            val kept = conversation.get("items")?.takeIf { it.isJsonArray }?.asJsonArray ?: continue
            if (kept.size() == 0 || load(id) != null) continue
            items[id] = kept
            schedule(id)
        }
    }

    fun remove(id: String) {
        due.remove(id)?.cancel(false)
        items.remove(id)
        try {
            Files.deleteIfExists(file(id))
        } catch (e: IOException) {
            LOG.warn("Could not delete a Code-AI conversation", e)
        }
    }

    /** Write whatever is waiting now: the project is closing. */
    fun flush() {
        for (id in due.keys.toList()) write(id)
    }

    private fun schedule(id: String) {
        if (due.containsKey(id)) return
        later(WRITE_AFTER_MS) { write(id) }?.let { due[id] = it }
    }

    /** Whole or not at all: written beside the file, then moved over it. */
    private fun write(id: String) {
        due.remove(id)?.cancel(false)
        val kept = items[id] ?: return
        val file = file(id)
        try {
            Files.createDirectories(dir)
            val draft = file.resolveSibling("${file.fileName}.tmp")
            Files.writeString(draft, kept.toString())
            try {
                Files.move(draft, file, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE)
            } catch (_: IOException) {
                Files.move(draft, file, StandardCopyOption.REPLACE_EXISTING)
            }
        } catch (e: IOException) {
            LOG.warn("Could not save a Code-AI conversation", e)
        }
    }

    private companion object {
        /** How long a conversation's changes are gathered before they are written. */
        const val WRITE_AFTER_MS = 500L
        val LOG = Logger.getInstance(TranscriptStore::class.java)
    }
}
