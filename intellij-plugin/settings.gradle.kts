// Maven Central turns a busy address away (HTTP 429), and a company's proxy is
// one address for everyone behind it. The plugin portal hands most of what it
// is asked for on to Maven Central, so a mirror of it, when one is named
// (CODE_AI_MAVEN_MIRROR, or -PmavenMirror), is asked first.
pluginManagement {
    repositories {
        providers.gradleProperty("mavenMirror").orElse(providers.environmentVariable("CODE_AI_MAVEN_MIRROR")).orNull
            ?.trim()?.takeIf { it.isNotEmpty() }?.let { maven(it) }
        gradlePluginPortal()
    }
}

// Fetches the JDK the build runs on and compiles with (21) when the machine has
// none: any Java can then start it (gradle/gradle-daemon-jvm.properties).
plugins {
    id("org.gradle.toolchains.foojay-resolver-convention") version "1.0.0"
}

rootProject.name = "code-ai-intellij"
