// The transcript by who is speaking: a run of the user's messages is one block,
// and everything the agent does until the user speaks again - reasoning, tool
// calls, answers, notices - is the next one.

import type { Item } from "./store/transcript";

export type Speaker = "user" | "agent";

export interface Block {
  speaker: Speaker;
  /** Stays put while the block grows, so React never remounts what is on screen. */
  key: string;
  items: Item[];
}

export function speakerOf(item: Item): Speaker {
  return item.kind === "user" ? "user" : "agent";
}

/**
 * The key of the agent block answering the user block `userKey` ("" before the
 * first one). Keyed by what it answers rather than by its first item, so the
 * block the typing indicator opens is the same one the first reply lands in.
 */
export function agentKey(userKey: string): string {
  return `agent:${userKey || "start"}`;
}

/** Consecutive items from the same side, in order. */
export function groupBlocks(items: readonly Item[]): Block[] {
  const blocks: Block[] = [];
  let lastUser = "";
  for (const item of items) {
    const speaker = speakerOf(item);
    const current = blocks[blocks.length - 1];
    if (current && current.speaker === speaker) {
      current.items.push(item);
      continue;
    }
    const key = speaker === "user" ? `user:${item.id}` : agentKey(lastUser);
    if (speaker === "user") lastUser = key;
    blocks.push({ speaker, key, items: [item] });
  }
  return blocks;
}

/**
 * Whether an agent block ends on a finished answer: the model had the last
 * word, with at most quiet notes after it. A tool, more reasoning or a warning
 * after the answer means the turn went on, or went wrong.
 */
export function endsOnAnswer(block: Block): boolean {
  let answered = false;
  for (const item of block.items) {
    if (item.kind === "assistant") answered = !item.streaming;
    else if (item.kind !== "notice" || item.level === "error" || item.level === "warning") answered = false;
  }
  return answered;
}

/** Where a user block sits, in the same coordinates as the viewport's top. */
export interface Span {
  top: number;
  bottom: number;
}

/**
 * Which user block to pin over the transcript: the last one that starts above
 * the viewport, once no more of it is in view than the pin itself would cover.
 * -1 when there is none to pin: at the top, while the request is still on
 * screen, or when the next request is about to reach the top - pinning the old
 * one there would cover the one the reader is scrolling to.
 */
export function blockToPin(blocks: readonly Span[], viewTop: number, zone: number): number {
  let found = -1;
  for (let i = 0; i < blocks.length; i++) {
    if (blocks[i].top < viewTop) found = i;
    else break;
  }
  if (found < 0 || blocks[found].bottom > viewTop + zone) return -1;
  const next = blocks[found + 1];
  if (next && next.top < viewTop + zone) return -1;
  return found;
}
