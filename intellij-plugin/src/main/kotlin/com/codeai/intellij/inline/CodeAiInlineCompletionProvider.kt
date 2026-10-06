package com.codeai.intellij.inline

import com.codeai.intellij.InlineHints
import com.codeai.intellij.bridge.BridgeSession
import com.codeai.intellij.editor.LanguageIds
import com.codeai.intellij.editor.Workspace
import com.codeai.intellij.util.json
import com.codeai.intellij.util.text
import com.google.gson.JsonObject
import com.intellij.codeInsight.inline.completion.DebouncedInlineCompletionProvider
import com.intellij.codeInsight.inline.completion.InlineCompletionEvent
import com.intellij.codeInsight.inline.completion.InlineCompletionProviderID
import com.intellij.codeInsight.inline.completion.InlineCompletionRequest
import com.intellij.codeInsight.inline.completion.elements.InlineCompletionGrayTextElement
import com.intellij.codeInsight.inline.completion.suggestion.InlineCompletionSingleSuggestion
import com.intellij.codeInsight.inline.completion.suggestion.InlineCompletionSuggestion
import com.intellij.openapi.application.readAction
import com.intellij.openapi.components.serviceIfCreated
import com.intellij.openapi.diagnostic.Logger
import com.intellij.openapi.editor.Editor
import com.intellij.openapi.fileEditor.FileDocumentManager
import kotlin.coroutines.cancellation.CancellationException
import kotlin.time.Duration
import kotlin.time.Duration.Companion.milliseconds
import kotlinx.coroutines.future.await

/**
 * Inline code hints: ghost text at the caret, from the same backend and the
 * same model setting as the VS Code extension's. While the backend's
 * `inline_hints_enabled` is on, the code around the caret goes to the bridge
 * and the model's completion comes back as the suggestion.
 */
internal class CodeAiInlineCompletionProvider : DebouncedInlineCompletionProvider() {
    override val id: InlineCompletionProviderID get() = InlineCompletionProviderID("CodeAi")

    override fun isEnabled(event: InlineCompletionEvent): Boolean {
        val editor: Editor = when (event) {
            is InlineCompletionEvent.DocumentChange -> event.editor
            is InlineCompletionEvent.DirectCall -> event.editor
            else -> return false
        }
        val project = editor.project ?: return false
        // Off until the chat has been opened and has read the setting, as in VS Code.
        if (project.serviceIfCreated<InlineHints>()?.enabled != true) return false
        return FileDocumentManager.getInstance().getFile(editor.document)?.isInLocalFileSystem == true
    }

    /** A short pause: a keystroke calls the request off before it reaches the model. */
    override suspend fun getDebounceDelay(request: InlineCompletionRequest): Duration = DEBOUNCE

    override suspend fun getSuggestionDebounced(request: InlineCompletionRequest): InlineCompletionSuggestion {
        val editor = request.editor
        val project = editor.project ?: return InlineCompletionSuggestion.Empty
        val client = BridgeSession.of(project).client ?: return InlineCompletionSuggestion.Empty
        val params = readAction {
            val file = FileDocumentManager.getInstance().getFile(request.document) ?: return@readAction null
            val text = request.document.immutableCharSequence
            val caret = request.endOffset.coerceIn(0, text.length)
            val prefix = text.subSequence(0, caret).toString()
            if (prefix.isBlank()) return@readAction null
            json(
                "prefix" to prefix,
                "suffix" to text.subSequence(caret, text.length).toString(),
                "path" to Workspace.relative(project, file),
                "language" to LanguageIds.of(file),
            )
        } ?: return InlineCompletionSuggestion.Empty

        val completion = try {
            (client.request("inlineComplete", params, AI_REQUEST_TIMEOUT_MS).await() as? JsonObject)?.text("completion")
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            LOG.info("Code-AI inline hint failed: ${e.message}")
            null
        }
        if (completion.isNullOrBlank()) return InlineCompletionSuggestion.Empty
        return InlineCompletionSingleSuggestion.build { emit(InlineCompletionGrayTextElement(completion)) }
    }

    private companion object {
        val LOG = Logger.getInstance(CodeAiInlineCompletionProvider::class.java)
        val DEBOUNCE = 250.milliseconds

        // The hint goes through the model, which the bridge bounds itself.
        const val AI_REQUEST_TIMEOUT_MS = 300_000L
    }
}
