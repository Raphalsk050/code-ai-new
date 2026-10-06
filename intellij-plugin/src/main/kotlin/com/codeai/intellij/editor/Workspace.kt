package com.codeai.intellij.editor

import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.project.Project
import com.intellij.openapi.roots.ProjectFileIndex
import com.intellij.openapi.util.Computable
import com.intellij.openapi.vfs.LocalFileSystem
import com.intellij.openapi.vfs.VfsUtil
import com.intellij.openapi.vfs.VirtualFile
import java.nio.file.InvalidPathException
import java.nio.file.Path

/** A file of the project: where it is on disk, and how the panel names it. */
internal data class Place(
    /** Absolute, as the system spells it: the key. */
    val path: String,
    /** Project-relative with forward slashes: what titles and the panel show. */
    val relative: String,
)

/** The folder the agent works on, and the project's files as seen from it. */
internal object Workspace {
    /** The folder the backend is started in: the project's own. */
    fun root(project: Project): Path? = try {
        project.basePath?.let { Path.of(it).normalize() }
    } catch (_: InvalidPathException) {
        null
    }

    /**
     * Where a path the backend named is: absolute, or relative to the project.
     * Null for one outside the project: not a change the IDE should put in
     * front of anyone.
     */
    fun resolve(project: Project, value: String): Place? {
        val root = root(project) ?: return null
        if (value.isBlank()) return null
        val absolute = try {
            val given = Path.of(value)
            // A relative path may come with either slash, whatever the system.
            if (given.isAbsolute) given.normalize() else root.resolve(value.replace('\\', '/')).normalize()
        } catch (_: InvalidPathException) {
            return null
        }
        if (!absolute.startsWith(root) || absolute == root) return null
        return Place(absolute.toString(), root.relativize(absolute).joinToString("/"))
    }

    /** How the panel names a file: relative to the project when it is inside it. */
    fun relative(project: Project, file: VirtualFile): String {
        val root = root(project)
        val path = file.toNioPathOrNull()?.normalize() ?: return file.path
        return if (root != null && path.startsWith(root) && path != root) root.relativize(path).joinToString("/")
        else file.path
    }

    private fun VirtualFile.toNioPathOrNull(): Path? = try {
        toNioPath()
    } catch (_: UnsupportedOperationException) {
        null
    }

    fun file(path: String): VirtualFile? = try {
        LocalFileSystem.getInstance().refreshAndFindFileByNioFile(Path.of(path))
    } catch (_: InvalidPathException) {
        null
    }

    /** Tell the IDE a file changed on disk behind its back: the agent writes with its own hands. */
    fun refresh(path: String) {
        val file = try {
            LocalFileSystem.getInstance().findFileByNioFile(Path.of(path))
        } catch (_: InvalidPathException) {
            null
        }
        if (file != null) VfsUtil.markDirtyAndRefresh(true, false, false, file)
        else try {
            // Not known yet: it was just created. Its folder is where it shows up.
            LocalFileSystem.getInstance().refreshAndFindFileByNioFile(Path.of(path))
        } catch (_: InvalidPathException) {
            // Nothing to refresh.
        }
    }

    // Folders VS Code's search leaves out for the same picker.
    private val SKIPPED = setOf("node_modules", ".git", "dist", "out", "build", ".venv", "__pycache__")
    private val GLOB_CHARACTERS = Regex("""[*?{}\[\]]""")

    /**
     * The project's files whose name holds `query`, shortest path first: what
     * the composer's `@` picker lists. Reads the project's index, which already
     * knows the files and what the project excludes.
     */
    fun search(project: Project, query: String, limit: Int = 200): List<String> =
        ApplicationManager.getApplication().runReadAction(Computable {
            if (project.isDisposed) return@Computable emptyList()
            val needle = query.replace(GLOB_CHARACTERS, "").lowercase()
            val found = ArrayList<String>()
            ProjectFileIndex.getInstance(project).iterateContent(
                { file ->
                    if (!file.isDirectory) {
                        val relative = relative(project, file)
                        val hay = if ('/' in needle) relative.lowercase() else file.name.lowercase()
                        if (needle.isEmpty() || needle in hay) found += relative
                    }
                    found.size < limit
                },
                { file -> !(file.isDirectory && file.name in SKIPPED) },
            )
            found.sortedBy { it.length }
        })
}
