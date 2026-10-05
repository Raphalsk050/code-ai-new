// Fetches the JDK the build compiles with (21) when the machine has none:
// Gradle then only needs some Java, 17 or newer, to start.
plugins {
    id("org.gradle.toolchains.foojay-resolver-convention") version "1.0.0"
}

rootProject.name = "code-ai-intellij"
