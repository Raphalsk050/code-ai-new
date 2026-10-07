// Where the caret is in a text field, in the window's pixels. A field does not
// say, so the text up to the caret is laid out again in a copy of the field,
// out of sight, and the end of that is where the caret is.

/** What makes the copy wrap its lines where the field does. */
const COPIED = [
  "boxSizing", "width", "fontFamily", "fontSize", "fontWeight", "fontStyle", "letterSpacing", "lineHeight", "wordSpacing",
  "textIndent", "textTransform", "tabSize", "paddingTop", "paddingRight", "paddingBottom", "paddingLeft",
  "borderTopWidth", "borderRightWidth", "borderBottomWidth", "borderLeftWidth",
] as const;

let copy: HTMLDivElement | null = null;

export function caretPoint(field: HTMLTextAreaElement): { x: number; y: number } {
  if (!copy?.isConnected) {
    copy = document.createElement("div");
    copy.setAttribute("aria-hidden", "true");
    document.body.appendChild(copy);
  }
  const style = getComputedStyle(field);
  copy.style.cssText =
    "position:absolute;left:-9999px;top:0;visibility:hidden;pointer-events:none;white-space:pre-wrap;overflow-wrap:break-word;border-style:solid;";
  for (const name of COPIED) copy.style[name] = style[name];
  copy.textContent = field.value.slice(0, field.selectionEnd);
  const mark = copy.appendChild(document.createElement("span"));
  // Something to measure that takes no room of its own.
  mark.textContent = "​";
  const at = mark.getBoundingClientRect();
  const from = copy.getBoundingClientRect();
  const box = field.getBoundingClientRect();
  return { x: box.left + at.left - from.left, y: box.top + at.top - from.top + at.height / 2 - field.scrollTop };
}
