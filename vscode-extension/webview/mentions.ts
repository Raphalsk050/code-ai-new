// Typing `@` in the composer offers the workspace's files, the same affordance
// the terminal has (`code_ai.ui.terminal.mentions`). Matching and insertion are
// here, away from React, because they are the part worth testing.

/** How many files the menu offers: more than this stops being a glance. */
export const MENTION_LIMIT = 8;

// An `@` that starts the text or follows whitespace, then everything up to the
// caret that is not whitespace. Anchored at the caret: an `@` earlier in a
// finished sentence is a mention already made, or an e-mail address.
const MENTION = /(?:^|(?<=\s))@([^\s@]*)$/;

/** The partial path being typed before `caret`, or null when there is none. */
export function mentionPrefix(text: string, caret: number): string | null {
  const head = text.slice(0, Math.max(0, caret));
  const match = MENTION.exec(head);
  // "" is a real answer - a bare `@` means "offer everything" - so this is not
  // written as a truthiness check.
  return match ? match[1] : null;
}

/** Replace the partial mention under the caret with `path`, keeping one space. */
export function applyMention(
  text: string,
  caret: number,
  path: string
): { text: string; caret: number } {
  const head = text.slice(0, Math.max(0, caret));
  const match = MENTION.exec(head);
  if (!match) return { text, caret };
  const start = head.length - match[0].length + (match[0].startsWith("@") ? 0 : 1);
  const insert = `@${path} `;
  const next = text.slice(0, start) + insert + text.slice(caret);
  return { text: next, caret: start + insert.length };
}

/**
 * Rank workspace paths against what is typed.
 *
 * A name match beats a path match, an earlier hit beats a later one, and a
 * shorter path breaks the tie - so `mentions.py` outranks
 * `tests/unit/test_mentions.py` for "mentions", and typing a folder still
 * finds everything under it.
 */
export function rankPaths(paths: string[], prefix: string): string[] {
  const query = prefix.toLowerCase();
  if (!query) return paths.slice(0, MENTION_LIMIT);
  const scored: { path: string; score: number }[] = [];
  for (const path of paths) {
    const lower = path.toLowerCase();
    const name = lower.slice(lower.lastIndexOf("/") + 1);
    let score: number;
    if (name.startsWith(query)) score = 0;
    else if (name.includes(query)) score = 1;
    else if (lower.includes(query)) score = 2;
    else continue;
    scored.push({ path, score: score * 1000 + Math.min(999, path.length) });
  }
  scored.sort((a, b) => a.score - b.score || a.path.localeCompare(b.path));
  return scored.slice(0, MENTION_LIMIT).map((item) => item.path);
}
