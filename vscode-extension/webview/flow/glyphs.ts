// One 16x16 stroke glyph per node kind, drawn on the node's header badge and in
// the add-node menu. Paths only, so they take whatever colour the CSS gives.

export interface Glyph {
  /** Stroked outline. */
  d: string;
  /** Solid parts, when the shape needs them. */
  fill?: string;
}

const CONVERT_GLYPH: Glyph = { d: "M2.5 5.5 H12 L9.5 3 M13.5 10.5 H4 L6.5 13" };

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
  // -- functions --------------------------------------------------------
  math: {
    d:
      "M2.5 4.8 H7 M4.75 2.5 V7.1 M9.3 4.8 H13.8 M9.3 11 H13.8 M9.3 13 H13.8 " +
      "M3 9.7 L6.6 13.3 M6.6 9.7 L3 13.3",
  },
  compare: { d: "M6.5 3.2 L2.5 6.4 L6.5 9.6 M2.5 12.8 H6.5 M9.5 3.2 L13.5 6.4 L9.5 9.6 M9.5 12.8 H13.5" },
  logic: { d: "M3 3.5 H7.6 A4.5 4.5 0 0 1 7.6 12.5 H3 Z M12.1 8 H14.3 M1.2 6 H3 M1.2 10 H3" },
  select: {
    d: "M2.5 8 H6 L9.8 4 H13.5 M6 8 L9.8 12 H13.5",
    fill: "M5 8 A1 1 0 1 0 7 8 A1 1 0 1 0 5 8 Z",
  },
  append: { d: "M2.5 4.5 H8 M2.5 11.5 H8 M8 4.5 Q11.2 4.5 11.2 8 Q11.2 11.5 8 11.5 M11.2 8 H14" },
  format: {
    d:
      "M6 2.5 Q4 2.5 4 4.5 V6.5 Q4 8 2.5 8 Q4 8 4 9.5 V11.5 Q4 13.5 6 13.5 " +
      "M10 2.5 Q12 2.5 12 4.5 V6.5 Q12 8 13.5 8 Q12 8 12 9.5 V11.5 Q12 13.5 10 13.5",
  },
  text_case: { d: "M2.5 3.5 H10 M6.25 3.5 V13 M12.3 8.5 V13.5 M10.6 11.6 L12.3 13.5 L14 11.6" },
  text_replace: { d: "M2.5 5.5 H11.5 L9 3 M13.5 10.5 H4.5 L7 13" },
  text_check: { d: "M11.5 7 A4.5 4.5 0 1 1 2.5 7 A4.5 4.5 0 1 1 11.5 7 Z M10.3 10.3 L14 14" },
  text_length: {
    d: "M2 10.5 L10.5 2 L14 5.5 L5.5 14 Z M5 9 L6.5 10.5 M7 7 L8.5 8.5 M9 5 L10.5 6.5",
  },
  substring: {
    d:
      "M6.5 5 A2 2 0 1 1 2.5 5 A2 2 0 1 1 6.5 5 Z M6.5 11 A2 2 0 1 1 2.5 11 A2 2 0 1 1 " +
      "6.5 11 Z M6.2 6.2 L14 12.5 M6.2 9.8 L14 3.5",
  },
  split: { d: "M2.5 8 H6 M6 8 L9 4 H13.5 M6 8 H13.5 M6 8 L9 12 H13.5" },
  to_int: CONVERT_GLYPH,
  to_float: CONVERT_GLYPH,
  to_text: CONVERT_GLYPH,
  to_bool: CONVERT_GLYPH,
};

const FALLBACK: Glyph = { d: "M3.5 3.5 H12.5 V12.5 H3.5 Z" };

export function glyphFor(kind: string): Glyph {
  return GLYPHS[kind] ?? FALLBACK;
}
