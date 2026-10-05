package com.codeai.intellij.bridge

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNotNull
import kotlin.test.assertTrue

class ResolveCommandTest {
    /** A fake filesystem: only the listed paths exist. */
    private fun host(windows: Boolean, vararg present: String) = object : ResolveHost {
        override val windows = windows
        override fun exists(file: String) = file in present
    }

    private val winPlugin = "C:\\Users\\dev\\AppData\\Roaming\\Google\\AndroidStudio2025.3\\plugins\\code-ai"
    private val posixPlugin = "/home/dev/.local/share/Google/AndroidStudio2025.3/code-ai"

    @Test
    fun `bundled binary is named per platform`() {
        assertEquals("code-ai.exe", binaryName(windows = true))
        assertEquals("code-ai", binaryName(windows = false))
    }

    @Test
    fun `bundled path uses the platform's separator`() {
        assertEquals("$winPlugin\\bin\\code-ai.exe", bundledBinaryPath(winPlugin, windows = true))
        assertEquals("$posixPlugin/bin/code-ai", bundledBinaryPath(posixPlugin, windows = false))
    }

    @Test
    fun `the bundled binary wins when the setting is left at the default`() {
        for ((windows, plugin) in listOf(true to winPlugin, false to posixPlugin)) {
            val bundled = bundledBinaryPath(plugin, windows)
            val resolved = resolveCommand(DEFAULT_COMMAND, plugin, emptyList(), host(windows, bundled))
            assertEquals(ResolvedCommand(bundled, CommandSource.BUNDLED), resolved)
        }
    }

    @Test
    fun `an explicit setting beats the bundled binary`() {
        val bundled = bundledBinaryPath(posixPlugin, windows = false)
        val resolved = resolveCommand(
            "/opt/code-ai/code-ai", posixPlugin, emptyList(), host(false, bundled, "/opt/code-ai/code-ai")
        )
        assertEquals(ResolvedCommand("/opt/code-ai/code-ai", CommandSource.CONFIGURED), resolved)
    }

    @Test
    fun `a Windows virtualenv is found under Scripts, not bin`() {
        val workspace = "C:\\work\\code-ai"
        val script = "$workspace\\.venv\\Scripts\\code-ai.exe"
        // The POSIX layout must not match on Windows, or the resolver reports a
        // hit that cannot be spawned.
        val posixLayout = "$workspace\\.venv\\bin\\code-ai"
        val resolved = resolveCommand(DEFAULT_COMMAND, winPlugin, listOf(workspace), host(true, script, posixLayout))
        assertEquals(ResolvedCommand(script, CommandSource.VENV), resolved)
    }

    @Test
    fun `a POSIX virtualenv is found under bin`() {
        val workspace = "/home/dev/code-ai"
        val script = "$workspace/.venv/bin/code-ai"
        val resolved = resolveCommand(DEFAULT_COMMAND, posixPlugin, listOf(workspace), host(false, script))
        assertEquals(ResolvedCommand(script, CommandSource.VENV), resolved)
    }

    @Test
    fun `project roots are searched in order`() {
        val first = "/home/dev/app"
        val second = "/home/dev/code-ai"
        val script = "$second/.venv/bin/code-ai"
        val resolved = resolveCommand(DEFAULT_COMMAND, null, listOf(first, second), host(false, script))
        assertEquals(ResolvedCommand(script, CommandSource.VENV), resolved)
    }

    @Test
    fun `with nothing on disk it falls back to the bare name`() {
        val resolved = resolveCommand(DEFAULT_COMMAND, posixPlugin, listOf("/ws"), host(false))
        assertEquals(ResolvedCommand(DEFAULT_COMMAND, CommandSource.PATH), resolved)
    }

    @Test
    fun `whitespace-only settings count as the default`() {
        val bundled = bundledBinaryPath(posixPlugin, windows = false)
        val resolved = resolveCommand("   ", posixPlugin, emptyList(), host(false, bundled))
        assertEquals(CommandSource.BUNDLED, resolved.source)
    }

    @Test
    fun `a Python script as the command is overridden by the bundled backend`() {
        // A .py cannot be started as a process. Honouring the setting would mean
        // failing on every launch with a setting nothing can make work, while
        // the shipped binary sits unused next to it.
        val script = "C:\\proj\\src\\code_ai\\__main__.py"
        val bundled = bundledBinaryPath(winPlugin, windows = true)
        val resolved = resolveCommand(script, winPlugin, emptyList(), host(true, script, bundled))
        assertEquals(bundled, resolved.command)
        assertEquals(CommandSource.BUNDLED, resolved.source)
        assertTrue("cannot be launched directly" in resolved.ignoredConfig.orEmpty())
        assertTrue("\"-m\", \"code_ai\"" in resolved.ignoredConfig.orEmpty())
    }

    @Test
    fun `the override still applies when there is no bundled backend to fall back to`() {
        val script = "/proj/src/code_ai/__main__.py"
        val resolved = resolveCommand(script, posixPlugin, emptyList(), host(false, script))
        assertEquals(DEFAULT_COMMAND, resolved.command)
        assertEquals(CommandSource.PATH, resolved.source)
        assertNotNull(resolved.ignoredConfig)
    }

    @Test
    fun `an executable path is honoured even though it looks unusual`() {
        // Only scripts are overridden; anything else is the user's call.
        val odd = "/opt/weird/code-ai-launcher"
        val resolved = resolveCommand(odd, posixPlugin, emptyList(), host(false))
        assertEquals(ResolvedCommand(odd, CommandSource.CONFIGURED), resolved)
    }

    @Test
    fun `the failure hint names the path and how it was chosen`() {
        val bundled = bundledBinaryPath(posixPlugin, windows = false)
        assertTrue("reinstall the plugin" in spawnFailureHint(ResolvedCommand(bundled, CommandSource.BUNDLED), false))
        assertTrue(
            "\\.venv\\Scripts\\code-ai.exe" in spawnFailureHint(ResolvedCommand("code-ai", CommandSource.PATH), true)
        )
        assertTrue("/.venv/bin/code-ai" in spawnFailureHint(ResolvedCommand("code-ai", CommandSource.PATH), false))
    }
}
