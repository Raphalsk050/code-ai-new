// SGR colour runs in command output, as spans a stylesheet can colour. Only
// what a build log uses: the 16 colours, bold, dim and reset. Everything else
// in an escape is dropped, never shown as text.

export interface AnsiSpan {
  text: string;
  /** "red", "green", ... or "" for the default colour. */
  color: string;
  bold: boolean;
  dim: boolean;
}

const COLORS = ["black", "red", "green", "yellow", "blue", "magenta", "cyan", "white"];

export function parseAnsi(input: string): AnsiSpan[] {
  const spans: AnsiSpan[] = [];
  let color = "";
  let bold = false;
  let dim = false;
  const pattern = /\x1b\[([0-9;]*)m|\x1b\[[0-?]*[ -/]*[@-~]|\x1b[^[]/g;
  let last = 0;
  const push = (text: string) => {
    if (!text) return;
    const prev = spans[spans.length - 1];
    if (prev && prev.color === color && prev.bold === bold && prev.dim === dim) prev.text += text;
    else spans.push({ text, color, bold, dim });
  };
  for (let match = pattern.exec(input); match; match = pattern.exec(input)) {
    push(input.slice(last, match.index));
    last = pattern.lastIndex;
    if (match[1] === undefined) continue;
    const codes = match[1] === "" ? [0] : match[1].split(";").map((c) => Number(c) || 0);
    for (let i = 0; i < codes.length; i++) {
      const code = codes[i];
      if (code === 0) {
        color = "";
        bold = false;
        dim = false;
      } else if (code === 1) bold = true;
      else if (code === 2) dim = true;
      else if (code === 22) bold = dim = false;
      else if (code === 39) color = "";
      else if (code >= 30 && code <= 37) color = COLORS[code - 30];
      else if (code >= 90 && code <= 97) color = COLORS[code - 90];
      else if (code === 38 || code === 48) {
        // 256-colour and true-colour forms: skip their arguments.
        i += codes[i + 1] === 5 ? 2 : codes[i + 1] === 2 ? 4 : 0;
      }
    }
  }
  push(input.slice(last));
  return spans;
}

/** The output as plain text, for copying. */
export function stripAnsi(input: string): string {
  return parseAnsi(input)
    .map((span) => span.text)
    .join("");
}
