package com.codeai.intellij

import com.intellij.openapi.components.Service

/**
 * Inline code hints (ghost text in the editor): whether the backend has them
 * on, and with which model. Mirrored from the backend's settings by the chat
 * host; read by whatever offers the hints.
 */
@Service(Service.Level.PROJECT)
internal class InlineHints {
    @Volatile
    var enabled = false

    @Volatile
    var model = ""
}
