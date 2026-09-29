// What a file-writing tool call is about to leave on disk, worked out from its
// arguments the way the tool itself works it out, so the editor can show the
// change before it lands. No `vscode` import: the unit tests run it in node.

/** The tools whose calls the editor shows as a diff. */
export const DIFF_TOOLS = new Set(["write_file", "edit_code"]);

export interface Edit {
  old: string;
  new: string;
  expected: number;
}

/** The edits of an edit_code call, in either shape the tool accepts; null if malformed. */
export function editsOf(args: Record<string, unknown>): Edit[] | null {
  const list: unknown[] = Array.isArray(args.edits)
    ? args.edits
    : [{ old: args.old_text, new: args.new_text }];
  const edits: Edit[] = [];
  for (const item of list) {
    if (!item || typeof item !== "object") return null;
    const fields = item as Record<string, unknown>;
    if (typeof fields.old !== "string" || !fields.old || typeof fields.new !== "string") return null;
    edits.push({ old: fields.old, new: fields.new, expected: Number(fields.expected_occurrences ?? 1) || 1 });
  }
  return edits.length ? edits : null;
}

/**
 * `text` with the edits applied, or null where the tool would refuse: text not
 * found, found a different number of times than expected, or edits overlapping.
 */
export function applyEdits(text: string, edits: Edit[]): string | null {
  const spans: { start: number; end: number; text: string }[] = [];
  for (const edit of edits) {
    const found: number[] = [];
    for (let at = text.indexOf(edit.old); at >= 0; at = text.indexOf(edit.old, at + edit.old.length)) {
      found.push(at);
    }
    if (found.length !== edit.expected) return null;
    for (const start of found) spans.push({ start, end: start + edit.old.length, text: edit.new });
  }
  spans.sort((a, b) => a.start - b.start);
  let out = "";
  let cursor = 0;
  for (const span of spans) {
    if (span.start < cursor) return null;
    out += text.slice(cursor, span.start) + span.text;
    cursor = span.end;
  }
  return out + text.slice(cursor);
}

/** The file a write_file or edit_code call leaves behind, given what is there now. */
export function proposedContent(
  tool: string,
  args: Record<string, unknown>,
  current: string
): string | null {
  if (tool === "write_file") return typeof args.content === "string" ? args.content : null;
  if (tool !== "edit_code") return null;
  const edits = editsOf(args);
  return edits ? applyEdits(current, edits) : null;
}

/** Whether a call writes into the project, as opposed to the session sandbox. */
export function writesToProject(args: Record<string, unknown>): boolean {
  const location = String(args.location ?? "").trim().toLowerCase();
  return location !== "sandbox";
}

/** The diff tab's title: the file name, and whose change it is. */
export function diffTitle(relative: string, phase: "proposed" | "written"): string {
  const name = relative.split(/[\\/]/).pop() || relative;
  return phase === "proposed" ? `${name} (Code-AI, proposed)` : `${name} (Code-AI)`;
}
