package com.codeai.intellij.bridge

// Which executable serves `code-ai bridge`. A port of the VS Code extension's
// src/resolve-command.ts: keep the two in step.

/**
 * Where the bridge executable came from. Surfaced in the UI so a failed spawn
 * says which of the paths was taken instead of only which name failed.
 */
internal enum class CommandSource { CONFIGURED, BUNDLED, VENV, PATH }

internal data class ResolvedCommand(
    val command: String,
    val source: CommandSource,
    /** Set when the configured command was overridden, saying what and why. */
    val ignoredConfig: String? = null,
)

/** The filesystem calls this file makes, injected so tests need no disk. */
internal interface ResolveHost {
    val windows: Boolean
    fun exists(file: String): Boolean
}

/** The value of the command setting that means "find it yourself". */
internal const val DEFAULT_COMMAND = "code-ai"

/**
 * A script is not an executable. A process cannot be started from one, so a
 * setting pointing at `__main__.py` is a configuration that can never work,
 * not a preference to honour.
 */
private val SCRIPT = Regex("""\.(py|pyw)$""", RegexOption.IGNORE_CASE)

/** Basename of the frozen binary the plugin carries, per platform. */
internal fun binaryName(windows: Boolean): String = if (windows) "code-ai.exe" else "code-ai"

private fun join(windows: Boolean, root: String, vararg parts: String): String {
    val separator = if (windows) "\\" else "/"
    return (listOf(root.trimEnd('/', '\\')) + parts).joinToString(separator)
}

/**
 * Path of the binary staged inside the plugin by its build. Kept in one place
 * because the packaging and the plugin have to agree on it; a mismatch is a
 * plugin that installs and never runs.
 */
internal fun bundledBinaryPath(pluginPath: String, windows: Boolean): String =
    join(windows, pluginPath, "bin", binaryName(windows))

/**
 * A virtualenv's executable under a project root. Windows venvs put console
 * scripts in `Scripts\` with an `.exe` shim; POSIX ones in `bin/` with no
 * extension.
 */
private fun venvCandidate(root: String, windows: Boolean): String =
    if (windows) join(true, root, ".venv", "Scripts", "code-ai.exe") else join(false, root, ".venv", "bin", "code-ai")

/**
 * Resolve the executable that serves `code-ai bridge`.
 *
 * Order matters. The binary the plugin ships wins over a virtualenv so that a
 * first-time install works with nothing else present; a developer who wants
 * their working tree instead sets the command explicitly, which wins over
 * everything. The bare name is the last resort.
 *
 * @param configured the command setting, verbatim
 * @param pluginPath the installed plugin's own directory, when it has one
 * @param workspaceRoots the project's roots, searched for a dev virtualenv
 */
internal fun resolveCommand(
    configured: String,
    pluginPath: String?,
    workspaceRoots: List<String>,
    host: ResolveHost,
): ResolvedCommand {
    val trimmed = configured.trim()
    var ignoredConfig: String? = null
    if (trimmed.isNotEmpty() && trimmed != DEFAULT_COMMAND) {
        if (!SCRIPT.containsMatchIn(trimmed)) return ResolvedCommand(trimmed, CommandSource.CONFIGURED)
        // Falling through rather than failing: the setting is unusable whatever
        // is done with it, and the bundled backend is right there.
        ignoredConfig =
            "The Code-AI command points at a Python script ($trimmed), which cannot be launched " +
                "directly. Using the backend shipped with the plugin instead. Clear the setting, or " +
                "set it to \"python\" with the arguments [\"-m\", \"code_ai\"] to run from source."
    }

    if (pluginPath != null) {
        val bundled = bundledBinaryPath(pluginPath, host.windows)
        if (host.exists(bundled)) return ResolvedCommand(bundled, CommandSource.BUNDLED, ignoredConfig)
    }
    for (root in workspaceRoots) {
        val candidate = venvCandidate(root, host.windows)
        if (host.exists(candidate)) return ResolvedCommand(candidate, CommandSource.VENV, ignoredConfig)
    }
    return ResolvedCommand(DEFAULT_COMMAND, CommandSource.PATH, ignoredConfig)
}

/**
 * Human-readable reason a spawn failed, naming the path that was tried and how
 * it was chosen: what turns "code-ai not found" into something the reader can
 * act on without knowing the resolution order.
 */
internal fun spawnFailureHint(resolved: ResolvedCommand, windows: Boolean): String = when (resolved.source) {
    CommandSource.BUNDLED ->
        "the binary shipped with the plugin (${resolved.command}) could not be started. " +
            "The install may be incomplete: reinstall the plugin."
    CommandSource.CONFIGURED -> "the Code-AI command points at ${resolved.command}, which was not found."
    CommandSource.VENV -> "${resolved.command} was found but could not be started."
    CommandSource.PATH -> {
        val example =
            if (windows) "C:\\path\\to\\.venv\\Scripts\\code-ai.exe" else "/path/to/.venv/bin/code-ai"
        "this build of the plugin carries no bundled binary and no virtualenv was found, so it fell " +
            "back to \"code-ai\" on PATH. Set the command under Settings | Tools | Code-AI to the " +
            "executable (e.g. $example), or install a plugin built with the binary bundled."
    }
}
