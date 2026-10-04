// Design tokens: the only place a colour, size or radius is decided.
// Colours come from the user's VS Code theme; the fallbacks are Dark Modern and
// apply only outside VS Code (tests, previews). Mirrors the "Code-AI" design system.

export const TOKENS = `
:root {
  /* surfaces */
  --bg: var(--vscode-editor-background, #1f1f1f);
  --bg-rail: var(--vscode-sideBar-background, #181818);
  --surface: var(--vscode-editorWidget-background, #252526);
  --surface-input: var(--vscode-input-background, #313131);
  --bg-glass: color-mix(in srgb, var(--bg) 65%, transparent);
  /* Nearly solid: what sits right behind the composer, where text must not read. */
  --bg-veil: color-mix(in srgb, var(--bg) 86%, transparent);
  --surface-input-glass: color-mix(in srgb, var(--surface-input) 80%, transparent);
  --hover: var(--vscode-list-hoverBackground, rgba(255,255,255,.06));
  --code-bg: var(--vscode-textCodeBlock-background, rgba(127,127,127,.12));
  --term-bg: var(--vscode-terminal-background, var(--bg));
  --shadow: var(--vscode-widget-shadow, rgba(0,0,0,.35));

  /* lines */
  --border: var(--vscode-panel-border, #2b2b2b);
  --border-strong: var(--vscode-input-border, #3c3c3c);
  --focus: var(--vscode-focusBorder, #0078d4);

  /* text */
  --fg: var(--vscode-foreground, #cccccc);
  --fg-strong: var(--vscode-editor-foreground, #e7e7e7);
  --fg-muted: var(--vscode-descriptionForeground, #9d9d9d);

  /* intent */
  --accent: var(--vscode-textLink-foreground, #4daafc);
  --button: var(--vscode-button-background, #0078d4);
  --button-hover: var(--vscode-button-hoverBackground, var(--button));
  --button-fg: var(--vscode-button-foreground, #ffffff);
  --success: var(--vscode-testing-iconPassed, #73c991);
  --warn: var(--vscode-editorWarning-foreground, #cca700);
  --danger: var(--vscode-errorForeground, #f85149);
  --accent-soft: color-mix(in srgb, var(--accent) 12%, transparent);
  --success-soft: color-mix(in srgb, var(--success) 12%, transparent);
  --warn-soft: color-mix(in srgb, var(--warn) 10%, transparent);
  --danger-soft: color-mix(in srgb, var(--danger) 10%, transparent);

  /* turn boxes */
  --turn-user-bg: color-mix(in srgb, var(--accent) 7%, transparent);
  --turn-user-line: color-mix(in srgb, var(--accent) 30%, var(--border));
  --turn-done-bg: color-mix(in srgb, var(--success) 7%, transparent);
  --turn-done-line: color-mix(in srgb, var(--success) 65%, var(--border));

  /* type: four sizes, nothing else */
  --font-ui: var(--vscode-font-family, "Segoe WPC", "Segoe UI", system-ui, sans-serif);
  --font-mono: var(--vscode-editor-font-family, "Cascadia Code", Consolas, monospace);
  --fs-label: 11px;
  --fs-small: 12px;
  --fs-body: var(--vscode-font-size, 13px);
  --fs-title: 14px;

  /* space: 4px scale */
  --space-1: 2px;
  --space-2: 4px;
  --space-3: 8px;
  --space-4: 12px;
  --space-5: 16px;
  --space-6: 24px;

  /* shape */
  --radius-sm: 15px;
  --radius-md: 15px;
  --radius-lg: 20px;
  --radius-pill: 999px;
}
`;
