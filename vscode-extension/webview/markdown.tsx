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
      hljs.highlightElement(el as HTMLElement);
    });
  }, [html]);

  return <div className="markdown" ref={ref} dangerouslySetInnerHTML={{ __html: html }} />;
}
