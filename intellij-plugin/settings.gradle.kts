// Fetches the JDK the build runs on and compiles with (21) when the machine has
// none: any Java can then start it (gradle/gradle-daemon-jvm.properties).
plugins {
    id("org.gradle.toolchains.foojay-resolver-convention") version "1.0.0"
}

rootProject.name = "code-ai-intellij"
