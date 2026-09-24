// One 16x16 stroke glyph per node kind, drawn on the node's header badge and in
// the add-node menu. Paths only, so they take whatever colour the CSS gives.

export interface Glyph {
  /** Stroked outline. */
  d: string;
  /** Solid parts, when the shape needs them. */
  fill?: string;
}

export const GLYPHS: Record<string, Glyph> = {
  start: { d: "", fill: "M5 3.2 L12.8 8 L5 12.8 Z" },
  end: { d: "M4 14 V2.5 M4 3 H12 L10.2 5.6 L12 8.2 H4" },
  model: {
    d:
      "M4.5 4.5 H11.5 V11.5 H4.5 Z M6.5 2 V4.5 M9.5 2 V4.5 M6.5 11.5 V14 M9.5 11.5 V14 " +
      "M2 6.5 H4.5 M2 9.5 H4.5 M11.5 6.5 H14 M11.5 9.5 H14",
  },
  profession: {
    d: "M3.5 5.5 H12.5 V13 H3.5 Z M8 5.5 V3 M1.8 8.5 V10.5 M14.2 8.5 V10.5 M6 11 H10",
    fill: "M7.1 2.6 A0.9 0.9 0 1 0 8.9 2.6 A0.9 0.9 0 1 0 7.1 2.6 Z " +
      "M5 8.5 A1 1 0 1 0 7 8.5 A1 1 0 1 0 5 8.5 Z M9 8.5 A1 1 0 1 0 11 8.5 A1 1 0 1 0 9 8.5 Z",
  },
  // The agent's head with nothing around it: no antenna, no ears.
  clean_agent: {
    d: "M3.5 4.5 H12.5 V12.5 H3.5 Z",
    fill: "M5 8.5 A1 1 0 1 0 7 8.5 A1 1 0 1 0 5 8.5 Z M9 8.5 A1 1 0 1 0 11 8.5 A1 1 0 1 0 9 8.5 Z",
  },
  custom_profession: {
    d: "M2.5 4 H13.5 V12.5 H2.5 Z M9 7 H11.5 M9 9.5 H11.5 M4 11 Q6 8.9 8 11",
    fill: "M4.6 7.2 A1.4 1.4 0 1 0 7.4 7.2 A1.4 1.4 0 1 0 4.6 7.2 Z",
  },
  tools: {
    d:
      "M10.2 2.4 A3.2 3.2 0 0 0 6.9 6.6 L2.7 10.8 A1.5 1.5 0 0 0 4.9 13 L9.1 8.8 " +
      "A3.2 3.2 0 0 0 13.3 5.5 L11.4 7.4 L9.5 6.9 L9 5 Z",
  },
  sequence: {
    d: "M6 4 H13.5 M6 8 H13.5 M6 12 H13.5",
    fill:
      "M2.1 4 A0.9 0.9 0 1 0 3.9 4 A0.9 0.9 0 1 0 2.1 4 Z M2.1 8 A0.9 0.9 0 1 0 3.9 8 " +
      "A0.9 0.9 0 1 0 2.1 8 Z M2.1 12 A0.9 0.9 0 1 0 3.9 12 A0.9 0.9 0 1 0 2.1 12 Z",
  },
  decision: {
    d: "M8 2.2 L13.8 8 L8 13.8 L2.2 8 Z M8 5.5 V8.5",
    fill: "M7.2 10.6 A0.8 0.8 0 1 0 8.8 10.6 A0.8 0.8 0 1 0 7.2 10.6 Z",
  },
  loop: { d: "M12.6 8 A4.6 4.6 0 1 1 11.2 4.7 M11.6 2.2 V5.1 H8.7" },
  user_chat: { d: "M2.5 3.5 H13.5 V10.5 H7.2 L4.5 13.2 V10.5 H2.5 Z M5 6.3 H11 M5 8.3 H9" },
  var_set: { d: "M3 13 L3.5 10.3 L10.6 3.2 L12.8 5.4 L5.7 12.5 Z M9.3 4.5 L11.5 6.7" },
  var_get: {
    d: "M1.8 8 Q8 1.8 14.2 8 Q8 14.2 1.8 8 Z",
    fill: "M6 8 A2 2 0 1 0 10 8 A2 2 0 1 0 6 8 Z",
  },
  print: { d: "M2.5 3.5 H13.5 V12.5 H2.5 Z M5 6.5 L7 8 L5 9.5 M8.5 10 H11" },
};

const FALLBACK: Glyph = { d: "M3.5 3.5 H12.5 V12.5 H3.5 Z" };

export function glyphFor(kind: string): Glyph {
  return GLYPHS[kind] ?? FALLBACK;
}
