import * as React from "react";

import { t } from "../i18n";
import { CATEGORY_COLORS } from "./model";

/** One thing the menu can do, with the section it is filed under. */
export interface MenuEntry {
  id: string;
  label: string;
  hint: string;
  section: string;
  colour: string;
  run(): void;
}

export interface ContextMenuProps {
  /** Where the click happened, in page coordinates. */
  at: { x: number; y: number };
  title: string;
  entries: MenuEntry[];
  /** A short menu (a node's own actions) needs no search box. */
  searchable: boolean;
  onClose(): void;
}

const MENU_W = 268;
const MENU_MAX_H = 420;

/**
 * The right-click menu, the way a Blueprint does it: a filter box and a
 * sectioned list, keyboard-drivable from the first keystroke.
 *
 * It is positioned in page coordinates and flipped when it would fall off the
 * edge, so a right-click near the bottom of the canvas still shows a full menu.
 */
export function ContextMenu({
  at,
  title,
  entries,
  searchable,
  onClose,
}: ContextMenuProps): JSX.Element {
  const [query, setQuery] = React.useState("");
  const [cursor, setCursor] = React.useState(0);
  const boxRef = React.useRef<HTMLDivElement | null>(null);
  const inputRef = React.useRef<HTMLInputElement | null>(null);

  React.useEffect(() => {
    inputRef.current?.focus();
  }, []);

  React.useEffect(() => {
    const onDown = (event: MouseEvent) => {
      if (!boxRef.current?.contains(event.target as Node)) onClose();
    };
    // `mousedown` rather than `click`: the menu must close before the canvas
    // treats the release as a selection.
    window.addEventListener("mousedown", onDown, true);
    return () => window.removeEventListener("mousedown", onDown, true);
  }, [onClose]);

  const matches = React.useMemo(() => filterEntries(entries, query), [entries, query]);
  React.useEffect(() => setCursor(0), [query]);

  // Grouped by name rather than by run, so a section appears once with all of
  // its rows - the catalog interleaves categories, and a section header that
  // repeats reads as a different section.
  const grouped = new Map<string, MenuEntry[]>();
  for (const entry of matches) {
    const rows = grouped.get(entry.section);
    if (rows) rows.push(entry);
    else grouped.set(entry.section, [entry]);
  }
  const sections = [...grouped].map(([name, rows]) => ({ name, rows }));
  const ordered = sections.flatMap((section) => section.rows);

  const choose = (entry: MenuEntry) => {
    entry.run();
    onClose();
  };

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      setCursor((value) => Math.max(0, Math.min(ordered.length - 1, value + step)));
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      const entry = ordered[cursor];
      if (entry) choose(entry);
    }
  };

  const style: React.CSSProperties = {
    left: Math.min(at.x, Math.max(8, window.innerWidth - MENU_W - 8)),
    top: Math.min(at.y, Math.max(8, window.innerHeight - MENU_MAX_H - 8)),
    width: MENU_W,
  };

  let index = -1;
  return (
    <div
      className="flow-menu"
      style={style}
      ref={boxRef}
      data-testid="context-menu"
      onKeyDown={onKeyDown}
      onContextMenu={(event) => event.preventDefault()}
    >
      <div className="flow-menu-title">{title}</div>
      {searchable && (
        <input
          ref={inputRef}
          className="flow-menu-search"
          data-testid="menu-search"
          placeholder={`${t("Search")}…`}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      )}
      <div className="flow-menu-list">
        {matches.length === 0 && (
          <div className="flow-menu-empty">{t("Nothing with that name.")}</div>
        )}
        {sections.map((section) => (
          <React.Fragment key={section.name}>
            <div className="flow-menu-section">{section.name}</div>
            {section.rows.map((entry) => {
              index += 1;
              const active = index === cursor;
              return (
                <button
                  key={entry.id}
                  className={`flow-menu-row${active ? " active" : ""}`}
                  data-testid={`menu-${entry.id}`}
                  onMouseEnter={() => setCursor(ordered.indexOf(entry))}
                  onClick={() => choose(entry)}
                >
                  <span className="swatch" style={{ background: entry.colour }} />
                  <span className="label">{entry.label}</span>
                  {entry.hint && <span className="hint">{entry.hint}</span>}
                </button>
              );
            })}
          </React.Fragment>
        ))}
      </div>
    </div>
  );
}

/** Substring match over label and hint, in the order the sections were built. */
export function filterEntries(entries: MenuEntry[], query: string): MenuEntry[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return entries;
  const words = needle.split(/\s+/);
  return entries.filter((entry) => {
    const haystack = `${entry.label} ${entry.hint} ${entry.section}`.toLowerCase();
    return words.every((word) => haystack.includes(word));
  });
}

export function categoryColour(category: string): string {
  return CATEGORY_COLORS[category] ?? CATEGORY_COLORS.flow;
}
