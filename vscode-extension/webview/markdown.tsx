import DOMPurify from "dompurify";
import hljs from "highlight.js/lib/common";
import { marked } from "marked";
import * as React from "react";

marked.setOptions({ gfm: true, breaks: true });

/**
 * Render trusted-but-untrusted-shaped model output as markdown. We parse with
 * `marked`, sanitize the HTML with DOMPurify, then highlight code blocks with
 * highlight.js after mount. Webview CSP already blocks inline scripts, and
 * sanitization strips anything active, so this is safe for a webview.
 */
export function Markdown({ text }: { text: string }): JSX.Element {
  const ref = React.useRef<HTMLDivElement>(null);

  const html = React.useMemo(() => {
    const raw = marked.parse(text, { async: false }) as string;
    return DOMPurify.sanitize(raw);
  }, [text]);

  React.useEffect(() => {
    ref.current?.querySelectorAll("pre code").forEach((el) => {
      const code = el as HTMLElement;
      hljs.highlightElement(code);
      addLineNumbers(code);
    });
  }, [html]);

  return <div className="markdown" ref={ref} dangerouslySetInnerHTML={{ __html: html }} />;
}

/**
 * Prepend a line-number gutter to a highlighted code block. The numbers live in
 * a sibling column that scrolls with the code horizontally but stays pinned to
 * the left, mirroring the diff view's gutter. Idempotent per <pre>.
 */
function addLineNumbers(code: HTMLElement): void {
  const pre = code.parentElement;
  if (!pre || pre.dataset.lnWrapped) return;
  const lines = (code.textContent ?? "").replace(/\n$/, "").split("\n");
  const gutter = document.createElement("span");
  gutter.className = "code-gutter";
  gutter.setAttribute("aria-hidden", "true");
  gutter.textContent = lines.map((_, i) => String(i + 1)).join("\n");
  pre.classList.add("has-line-numbers");
  pre.insertBefore(gutter, code);
  pre.dataset.lnWrapped = "1";
}
