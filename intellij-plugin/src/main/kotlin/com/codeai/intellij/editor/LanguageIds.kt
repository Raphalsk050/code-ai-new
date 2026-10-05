package com.codeai.intellij.editor

import com.intellij.openapi.fileTypes.LanguageFileType
import com.intellij.openapi.fileTypes.PlainTextFileType
import com.intellij.openapi.vfs.VirtualFile

/**
 * The language id VS Code would give a file. The backend and the panel were
 * written against those names (they fence code with them, and prompts quote
 * them), so the IDE's own, which differ by product, are translated here.
 */
internal object LanguageIds {
    private val BY_EXTENSION = mapOf(
        "kt" to "kotlin", "kts" to "kotlin", "java" to "java", "groovy" to "groovy", "gradle" to "groovy",
        "scala" to "scala", "py" to "python", "pyi" to "python", "rb" to "ruby", "php" to "php",
        "js" to "javascript", "mjs" to "javascript", "cjs" to "javascript", "jsx" to "javascriptreact",
        "ts" to "typescript", "mts" to "typescript", "cts" to "typescript", "tsx" to "typescriptreact",
        "json" to "json", "jsonc" to "jsonc", "json5" to "json5", "xml" to "xml", "xsd" to "xml", "xsl" to "xsl",
        "html" to "html", "htm" to "html", "css" to "css", "scss" to "scss", "sass" to "sass", "less" to "less",
        "vue" to "vue", "svelte" to "svelte", "md" to "markdown", "markdown" to "markdown", "tex" to "latex",
        "c" to "c", "h" to "c", "cpp" to "cpp", "cc" to "cpp", "cxx" to "cpp", "hpp" to "cpp", "hh" to "cpp",
        "hxx" to "cpp", "m" to "objective-c", "mm" to "objective-cpp", "cs" to "csharp", "fs" to "fsharp",
        "vb" to "vb", "go" to "go", "rs" to "rust", "swift" to "swift", "dart" to "dart", "lua" to "lua",
        "pl" to "perl", "pm" to "perl", "r" to "r", "jl" to "julia", "ex" to "elixir", "exs" to "elixir",
        "erl" to "erlang", "hs" to "haskell", "clj" to "clojure", "sh" to "shellscript", "bash" to "shellscript",
        "zsh" to "shellscript", "fish" to "fish", "ps1" to "powershell", "bat" to "bat", "cmd" to "bat",
        "sql" to "sql", "yml" to "yaml", "yaml" to "yaml", "toml" to "toml", "ini" to "ini", "cfg" to "ini",
        "properties" to "properties", "proto" to "proto", "graphql" to "graphql", "gql" to "graphql",
        "tf" to "terraform", "diff" to "diff", "patch" to "diff", "log" to "log", "txt" to "plaintext",
        "aidl" to "aidl", "pro" to "proguard", "cmake" to "cmake",
    )
    private val BY_NAME = mapOf(
        "dockerfile" to "dockerfile", "makefile" to "makefile", "cmakelists.txt" to "cmake",
        "gemfile" to "ruby", "rakefile" to "ruby", ".gitignore" to "ignore", ".env" to "dotenv",
    )

    fun of(file: VirtualFile): String {
        BY_NAME[file.name.lowercase()]?.let { return it }
        file.extension?.lowercase()?.let(BY_EXTENSION::get)?.let { return it }
        val type = file.fileType
        if (type is PlainTextFileType) return "plaintext"
        // Unknown here: the IDE's own name for it is the best there is.
        return ((type as? LanguageFileType)?.language?.id ?: type.name).lowercase().replace(' ', '-')
    }
}
