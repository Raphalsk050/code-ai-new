// What the agent changed in this conversation, file by file, measured from the
// file as it was before the agent first touched it. No vscode import: the host
// wraps it, the tests drive it directly.

import { structuredPatch } from "diff";

export interface LedgerEntry {
  /** Absolute path: the key. */
  path: string;
  /** Workspace-relative with forward slashes. */
  relative: string;
  /** The text before the first change; undefined when it was never captured. */
  before?: string;
  /** False when the agent created the file. */
  existed: boolean;
}

export interface ChangeSummary {
  path: string;
  added: number;
  removed: number;
  created: boolean;
  /** The original is known, so the change can be undone. */
  revertable: boolean;
}

export class ChangeLedger {
  private readonly entries = new Map<string, LedgerEntry>();

  /** The text a file held right before a write. The first one per file wins. */
  snapshot(path: string, relative: string, content: string, existed: boolean): void {
    const known = this.entries.get(path);
    if (known && known.before !== undefined) return;
    this.entries.set(path, { path, relative, before: content, existed: known ? known.existed && existed : existed });
  }

  /** A write landed; keep the file even when no snapshot arrived for it. */
  touched(path: string, relative: string): void {
    if (!this.entries.has(path)) this.entries.set(path, { path, relative, existed: true });
  }

  get(path: string): LedgerEntry | undefined {
    return this.entries.get(path);
  }

  byRelative(relative: string): LedgerEntry | undefined {
    for (const entry of this.entries.values()) if (entry.relative === relative) return entry;
    return undefined;
  }

  forget(path: string): void {
    this.entries.delete(path);
  }

  clear(): void {
    this.entries.clear();
  }

  list(): LedgerEntry[] {
    return [...this.entries.values()];
  }

  /**
   * Each file against what is on disk now (`current` returns null for a file
   * that is gone). A file back to how it was drops out.
   */
  async summarize(current: (path: string) => Promise<string | null>): Promise<ChangeSummary[]> {
    const out: ChangeSummary[] = [];
    for (const entry of this.entries.values()) {
      const now = await current(entry.path);
      if (entry.before === undefined) {
        out.push({ path: entry.relative, added: 0, removed: 0, created: false, revertable: false });
        continue;
      }
      const after = now ?? "";
      if (entry.existed && now !== null && after === entry.before) continue;
      if (!entry.existed && now === null) continue;
      const { added, removed } = countLines(entry.before, after);
      out.push({ path: entry.relative, added, removed, created: !entry.existed, revertable: true });
    }
    return out;
  }
}

export function countLines(before: string, after: string): { added: number; removed: number } {
  let added = 0;
  let removed = 0;
  for (const hunk of structuredPatch("a", "b", before, after, "", "", { context: 0 }).hunks) {
    for (const line of hunk.lines) {
      if (line[0] === "+") added++;
      else if (line[0] === "-") removed++;
    }
  }
  return { added, removed };
}
