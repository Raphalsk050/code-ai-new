import DOMPurify from "dompurify";
import * as React from "react";

import { renderBlocks } from "./markdown-render";

const sanitize = (html: string): string => DOMPurify.sanitize(html);

/**
 * Model output as markdown: parsed with `marked`, sanitized with DOMPurify.
 * The webview's CSP already blocks inline scripts and sanitizing strips
 * anything active, so this is safe to put in the page.
 *
 * Rendered block by block. While an answer streams only its last block
 * changes, and React leaves the DOM of the others alone: what is already on
 * screen does not blink, lose its selection or move under a reader.
 */
export function Markdown({ text, streaming = false }: { text: string; streaming?: boolean }): JSX.Element {
  const cache = React.useRef(new Map<string, string>());
  const blocks = React.useMemo(() => renderBlocks(text, streaming, cache.current, sanitize), [text, streaming]);
  return (
    <div className="markdown">
      {blocks.map((block) => (
        <MarkdownBlock key={block.key} html={block.html} />
      ))}
    </div>
  );
}

const MarkdownBlock = React.memo(function MarkdownBlock({ html }: { html: string }) {
  return <div className="md-block" dangerouslySetInnerHTML={{ __html: html }} />;
});
