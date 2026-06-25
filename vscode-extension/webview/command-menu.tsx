import * as React from "react";

import type { SlashCommand } from "./commands";

/** Terminal-style slash-command palette that floats above the composer. */
export function CommandMenu({
  commands,
  active,
  onPick,
  onHover,
}: {
  commands: SlashCommand[];
  active: number;
  onPick: (cmd: SlashCommand) => void;
  onHover: (index: number) => void;
}): JSX.Element | null {
  const ref = React.useRef<HTMLDivElement>(null);

  // Keep the highlighted row in view as the user arrows through the list.
  React.useEffect(() => {
    const el = ref.current?.querySelector(`[data-idx="${active}"]`);
    el?.scrollIntoView({ block: "nearest" });
  }, [active]);

  if (commands.length === 0) return null;
  return (
    <div className="cmd-menu" ref={ref}>
      {commands.map((cmd, i) => (
        <div
          key={cmd.command}
          data-idx={i}
          className={`cmd-item ${i === active ? "active" : ""}`}
          onMouseDown={(e) => {
            // mousedown (not click) so the composer textarea never loses focus.
            e.preventDefault();
            onPick(cmd);
          }}
          onMouseEnter={() => onHover(i)}
        >
          <span className="cmd-name">{cmd.command}</span>
          <span className="cmd-desc">{cmd.description}</span>
        </div>
      ))}
    </div>
  );
}
