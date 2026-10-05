// What the user is looking at in the editor, as the model is told about it.
// No `vscode` import: the host gathers it, this file words it, and the unit
// tests run it in node. The Android Studio plugin words it the same way
// (intellij-plugin, EditorContext.kt): keep the two in step.

import type { EditorContext } from "./protocol";

/** How many of the other open files are named to the model; the rest are counted. */
export const MAX_OPEN_FILES_NAMED = 30;

/**
 * The other files open in the editor, as the context lists them: in tab order,
 * each once, without the active one, with forward slashes.
 */
export function otherOpenFiles(open: string[], active?: string): string[] {
  const slashes = (file: string) => file.replace(/\\/g, "/");
  const skip = active ? slashes(active) : undefined;
  const seen = new Set<string>();
  const out: string[] = [];
  for (const file of open.map(slashes)) {
    if (!file || file === skip || seen.has(file)) continue;
    seen.add(file);
    out.push(file);
  }
  return out;
}

/** Render an {@link EditorContext} into the preamble the model receives. */
export function formatEditorContext(context: EditorContext | null): string {
  if (!context) return "";
  const lines: string[] = [];
  if (context.path) {
    lines.push(`[Editor context] Active file: ${context.path} (${context.language})`);
    if (context.selection && context.startLine && context.endLine) {
      lines.push(
        `User has selected lines ${context.startLine}-${context.endLine}:`,
        "```" + context.language,
        context.selection,
        "```"
      );
    } else {
      lines.push("The user currently has this file open (no selection).");
    }
  }
  const open = context.openFiles ?? [];
  if (open.length) {
    lines.push(
      context.path
        ? "Other files open in the editor:"
        : "[Editor context] Files open in the editor (none of them has the focus):"
    );
    for (const file of open.slice(0, MAX_OPEN_FILES_NAMED)) lines.push(`- ${file}`);
    if (open.length > MAX_OPEN_FILES_NAMED) lines.push(`- and ${open.length - MAX_OPEN_FILES_NAMED} more`);
  }
  return lines.join("\n");
}
