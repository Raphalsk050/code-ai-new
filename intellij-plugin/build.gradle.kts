import groovy.json.JsonSlurper
import java.util.Properties
import org.jetbrains.kotlin.gradle.dsl.KotlinVersion

plugins {
    id("org.jetbrains.kotlin.jvm") version "2.4.20"
    id("org.jetbrains.intellij.platform") version "2.19.0"
}

val repoRoot = layout.projectDirectory.dir("..")

/** X.Y.Z of the product: the plugin ships under it, like the VS Code extension. */
val productVersion: String = run {
    val text = providers.fileContents(repoRoot.file("src/code_ai/version.json")).asText.get()
    @Suppress("UNCHECKED_CAST")
    val data = JsonSlurper().parseText(text) as Map<String, Any>
    "${data["major"]}.${data["minor"]}.${data["patch"]}"
}

group = "com.codeai"
version = productVersion

/** Per-machine settings, kept out of git: `local.properties`, then `-P`, then the environment. */
val localProperties = Properties().apply {
    val file = layout.projectDirectory.file("local.properties").asFile
    if (file.isFile) file.inputStream().use(::load)
}

fun setting(name: String, env: String): String? =
    providers.gradleProperty(name).orNull
        ?: localProperties.getProperty(name)
        ?: providers.environmentVariable(env).orNull

/** An installed IDE to build against, instead of downloading one. */
val ideDir = setting("ideDir", "CODE_AI_IDE_DIR")

/** A folder holding the frozen backend (`code-ai`, or `code-ai.exe`) to ship inside the plugin. */
val backendDir = setting("backendDir", "CODE_AI_BACKEND_DIR")

repositories {
    mavenCentral()
    intellijPlatform {
        defaultRepositories()
    }
}

dependencies {
    intellijPlatform {
        if (ideDir != null) local(ideDir) else androidStudio(providers.gradleProperty("platformVersion"))
        // The runtime Android Studio ships has no embedded browser (JCEF), which
        // the panel is drawn in. The sandbox IDE runs on the JetBrains Runtime
        // it was built from, with the browser, as a user's does once they have
        // switched (see README). -PjbrVersion= leaves the IDE on its own
        // runtime: what a user sees before switching.
        providers.gradleProperty("jbrVersion").orNull?.takeIf { it.isNotBlank() }?.let { jetbrainsRuntime(it) }
    }
    testImplementation(kotlin("test"))
    testRuntimeOnly("org.junit.platform:junit-platform-launcher")
    // The IDE's test listener, which the platform puts on every test run, is written against JUnit 4.
    testRuntimeOnly("junit:junit:4.13.2")
}

kotlin {
    jvmToolchain(21)
    compilerOptions {
        // The IDE brings its own Kotlin runtime: stay at the level it ships.
        languageVersion = KotlinVersion.KOTLIN_2_2
        apiVersion = KotlinVersion.KOTLIN_2_2
    }
}

intellijPlatform {
    buildSearchableOptions = false
    pluginConfiguration {
        ideaVersion {
            sinceBuild = providers.gradleProperty("sinceBuild")
            untilBuild = provider { null }
        }
    }
}

// --- the panel ---------------------------------------------------------------
// The chat is the VS Code extension's own webview bundle, shipped unchanged:
// one panel, written once. It is built where it lives and copied in.

val vscodeExtension = repoRoot.dir("vscode-extension")
val npm = if (System.getProperty("os.name").lowercase().contains("win")) "npm.cmd" else "npm"

/**
 * Runs npm in the panel's folder.
 *
 * The environment is added to when the task runs. Added to while the build is
 * being configured, the configuration cache would keep the whole of it, PATH
 * included, as it was the day the cache was written: a Node.js installed
 * since would stay unfound.
 */
fun Exec.npm(vararg arguments: String, variables: Map<String, String> = emptyMap()) {
    workingDir = vscodeExtension.asFile
    commandLine(listOf(npm) + arguments)
    doFirst {
        this as Exec
        variables.forEach { (name, value) -> environment(name, value) }
    }
}

/** What npm has installed, held against what its lock file asks for. */
object NodeModules {
    /**
     * Whether every package of the lock file is installed, at its version.
     * [installed] is the list npm leaves in node_modules once an install has
     * run to its end, so one that was cut short reads as not installed.
     */
    fun match(lock: File, installed: File): Boolean {
        if (!lock.isFile || !installed.isFile) return false
        val have = packages(installed)
        return packages(lock).all { (path, wanted) ->
            val there = have[path]
            when {
                path.isEmpty() -> true // the panel itself
                there != null -> there["version"] == wanted["version"]
                else -> wanted["optional"] == true // another platform's binary
            }
        }
    }

    @Suppress("UNCHECKED_CAST")
    private fun packages(file: File): Map<String, Map<String, Any?>> =
        (JsonSlurper().parse(file) as? Map<String, Any?>)?.get("packages") as? Map<String, Map<String, Any?>> ?: emptyMap()
}

val installWebviewDependencies by tasks.registering(Exec::class) {
    description = "Installs the panel's npm dependencies, unless what is installed is what the lock file asks for."
    // The e2e browser is hundreds of megabytes the build has no use for.
    npm("ci", "--no-audit", "--no-fund", variables = mapOf("PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD" to "1"))
    val lock = vscodeExtension.file("package-lock.json").asFile
    val installed = vscodeExtension.file("node_modules/.package-lock.json").asFile
    onlyIf { !NodeModules.match(lock, installed) }
}

val buildWebview by tasks.registering(Exec::class) {
    description = "Bundles the panel (vscode-extension/dist)."
    dependsOn(installWebviewDependencies)
    npm("run", "build")
    inputs.dir(vscodeExtension.dir("webview"))
    inputs.dir(vscodeExtension.dir("src"))
    inputs.files(vscodeExtension.file("esbuild.mjs"), vscodeExtension.file("package-lock.json"))
    outputs.files(vscodeExtension.file("dist/webview.js"), vscodeExtension.file("dist/flow.js"))
    outputs.dir(vscodeExtension.dir("dist/codicons"))
}

val webviewResources by tasks.registering(Sync::class) {
    description = "Collects the panel's bundles as plugin resources."
    dependsOn(buildWebview)
    from(vscodeExtension.dir("dist")) {
        include("webview.js", "flow.js", "codicons/**")
    }
    into(layout.buildDirectory.dir("generated/webview/webview"))
}

sourceSets.main {
    resources.srcDir(webviewResources.map { it.destinationDir.parentFile })
}

// --- the backend -------------------------------------------------------------
// A packaged plugin carries the frozen `code-ai` in its own bin/ folder, where
// the plugin looks for it first (ResolveCommand.kt). Without one the plugin
// still builds, and finds the backend in the project's .venv or on PATH.

tasks.prepareSandbox {
    if (backendDir != null) {
        from(backendDir) {
            into(pluginName.map { "$it/bin" })
        }
    }
}

tasks.buildPlugin {
    // The backend has to stay executable inside the package.
    useFileSystemPermissions()
}

// --- the sandbox IDE ---------------------------------------------------------

/** Words of a `-P` property, as a command line splits them. */
fun words(property: String): List<String> =
    providers.gradleProperty(property).orNull?.split(Regex("\\s+"))?.filter { it.isNotBlank() } ?: emptyList()

tasks.runIde {
    // A sandbox is a fresh install: go straight to the IDE, not through Android Studio's setup wizard.
    jvmArgs("-Ddisable.android.first.run=true")
    // The backend being worked on is the repository's own (install.sh puts it in .venv):
    // the sandbox finds it there without anything being set in the IDE.
    jvmArgs("-Dcode.ai.dev.root=${repoRoot.asFile.absolutePath}")
    // -PideJvmArgs="-Dkey=value ..." and -PideArgs="/path/to/a/project" reach the IDE as they are.
    jvmArgs(words("ideJvmArgs"))
    args(words("ideArgs"))
}

tasks.test {
    useJUnitPlatform()
}
