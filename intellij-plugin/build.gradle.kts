import groovy.json.JsonSlurper
import java.util.Properties
import org.jetbrains.intellij.platform.gradle.tasks.PrepareSandboxTask
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

/** What the first of those places to name a setting says, even when it says nothing. */
fun answer(name: String, env: String): String? =
    providers.gradleProperty(name).orNull
        ?: localProperties.getProperty(name)
        ?: providers.environmentVariable(env).orNull

/** A setting, or null: left out, or given empty to switch off what a file sets (`-PideDir=`). */
fun setting(name: String, env: String): String? = answer(name, env)?.trim()?.takeIf { it.isNotEmpty() }

/** An installed IDE to build against, instead of downloading one. */
val ideDir = setting("ideDir", "CODE_AI_IDE_DIR")

/** A folder holding the frozen backend (`code-ai`, or `code-ai.exe`) to ship inside the plugin. */
val backendDir = setting("backendDir", "CODE_AI_BACKEND_DIR")

/** A Node.js to bundle the panel with instead of the one on PATH (setup.sh fetches one when the system's is too old). */
val nodeDir = setting("nodeDir", "CODE_AI_NODE_DIR")

// --- the IDE -----------------------------------------------------------------

/** A file of the installed IDE, at whichever of the places a platform keeps it. */
fun ideFile(vararg places: String): File? = ideDir?.let { dir -> places.map { File(dir, it) }.firstOrNull(File::isFile) }

/** Read while the build is configured, and so that the build notices when it changes. */
fun textOf(file: File): String = providers.fileContents(layout.projectDirectory.file(file.absolutePath)).asText.get()

/** What the installed IDE says of itself, in its product-info.json. */
val installedIde: Map<String, Any?>? = ideDir?.let { dir ->
    val file = ideFile("product-info.json", "Resources/product-info.json", "Contents/Resources/product-info.json")
        ?: error("ideDir is not an IDE, there is no product-info.json in it: $dir")
    @Suppress("UNCHECKED_CAST")
    JsonSlurper().parseText(textOf(file)) as Map<String, Any?>
}

/** The IntelliJ Platform release the plugin is built against, as its builds are numbered: 253 is 2025.3. */
val platformBranch: Int =
    if (installedIde != null) {
        installedIde["buildNumber"]?.toString()?.substringBefore('.')?.toIntOrNull()
            ?: error("ideDir names an IDE whose product-info.json has no build number: $ideDir")
    } else {
        val (year, release) = providers.gradleProperty("platformVersion").get().split('.')
        year.toInt() % 100 * 10 + release.toInt()
    }

// --- the embedded browser ----------------------------------------------------
// The panel is drawn in the IDE's embedded browser (JCEF), and where an IDE
// keeps that browser changed with 2026.1.
//
// Before, its classes are the IDE's and the browser itself is part of the Java
// runtime. Android Studio ships a runtime without one, so the sandbox IDE runs
// on the JetBrains Runtime that has it, as a user's does once they have
// switched.
//
// Since, the browser is a plugin of JetBrains' own, Web Browser (JCEF), with
// its Chromium inside, and from 2026.2 the classes are that plugin's as well.
// JetBrains' IDEs come with it; Android Studio gets it from the Marketplace,
// where the build fetches it for the sandbox and a user installs it once (see
// README).

val browserPlugin = "com.intellij.modules.jcef"
val browserIsPlugin = platformBranch >= 261
val browserIsBundled = (installedIde?.get("bundledPlugins") as? List<*>)?.contains(browserPlugin) == true

/** This machine, as the Marketplace names the builds of a plugin that has one for each system. */
val hostSystem: String = run {
    val system = System.getProperty("os.name").lowercase()
    val family = when {
        system.startsWith("windows") -> "windows"
        system.startsWith("mac") -> "mac"
        else -> "linux"
    }
    val processor = System.getProperty("os.arch").lowercase()
    "$family-" + if (processor == "aarch64" || processor == "arm64") "arm64" else "x86_64"
}

/**
 * The JetBrains Runtime the installed IDE ships, as jetbrainsRuntime() names
 * one (21.0.9b1163.94): read from the runtime's own `release` file.
 */
fun installedRuntime(): String? {
    val text = ideFile("jbr/release", "Contents/jbr/Contents/Home/release")?.let(::textOf) ?: return null
    fun value(pattern: String) = Regex(pattern, RegexOption.MULTILINE).find(text)?.groupValues?.get(1)
    val java = value("""^JAVA_VERSION="([^"]+)"""") ?: return null
    val build = value("""^JAVA_RUNTIME_VERSION="[^"]*-b([0-9][0-9.]*)"""") ?: return null
    return "${java}b$build"
}

/**
 * The JetBrains Runtime the sandbox IDE runs on where the browser is the
 * runtime's: the variant with JCEF of the one the IDE ships. The installed IDE
 * says which that is; for the release the build downloads it is written
 * beside it in gradle.properties. `-PjbrVersion=<version>` names another, and
 * `-PjbrVersion=` none: the IDE then runs on what it ships, which is what a
 * user sees before switching (see README).
 */
val sandboxRuntime: String? =
    (answer("jbrVersion", "CODE_AI_JBR_VERSION")
        ?: (if (browserIsPlugin) null else installedRuntime() ?: providers.gradleProperty("platformJbrVersion").orNull))
        ?.trim()?.takeIf { it.isNotEmpty() }

repositories {
    mavenCentral()
    intellijPlatform {
        defaultRepositories()
    }
}

dependencies {
    intellijPlatform {
        if (ideDir != null) local(ideDir) else androidStudio(providers.gradleProperty("platformVersion"))
        when {
            !browserIsPlugin -> Unit
            browserIsBundled -> bundledPlugin(browserPlugin)
            else -> compatiblePlugin(browserPlugin)
        }
        sandboxRuntime?.let { jetbrainsRuntime(it) }
    }
    testImplementation(kotlin("test"))
    testRuntimeOnly("org.junit.platform:junit-platform-launcher")
    // The IDE's test listener, which the platform puts on every test run, is written against JUnit 4.
    testRuntimeOnly("junit:junit:4.13.2")
}

// The browser plugin has a build for each system. Asked which of them fits an
// IDE, the Marketplace names one for whichever system: take this machine's.
configurations.configureEach {
    resolutionStrategy.eachDependency {
        if (requested.name == browserPlugin) {
            requested.version?.let { useVersion(it.replace(Regex("-(linux|mac|windows)-(x86_64|arm64)$"), "-$hostSystem")) }
        }
    }
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
val windows = hostSystem.startsWith("windows")

/** Where `nodeDir` keeps node and npm: they run from there, and nothing else on the machine has to know of them. */
val nodeBin: String? = nodeDir?.let { if (windows) it else File(it, "bin").path }

/**
 * Runs one of Node.js's two commands, `node` or `npm`.
 *
 * The environment is added to when the task runs. Added to while the build is
 * being configured, the configuration cache would keep the whole of it, PATH
 * included, as it was the day the cache was written: a Node.js installed
 * since would stay unfound.
 */
fun Exec.nodeCommand(name: String, arguments: List<String>, variables: Map<String, String> = emptyMap()) {
    // Locals, so that the action below holds on to them and not to this script.
    val bin = nodeBin
    val command = if (!windows) name else if (name == "npm") "npm.cmd" else "$name.exe"
    commandLine(listOf(bin?.let { File(it, command).path } ?: command) + arguments)
    doFirst {
        this as Exec
        variables.forEach { (name, value) -> environment(name, value) }
        if (bin != null) {
            // npm starts node, and the packages' own scripts start it again, by name.
            val path = environment.keys.firstOrNull { it.equals("PATH", ignoreCase = true) } ?: "PATH"
            environment(path, listOfNotNull(bin, environment[path]?.toString()).joinToString(File.pathSeparator))
        }
    }
}

/** Runs npm in the panel's folder. */
fun Exec.npm(vararg arguments: String, variables: Map<String, String> = emptyMap()) {
    workingDir = vscodeExtension.asFile
    nodeCommand("npm", arguments.toList(), variables)
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

// macOS seals an application once it has run, and the browser plugin holds a
// few: the processes its Chromium runs in. Its copy in a sandbox can then be
// removed and not written over, so it is removed, and the copy that follows
// is a new one.
tasks.withType<PrepareSandboxTask>().configureEach {
    doFirst {
        (this as Sync).destinationDir
            .listFiles { plugin -> File(plugin, "jcef/Frameworks").isDirectory }
            ?.forEach { it.deleteRecursively() }
    }
}

// --- the sandbox IDE ---------------------------------------------------------

/** Words of a `-P` property, as a command line splits them. */
fun words(property: String): List<String> =
    providers.gradleProperty(property).orNull?.split(Regex("\\s+"))?.filter { it.isNotBlank() } ?: emptyList()

tasks.runIde {
    // A sandbox is a fresh install: go straight to the IDE, past what Android
    // Studio asks a new user. Its setup wizard, a Google sign-in, and whether
    // to send usage statistics: that last answer is kept outside the sandbox,
    // where it would stand for the user's own Android Studio too.
    jvmArgs(
        "-Ddisable.android.first.run=true",
        "-Dgoogle.login.enable.startup.sign.in.dialog=false",
        "-Ddisable.android.analytics.consent.dialog=true",
    )
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
