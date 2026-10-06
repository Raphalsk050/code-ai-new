package com.codeai.intellij.web

import com.intellij.execution.process.ConsoleHighlighter
import com.intellij.execution.ui.ConsoleViewContentType
import com.intellij.openapi.editor.colors.EditorColorsManager
import com.intellij.openapi.util.SystemInfo
import com.intellij.ui.JBColor
import com.intellij.util.ui.JBUI
import com.intellij.util.ui.UIUtil
import java.awt.Color
import kotlin.math.roundToInt

/**
 * The IDE's look, in the vocabulary the panel was written against.
 *
 * The panel takes every colour and font from the `--vscode-*` variables VS Code
 * gives a webview, so that it blends with the user's theme. Here they are filled
 * in from the IDE's own theme and editor colour scheme instead: the same panel,
 * dressed for the window it is in.
 */
internal object ThemeVariables {
    /** Whether the IDE's theme is a dark one. */
    fun isDark(): Boolean = !JBColor.isBright()

    /** The class VS Code puts on a webview's body for the theme's kind. */
    fun kind(): String = if (isDark()) "vscode-dark" else "vscode-light"

    /** The colour behind the panel, for the browser to paint before the page has loaded. */
    fun background(): String = css(EditorColorsManager.getInstance().globalScheme.defaultBackground)

    /** A `:root` rule defining every variable. Reads the theme: call it on the EDT. */
    fun stylesheet(): String =
        variables().entries.joinToString(separator = "\n", prefix = ":root {\n", postfix = "\n}") { (name, value) ->
            "  --vscode-$name: $value;"
        } + "\n:root { color-scheme: ${if (isDark()) "dark" else "light"}; --monaco-monospace-font: ${monoStack()}; " +
            "--text-link-decoration: none; }"

    internal fun variables(): Map<String, String> {
        val dark = isDark()
        val scheme = EditorColorsManager.getInstance().globalScheme
        val foreground = UIUtil.getLabelForeground()
        val editorForeground = scheme.defaultForeground
        val button = JBUI.CurrentTheme.Button.defaultButtonColorStart()
        val link = JBUI.CurrentTheme.Link.Foreground.ENABLED
        return linkedMapOf(
            // type
            "font-family" to uiStack(),
            "font-weight" to "normal",
            "font-size" to "${JBUI.Fonts.label().size}px",
            "editor-font-family" to "${quoted(scheme.editorFontName)}, ${monoStack()}",
            "editor-font-size" to "${scheme.editorFontSize}px",
            "editor-font-weight" to "normal",
            // surfaces
            "editor-background" to css(scheme.defaultBackground),
            "sideBar-background" to css(JBUI.CurrentTheme.ToolWindow.background()),
            "editorWidget-background" to css(JBUI.CurrentTheme.Popup.BACKGROUND),
            "input-background" to css(UIUtil.getTextFieldBackground()),
            "list-hoverBackground" to css(JBUI.CurrentTheme.List.Hover.background(true)),
            "editor-inactiveSelectionBackground" to css(UIUtil.getListSelectionBackground(false)),
            "textCodeBlock-background" to css(withAlpha(editorForeground, 0.10)),
            "textPreformat-background" to css(withAlpha(editorForeground, 0.12)),
            "textBlockQuote-background" to css(withAlpha(editorForeground, 0.06)),
            "terminal-background" to css(
                scheme.getColor(ConsoleViewContentType.CONSOLE_BACKGROUND_KEY) ?: scheme.defaultBackground
            ),
            "widget-shadow" to if (dark) "rgba(0, 0, 0, 0.36)" else "rgba(0, 0, 0, 0.16)",
            // lines
            "panel-border" to css(JBColor.border()),
            "input-border" to css(JBColor.namedColor("Component.borderColor", JBColor.border())),
            "focusBorder" to css(JBUI.CurrentTheme.Focus.focusColor()),
            "textBlockQuote-border" to css(withAlpha(link, 0.5)),
            // text
            "foreground" to css(foreground),
            "editor-foreground" to css(editorForeground),
            "input-foreground" to css(UIUtil.getTextFieldForeground()),
            "descriptionForeground" to css(UIUtil.getLabelInfoForeground()),
            "textPreformat-foreground" to css(editorForeground),
            // intent
            "textLink-foreground" to css(link),
            "textLink-activeForeground" to css(JBUI.CurrentTheme.Link.Foreground.HOVERED),
            "button-background" to css(button),
            "button-hoverBackground" to css(blend(button, if (dark) Color.WHITE else Color.BLACK, 0.10)),
            "button-foreground" to css(JBUI.CurrentTheme.Button.defaultButtonForeground()),
            "testing-iconPassed" to css(JBColor.namedColor("Label.successForeground", JBColor(0x368746, 0x5FAD65))),
            "editorWarning-foreground" to css(JBUI.CurrentTheme.Label.warningForeground()),
            "errorForeground" to css(JBUI.CurrentTheme.Label.errorForeground()),
            "terminal-ansiMagenta" to css(
                scheme.getAttributes(ConsoleHighlighter.MAGENTA)?.foregroundColor ?: JBColor(0xBC05BC, 0xBC3FBC)
            ),
            // the scrollbar and key caps VS Code styles in every webview
            "scrollbarSlider-background" to if (dark) "rgba(121, 121, 121, 0.4)" else "rgba(100, 100, 100, 0.4)",
            "scrollbarSlider-hoverBackground" to "rgba(100, 100, 100, 0.7)",
            "scrollbarSlider-activeBackground" to if (dark) "rgba(191, 191, 191, 0.4)" else "rgba(0, 0, 0, 0.6)",
            "keybindingLabel-background" to "rgba(128, 128, 128, 0.17)",
            "keybindingLabel-foreground" to css(foreground),
            "keybindingLabel-border" to if (dark) "rgba(51, 51, 51, 0.6)" else "rgba(204, 204, 204, 0.4)",
            "keybindingLabel-bottomBorder" to if (dark) "rgba(68, 68, 68, 0.6)" else "rgba(187, 187, 187, 0.4)",
        )
    }

    /** The interface font VS Code uses on each system: the page cannot see the IDE's own. */
    private fun uiStack(): String = when {
        SystemInfo.isMac -> "-apple-system, BlinkMacSystemFont, sans-serif"
        SystemInfo.isWindows -> "\"Segoe WPC\", \"Segoe UI\", sans-serif"
        else -> "system-ui, \"Ubuntu\", \"Droid Sans\", sans-serif"
    }

    private fun monoStack(): String = when {
        SystemInfo.isMac -> "Menlo, Monaco, \"Courier New\", monospace"
        SystemInfo.isWindows -> "Consolas, \"Courier New\", monospace"
        else -> "\"Droid Sans Mono\", \"DejaVu Sans Mono\", monospace"
    }

    private fun quoted(family: String): String = "\"" + family.replace("\\", "").replace("\"", "") + "\""

    internal fun css(color: Color): String =
        if (color.alpha == 255) "#%02x%02x%02x".format(color.red, color.green, color.blue)
        else "rgba(${color.red}, ${color.green}, ${color.blue}, ${(color.alpha / 255.0 * 1000).roundToInt() / 1000.0})"

    private fun withAlpha(color: Color, alpha: Double): Color =
        Color(color.red, color.green, color.blue, (alpha * 255).roundToInt())

    /** `color` moved `share` of the way towards `other`. */
    private fun blend(color: Color, other: Color, share: Double): Color = Color(
        (color.red + (other.red - color.red) * share).roundToInt(),
        (color.green + (other.green - color.green) * share).roundToInt(),
        (color.blue + (other.blue - color.blue) * share).roundToInt(),
        color.alpha,
    )
}
