import { promises as fs } from "fs";
import * as path from "path";

/** How long a conversation's changes are gathered before they are written. */
const WRITE_AFTER_MS = 500;

/**
 * The panel's conversations, one file each, kept by the host.
 *
 * They used to live in the webview's own state, which VS Code takes as one
 * string: every new message serialised the whole history, megabytes of it,
 * and carried it to the editor's window. Here a save carries only what
 * changed, from the first message that differs, and the disk sees one write
 * per conversation per half second at most.
 */
export class TranscriptStore {
  private readonly items = new Map<string, unknown[]>();
  private readonly due = new Map<string, ReturnType<typeof setTimeout>>();
  /** One conversation's loads and saves, in the order they came: two saves never read the file at once. */
  private readonly turns = new Map<string, Promise<unknown>>();
  /** Writes in the order they were asked for: a slow one is never overtaken by a later state. */
  private writing: Promise<void> = Promise.resolve();

  constructor(private readonly dir: string) {}

  private file(id: string): string {
    return path.join(this.dir, `${encodeURIComponent(id)}.json`);
  }

  private serial<T>(id: string, task: () => Promise<T>): Promise<T> {
    const next = (this.turns.get(id) ?? Promise.resolve()).then(task, task);
    this.turns.set(id, next);
    void next.finally(() => {
      if (this.turns.get(id) === next) this.turns.delete(id);
    });
    return next;
  }

  /** A conversation's items, or null when there is nothing kept for it. */
  load(id: string): Promise<unknown[] | null> {
    return this.serial(id, () => this.read(id));
  }

  private async read(id: string): Promise<unknown[] | null> {
    const known = this.items.get(id);
    if (known) return known;
    try {
      const items: unknown = JSON.parse(await fs.readFile(this.file(id), "utf8"));
      if (!Array.isArray(items)) return null;
      this.items.set(id, items);
      return items;
    } catch {
      return null;
    }
  }

  /**
   * Replace everything from `from` on with `items`. False when the host holds
   * fewer than `from` (a write lost to a crash): the caller has to send it all.
   */
  save(id: string, from: number, items: unknown[]): Promise<boolean> {
    return this.serial(id, async () => {
      const kept = from === 0 ? [] : await this.read(id);
      if (!kept || kept.length < from) return false;
      kept.length = from;
      for (const item of items) kept.push(item);
      this.items.set(id, kept);
      this.schedule(id);
      return true;
    });
  }

  /** Conversations kept by an older panel, taken over where none is kept yet. */
  async adopt(conversations: { id: string; items: unknown[] }[]): Promise<void> {
    for (const { id, items } of conversations) {
      if (!Array.isArray(items) || items.length === 0) continue;
      await this.serial(id, async () => {
        if (await this.read(id)) return;
        this.items.set(id, items);
        this.schedule(id);
      });
    }
  }

  remove(id: string): Promise<void> {
    return this.serial(id, async () => {
      clearTimeout(this.due.get(id));
      this.due.delete(id);
      this.items.delete(id);
      await this.enqueue(() => fs.rm(this.file(id), { force: true }));
    });
  }

  /** Write whatever is waiting now: the window is closing. */
  async flush(): Promise<void> {
    for (const id of [...this.due.keys()]) this.write(id);
    await this.writing;
  }

  private schedule(id: string): void {
    if (this.due.has(id)) return;
    this.due.set(
      id,
      setTimeout(() => this.write(id), WRITE_AFTER_MS)
    );
  }

  private write(id: string): void {
    clearTimeout(this.due.get(id));
    this.due.delete(id);
    const items = this.items.get(id);
    if (!items) return;
    const text = JSON.stringify(items);
    const file = this.file(id);
    void this.enqueue(async () => {
      await fs.mkdir(this.dir, { recursive: true });
      // Whole or not at all: written beside the file, then moved over it.
      const draft = `${file}.tmp`;
      await fs.writeFile(draft, text, "utf8");
      await fs.rename(draft, file);
    });
  }

  private enqueue(task: () => Promise<unknown>): Promise<void> {
    this.writing = this.writing.then(task).then(
      () => undefined,
      (err) => console.error("[code-ai] could not keep a conversation", err)
    );
    return this.writing;
  }
}
