import * as React from "react";

import { IconFile } from "./icons";

/** The `@` file picker: same shape as the slash menu, so there is one thing to learn. */
export function MentionMenu({
  paths,
  active,
  onPick,
  onHover,
}: {
  paths: string[];
  active: number;
  onPick: (path: string) => void;
  onHover: (index: number) => void;
}): JSX.Element | null {
  const ref = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    ref.current?.querySelector(`[data-idx="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active]);

  if (paths.length === 0) return null;
  return (
    <div className="cmd-menu mention-menu" ref={ref}>
      {paths.map((path, i) => {
        const cut = path.lastIndexOf("/");
        return (
          <div
            key={path}
            data-idx={i}
            className={`cmd-item ${i === active ? "active" : ""}`}
            onMouseDown={(e) => {
              // mousedown, not click: the composer must keep focus.
              e.preventDefault();
              onPick(path);
            }}
            onMouseEnter={() => onHover(i)}
          >
            <IconFile size={12} />
            <span className="cmd-name">{cut < 0 ? path : path.slice(cut + 1)}</span>
            <span className="cmd-desc">{cut < 0 ? "" : path.slice(0, cut)}</span>
          </div>
        );
      })}
    </div>
  );
}
