import * as React from "react";

/** A VS Code codicon by name (https://microsoft.github.io/vscode-codicons/). Decorative: name the control, not the glyph. */
export function Codicon({
  name,
  size = 16,
  className = "",
  spin = false,
}: {
  name: string;
  size?: number;
  className?: string;
  spin?: boolean;
}): JSX.Element {
  return (
    <i
      className={`codicon codicon-${name}${spin ? " codicon-modifier-spin" : ""}${className ? ` ${className}` : ""}`}
      style={{ fontSize: size }}
      aria-hidden="true"
    />
  );
}
