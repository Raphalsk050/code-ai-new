package com.codeai.intellij.web

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class WebviewPageTest {
    private fun page(state: String? = null, script: String = "webview.js") = WebviewPage.html(
        script = script,
        theme = ":root { --vscode-foreground: #cccccc; }",
        themeKind = "vscode-dark",
        state = state,
        toHost = "window.cefQuery_1({request: '' + payload});",
        nonce = "n0nce",
    )

    @Test
    fun `the page gives the bundle what VS Code gives a webview`() {
        val html = page()
        // The host it talks to, the stylesheet under it, the theme, and its icons.
        assertTrue("window.acquireVsCodeApi = function" in html)
        assertTrue("<style id=\"_defaultStyles\">@layer vscode-default {" in html)
        assertTrue("<style id=\"code-ai-theme\">:root { --vscode-foreground: #cccccc; }</style>" in html)
        assertTrue("<link rel=\"stylesheet\" href=\"codicons/codicon.css\" />" in html)
        // data-board: this host has the board's tab, so the chat shows its button.
        assertTrue("<body class=\"vscode-dark\" data-vscode-theme-kind=\"vscode-dark\" data-board=\"1\">" in html)
        // The body's side padding is VS Code's, and the panel is laid out on it.
        assertTrue("padding: 0 20px;" in html)
    }

    @Test
    fun `only the page's own scripts may run`() {
        val html = page(script = "flow.js")
        assertTrue("script-src 'nonce-n0nce';" in html)
        assertTrue("<script nonce=\"n0nce\" src=\"flow.js\"></script>" in html)
        assertEquals(2, Regex("<script").findAll(html).count())
        assertEquals(2, Regex("<script nonce=\"n0nce\"").findAll(html).count())
        // Nothing is fetched from anywhere but the plugin itself.
        assertTrue("default-src 'none';" in html)
    }

    @Test
    fun `the bundle talks to the plugin through the function it is handed`() {
        val html = page()
        assertTrue("window.cefQuery_1({request: '' + payload});" in html)
        assertTrue("toHost(\"message\", message);" in html)
        // Ready is announced when the bundle starts listening, not when the page has loaded.
        assertTrue("if (type === \"message\") {" in html && "toHost(\"ready\");" in html)
    }

    @Test
    fun `a saved state is handed back, and cannot break out of the script`() {
        assertTrue("var state = undefined;" in page())
        assertTrue("var state = {\"prefs\":{\"mode\":\"agent\"}};" in page("""{"prefs":{"mode":"agent"}}"""))
        // A conversation may hold any text, a closing script tag included.
        val hostile = """{"title":"</script><script>alert(1)</script>","note":"<!-- x"}"""
        val html = page(hostile)
        assertFalse("</script><script>alert(1)" in html)
        assertTrue("""{"title":"<\/script><script>alert(1)<\/script>","note":"<\!-- x"}""" in html)
        assertEquals(2, Regex("<script").findAll(html.replace("<script>alert", "")).count())
    }
}
