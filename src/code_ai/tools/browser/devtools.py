"""What the browser's developer tools would show, for the agent to read.

The page text and the numbered elements answer "what is on screen". Working on
a web app needs the rest of what DevTools shows: the element tree and its HTML,
the styles that land on an element, what the console printed, which requests
went out and how they came back, what the page keeps in storage, the
accessibility tree, and how long the page took to load.

Everything here only reads, and everything is bounded below the tool-output
budget (``budgets.max_tool_output_chars``, 12,000 by default) so a payload
arrives whole instead of being cut off mid-JSON.

The console and the network cannot be read after the fact - they happen - so
:class:`DevtoolsRecorder` listens on every page from the moment the session
first touches it, and keeps the most recent entries.

Credentials stay out of the model's context here as they do everywhere in the
browser tools: cookie values are never read out, storage values that look like
tokens are hidden, and token-shaped strings are masked in all returned text.
"""

from __future__ import annotations

import json
import re
import weakref
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from code_ai.core.errors import ToolArgumentError

# The panels browser_inspect can read, in the order DevTools shows them.
INSPECT_ASPECTS = (
    "dom",
    "html",
    "styles",
    "console",
    "network",
    "storage",
    "accessibility",
    "performance",
)

# Console levels from least to most severe; a read asks for "this and worse".
LOG_LEVELS = ("debug", "log", "warning", "error")
_SEVERITY = {"debug": 0, "verbose": 0, "log": 1, "info": 1, "warning": 2, "error": 3, "assert": 3}

MAX_TREE_CHARS = 8_000
MAX_HTML_CHARS = 9_000
MAX_DOM_NODES = 500
DEFAULT_DOM_DEPTH = 8
MAX_DOM_DEPTH = 40
# Entries kept per log. The oldest go first: what the agent is debugging is
# almost always what just happened.
MAX_LOG_ENTRIES = 300
DEFAULT_LOG_LIMIT = 50
MAX_MESSAGE_CHARS = 500
MAX_URL_CHARS = 300
MAX_STYLE_RULES = 30
MAX_STORAGE_ENTRIES = 50
MAX_STORAGE_VALUE_CHARS = 200
MAX_EVAL_CHARS = 6_000

# What the Computed pane is usually opened for: layout, box, type, visibility.
DEFAULT_STYLE_PROPERTIES = (
    "display",
    "position",
    "top",
    "right",
    "bottom",
    "left",
    "width",
    "height",
    "margin",
    "padding",
    "border",
    "box-sizing",
    "color",
    "background-color",
    "background-image",
    "font-family",
    "font-size",
    "font-weight",
    "line-height",
    "text-align",
    "opacity",
    "visibility",
    "z-index",
    "overflow",
    "flex-direction",
    "justify-content",
    "align-items",
    "gap",
    "grid-template-columns",
    "transform",
    "pointer-events",
    "cursor",
)


# ---------------------------------------------------------------- redaction

_JWT = re.compile(r"\beyJ[\w-]{6,}\.[\w-]{6,}\.[\w-]{6,}")
_AUTH_SCHEME = re.compile(r"(?i)\b(bearer|basic|token)\s+[\w\-.~+/=]{12,}")
_SECRET_PARAM = re.compile(
    r"(?i)([?&;](?:access_token|refresh_token|id_token|token|auth|api_?key|key|password"
    r"|passwd|secret|signature|sig|code|session|sid)=)[^&#\s\"'<>]+"
)
_SENSITIVE_KEY = re.compile(
    r"(?i)(token|auth|secret|passw|session|jwt|bearer|credential|api[_-]?key|cookie|csrf|xsrf)"
)
# A long run with no spaces is an id, a hash or a token. Hiding the odd harmless
# id costs little; showing a token costs the credential.
_OPAQUE_VALUE = re.compile(r"^[A-Za-z0-9_\-.+/=]{32,}$")


def mask(text: str) -> str:
    """Hide token-shaped substrings in text that is about to reach the model."""

    text = _JWT.sub("<hidden token>", text)
    text = _AUTH_SCHEME.sub(lambda match: f"{match.group(1)} <hidden>", text)
    return _SECRET_PARAM.sub(lambda match: f"{match.group(1)}<hidden>", text)


def clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


def _storage_value(key: str, value: str) -> str:
    if _SENSITIVE_KEY.search(key) or _OPAQUE_VALUE.match(value.strip()) or _JWT.search(value):
        return f"<hidden, {len(value)} chars>"
    return clip(mask(value), MAX_STORAGE_VALUE_CHARS)


# ---------------------------------------------------------------- recorder


def _never_raises(handler: Any) -> Any:
    """Keep a listener's failure inside the listener.

    It runs inside Playwright's event dispatch, where an exception would be
    logged against the page rather than reaching anyone - and a malformed
    console message is not worth disturbing the page for.
    """

    def guarded(self: DevtoolsRecorder, payload: Any) -> None:
        try:
            handler(self, payload)
        except Exception:  # noqa: BLE001 - see above
            return

    # Each keeps its own name, and that is load-bearing: Playwright caches the
    # wrapper it builds around a bound-method listener under the method's
    # name, so listeners all called "guarded" would share the first one's
    # wrapper - every page event would reach the console handler.
    guarded.__name__ = handler.__name__
    guarded.__qualname__ = handler.__qualname__
    return guarded


def _failed(entry: dict[str, Any]) -> bool:
    return "failed" in entry or int(entry.get("status") or 0) >= 400


def _public(entry: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in entry.items() if key != "seq"}


@dataclass
class DevtoolsRecorder:
    """The console and network logs, captured as they happen on each page."""

    console: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=MAX_LOG_ENTRIES))
    network: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=MAX_LOG_ENTRIES))
    # Counts every console entry ever logged, so "what did this script print"
    # stays answerable after the bounded log has dropped its oldest lines.
    seq: int = 0
    _watched: weakref.WeakSet[Any] = field(default_factory=weakref.WeakSet, repr=False)
    _inflight: dict[int, dict[str, Any]] = field(default_factory=dict, repr=False)

    def watch(self, page: Any) -> None:
        """Start listening on ``page``, once, however often it is handed over."""

        if page in self._watched:
            return
        self._watched.add(page)
        page.on("console", self._on_console)
        page.on("pageerror", self._on_page_error)
        page.on("response", self._on_response)
        page.on("requestfinished", self._on_request_finished)
        page.on("requestfailed", self._on_request_failed)

    @_never_raises
    def _on_console(self, message: Any) -> None:
        location = message.location or {}
        source = str(location.get("url") or "")
        line = location.get("lineNumber")
        if source and isinstance(line, int):
            source = f"{source}:{line + 1}"  # Playwright counts lines from 0
        self._log(str(message.type), str(message.text), source)

    @_never_raises
    def _on_page_error(self, error: Any) -> None:
        self._log("error", f"Uncaught {error}", "")

    def _log(self, level: str, text: str, source: str) -> None:
        self.seq += 1
        entry: dict[str, Any] = {
            "seq": self.seq,
            "level": level,
            "text": clip(mask(text), MAX_MESSAGE_CHARS),
        }
        if source:
            entry["source"] = clip(mask(source), MAX_URL_CHARS)
        self.console.append(entry)

    @_never_raises
    def _on_response(self, response: Any) -> None:
        request = response.request
        entry = self._request_entry(request)
        entry["status"] = response.status
        mime = (response.headers or {}).get("content-type", "")
        if mime:
            entry["mime"] = mime.split(";")[0].strip()
        self.network.append(entry)
        # A request that never finishes (the page navigated away mid-flight)
        # leaves its entry here, and the cap is what stops that from growing.
        if len(self._inflight) > MAX_LOG_ENTRIES:
            self._inflight.clear()
        self._inflight[id(request)] = entry

    @_never_raises
    def _on_request_finished(self, request: Any) -> None:
        entry = self._inflight.pop(id(request), None)
        if entry is None:
            return
        end = (request.timing or {}).get("responseEnd", -1)
        if isinstance(end, int | float) and end >= 0:
            entry["ms"] = round(end)

    @_never_raises
    def _on_request_failed(self, request: Any) -> None:
        entry = self._inflight.pop(id(request), None)
        if entry is None:
            entry = self._request_entry(request)
            self.network.append(entry)
        entry["failed"] = str(request.failure or "failed")

    @staticmethod
    def _request_entry(request: Any) -> dict[str, Any]:
        return {
            "method": str(request.method),
            "url": clip(mask(str(request.url)), MAX_URL_CHARS),
            "type": str(request.resource_type),
        }

    def since(self, seq: int) -> list[dict[str, Any]]:
        """Console entries logged after ``seq``, oldest first."""

        return [_public(entry) for entry in self.console if entry["seq"] > seq]

    def read(
        self,
        aspect: str,
        *,
        level: str = "log",
        contains: str = "",
        failed_only: bool = False,
        limit: int = DEFAULT_LOG_LIMIT,
        clear: bool = False,
    ) -> dict[str, Any]:
        """The newest ``limit`` entries of one log that pass its filters."""

        if aspect == "console":
            log = self.console
            floor = _SEVERITY.get(level, 1)
            matching = [entry for entry in log if _SEVERITY.get(entry["level"], 1) >= floor]
        else:
            log = self.network
            needle = contains.lower()
            matching = [
                entry
                for entry in log
                if (not needle or needle in entry["url"].lower())
                and (not failed_only or _failed(entry))
            ]
        shown = [_public(entry) for entry in matching[-limit:]]
        payload: dict[str, Any] = {"entries": shown, "matching": len(matching)}
        if len(matching) > len(shown):
            payload["note"] = (
                f"Showing the newest {len(shown)} of {len(matching)}; raise limit for more."
            )
        elif not matching and not len(self._watched):
            payload["note"] = (
                "Nothing captured yet: logging starts when the browser opens its first page."
            )
        if clear:
            log.clear()
            payload["cleared"] = True
        return payload


# ---------------------------------------------------------------- in-page readers
#
# Each runs inside the page and returns plain JSON. A selector the page cannot
# parse comes back as {error}, so it is reported as the argument mistake it is
# rather than as a browser failure.

DOM_TREE_JS = r"""
(args) => {
  const [selector, maxDepth, maxNodes, maxChars] = args;
  let root;
  try {
    root = selector
      ? document.querySelector(selector)
      : (document.body || document.documentElement);
  } catch (error) {
    return {error: 'invalid selector'};
  }
  if (!root) return {found: false};
  const OPAQUE = new Set([
    'script', 'style', 'noscript', 'template', 'svg', 'canvas', 'video', 'audio',
    'iframe', 'object', 'embed',
  ]);
  const ATTRS = [
    'href', 'src', 'type', 'name', 'role', 'aria-label', 'aria-expanded', 'aria-hidden',
    'placeholder', 'alt', 'title', 'for', 'action', 'method', 'data-testid', 'disabled',
    'checked', 'value',
  ];
  const squash = (text, limit) => {
    const flat = String(text).replace(/\s+/g, ' ').trim();
    return flat.length > limit ? flat.slice(0, limit) + '…' : flat;
  };
  const secretInput = (el) => /^(password|hidden)$/i.test(el.getAttribute('type') || '');
  const describe = (el) => {
    let out = el.tagName.toLowerCase();
    if (el.id) out += '#' + el.id;
    const classes = typeof el.className === 'string' ? el.className.trim() : '';
    if (classes) out += '.' + classes.split(/\s+/).slice(0, 5).join('.');
    for (const name of ATTRS) {
      if (!el.hasAttribute(name)) continue;
      const value = el.getAttribute(name);
      if (name === 'value' && secretInput(el)) out += ' value=<hidden>';
      else out += value === '' ? ' ' + name : ' ' + name + '="' + squash(value, 80) + '"';
    }
    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') out += '  (hidden)';
    return out;
  };
  const lines = [];
  let nodes = 0;
  let chars = 0;
  let truncated = false;
  const emit = (depth, text) => {
    if (truncated) return false;
    if (nodes > maxNodes || chars > maxChars) {
      truncated = true;
      return false;
    }
    const line = '  '.repeat(depth) + text;
    lines.push(line);
    chars += line.length + 1;
    return true;
  };
  const shown = (node) => node.nodeType === Node.ELEMENT_NODE
    || (node.nodeType === Node.TEXT_NODE && node.textContent.trim() !== '');
  const walk = (node, depth) => {
    if (node.nodeType === Node.TEXT_NODE) {
      emit(depth, JSON.stringify(squash(node.textContent, 120)));
      return;
    }
    nodes += 1;
    const shadow = node.nodeType === Node.DOCUMENT_FRAGMENT_NODE;
    if (!emit(depth, shadow ? '#shadow-root' : describe(node))) return;
    if (!shadow && OPAQUE.has(node.tagName.toLowerCase())) return;
    const children = [...node.childNodes].filter(shown);
    if (!shadow && node.shadowRoot) children.unshift(node.shadowRoot);
    if (!children.length) return;
    if (depth >= maxDepth) {
      emit(depth + 1, '… ' + children.length + ' more inside (raise depth or pass a selector)');
      return;
    }
    for (const child of children) {
      if (truncated) return;
      walk(child, depth + 1);
    }
  };
  walk(root, 0);
  return {
    found: true,
    matches: selector ? document.querySelectorAll(selector).length : 1,
    tree: lines.join('\n'),
    truncated,
  };
}
"""

HTML_JS = r"""
(args) => {
  const [selector, keepScripts] = args;
  let root;
  try {
    root = selector ? document.querySelector(selector) : document.documentElement;
  } catch (error) {
    return {error: 'invalid selector'};
  }
  if (!root) return {found: false};
  const copy = root.cloneNode(true);
  const inside = (css) => [
    ...(copy.matches && copy.matches(css) ? [copy] : []),
    ...copy.querySelectorAll(css),
  ];
  if (!keepScripts) {
    for (const el of inside('script, style')) {
      const length = (el.textContent || '').length;
      if (length) el.textContent = '/* ' + length + ' chars omitted */';
    }
  }
  for (const el of inside('input')) {
    const secret = /^(password|hidden)$/i.test(el.getAttribute('type') || '');
    if (secret && el.hasAttribute('value')) el.setAttribute('value', '<hidden>');
  }
  const doctype = !selector && document.doctype ? '<!DOCTYPE ' + document.doctype.name + '>\n' : '';
  return {
    found: true,
    matches: selector ? document.querySelectorAll(selector).length : 1,
    html: doctype + copy.outerHTML,
  };
}
"""

STYLES_JS = r"""
(args) => {
  const [selector, properties, maxRules] = args;
  let matches;
  try {
    matches = document.querySelectorAll(selector);
  } catch (error) {
    return {error: 'invalid selector'};
  }
  if (!matches.length) return {found: false};
  const el = matches[0];
  const style = window.getComputedStyle(el);
  const computed = {};
  for (const name of properties) {
    const value = style.getPropertyValue(name);
    if (value !== '') computed[name] = value;
  }
  const secretInput = /^(password|hidden)$/i.test(el.getAttribute('type') || '');
  const attributes = {};
  for (const attr of el.attributes) {
    const value = attr.value.length > 200 ? attr.value.slice(0, 200) + '…' : attr.value;
    attributes[attr.name] = attr.name === 'value' && secretInput ? '<hidden>' : value;
  }
  const rules = [];
  let unreadable = 0;
  const collect = (list, source) => {
    for (const rule of list) {
      if (rules.length >= maxRules) return;
      if (rule.selectorText) {
        let hit = false;
        try { hit = el.matches(rule.selectorText); } catch (error) { hit = false; }
        if (hit) rules.push({selector: rule.selectorText, css: rule.style.cssText, source});
      } else if (rule.cssRules) {
        if (rule.media && !window.matchMedia(rule.media.mediaText).matches) continue;
        collect(rule.cssRules, source);
      }
    }
  };
  for (const sheet of document.styleSheets) {
    let list;
    try { list = sheet.cssRules; } catch (error) { unreadable += 1; continue; }
    collect(list, sheet.href || 'inline <style>');
  }
  const inline = el.getAttribute('style');
  if (inline) rules.push({selector: 'element.style', css: inline, source: 'style attribute'});
  const box = el.getBoundingClientRect();
  return {
    found: true,
    matches: matches.length,
    tag: el.tagName.toLowerCase(),
    attributes,
    box: {
      x: Math.round(box.left), y: Math.round(box.top),
      width: Math.round(box.width), height: Math.round(box.height),
    },
    computed,
    rules,
    unreadable,
  };
}
"""

STORAGE_JS = r"""
async () => {
  const read = (area) => {
    try {
      const store = area();
      const out = [];
      for (let i = 0; i < store.length; i += 1) {
        const key = store.key(i);
        out.push([key, store.getItem(key) || '']);
      }
      return out;
    } catch (error) {
      return [];
    }
  };
  const attempt = async (probe) => {
    try { return await probe(); } catch (error) { return []; }
  };
  return {
    local_storage: read(() => window.localStorage),
    session_storage: read(() => window.sessionStorage),
    indexeddb: await attempt(async () => (await indexedDB.databases())
      .map((db) => db.name + (db.version ? ' (v' + db.version + ')' : ''))),
    service_workers: await attempt(async () => (await navigator.serviceWorker.getRegistrations())
      .map((registration) => registration.scope)),
    cache_storage: await attempt(() => caches.keys()),
  };
}
"""

PERFORMANCE_JS = r"""
() => {
  const ms = (value) => Math.round(value);
  const nav = performance.getEntriesByType('navigation')[0];
  const navigation = nav ? {
    type: nav.type,
    status: nav.responseStatus,
    dns_ms: ms(nav.domainLookupEnd - nav.domainLookupStart),
    connect_ms: ms(nav.connectEnd - nav.connectStart),
    ttfb_ms: ms(nav.responseStart - nav.requestStart),
    download_ms: ms(nav.responseEnd - nav.responseStart),
    dom_content_loaded_ms: ms(nav.domContentLoadedEventEnd),
    load_ms: ms(nav.loadEventEnd),
    transfer_bytes: nav.transferSize,
  } : null;
  const paint = {};
  for (const entry of performance.getEntriesByType('paint')) {
    paint[entry.name] = ms(entry.startTime);
  }
  const resources = performance.getEntriesByType('resource');
  const byType = {};
  let bytes = 0;
  for (const entry of resources) {
    byType[entry.initiatorType] = (byType[entry.initiatorType] || 0) + 1;
    bytes += entry.transferSize || 0;
  }
  const slowest = [...resources].sort((a, b) => b.duration - a.duration).slice(0, 10)
    .map((entry) => ({
      url: entry.name.slice(0, 300), type: entry.initiatorType,
      ms: ms(entry.duration), bytes: entry.transferSize,
    }));
  const heap = performance.memory;
  return {
    navigation,
    paint,
    resources: {count: resources.length, transfer_bytes: bytes, by_type: byType, slowest},
    memory: heap ? {
      used_js_heap_mb: +(heap.usedJSHeapSize / 1048576).toFixed(1),
      total_js_heap_mb: +(heap.totalJSHeapSize / 1048576).toFixed(1),
    } : null,
    dom_nodes: document.getElementsByTagName('*').length,
  };
}
"""


# ---------------------------------------------------------------- panels


async def inspect_page(
    page: Any,
    context: Any,
    aspect: str,
    *,
    selector: str = "",
    depth: int = DEFAULT_DOM_DEPTH,
    css_properties: tuple[str, ...] = (),
    include_scripts: bool = False,
    offset: int = 0,
) -> dict[str, Any]:
    """One DevTools panel's worth of the current page (console/network aside)."""

    if aspect == "dom":
        return await _read_dom(page, selector, depth)
    if aspect == "html":
        return await _read_html(page, selector, include_scripts, offset)
    if aspect == "styles":
        return await _read_styles(page, selector, css_properties or DEFAULT_STYLE_PROPERTIES)
    if aspect == "accessibility":
        return await _read_accessibility(page, selector)
    if aspect == "storage":
        return await _read_storage(page, context)
    if aspect == "performance":
        return _read_performance(await page.evaluate(PERFORMANCE_JS))
    raise ToolArgumentError(f"aspect must be one of: {', '.join(INSPECT_ASPECTS)}.")


def _checked(result: Any, selector: str) -> dict[str, Any] | None:
    """The page's answer, or None when the selector matched nothing.

    A selector the page cannot parse is the caller's mistake and is raised as
    one. A selector that parses but matches nothing is an answer about the
    page, not an error, so it is returned for the model to act on.
    """

    if not isinstance(result, dict):
        return None
    if result.get("error"):
        raise ToolArgumentError(f'"{selector}" is not a valid CSS selector.')
    return result if result.get("found") else None


def _nothing_matches(selector: str) -> dict[str, Any]:
    return {
        "found": False,
        "note": f'Nothing on the page matches "{selector}". The dom aspect shows what is there.',
    }


def _first_of(matches: int) -> dict[str, Any]:
    if matches > 1:
        return {"matches": matches, "shown": "the first match"}
    return {}


async def _read_dom(page: Any, selector: str, depth: int) -> dict[str, Any]:
    result = _checked(
        await page.evaluate(DOM_TREE_JS, [selector, depth, MAX_DOM_NODES, MAX_TREE_CHARS]),
        selector,
    )
    if result is None:
        return _nothing_matches(selector)
    payload: dict[str, Any] = {"found": True, **_first_of(int(result.get("matches") or 1))}
    payload["tree"] = mask(str(result.get("tree") or ""))
    if result.get("truncated"):
        payload["truncated"] = True
        payload["next"] = "Cut off here. Pass a selector for the part you need, or lower depth."
    return payload


async def _read_html(
    page: Any, selector: str, include_scripts: bool, offset: int
) -> dict[str, Any]:
    result = _checked(await page.evaluate(HTML_JS, [selector, include_scripts]), selector)
    if result is None:
        return _nothing_matches(selector)
    html = mask(str(result.get("html") or ""))
    total = len(html)
    start = max(0, min(offset, total))
    chunk = html[start : start + MAX_HTML_CHARS]
    end = start + len(chunk)
    payload: dict[str, Any] = {"found": True, **_first_of(int(result.get("matches") or 1))}
    payload["html"] = chunk
    payload["total_chars"] = total
    if start or end < total:
        payload["range"] = [start, end]
    if end < total:
        payload["next"] = (
            f"{total - end:,} more chars: call again with offset={end}, "
            "or narrow it with a selector."
        )
    return payload


async def _read_styles(page: Any, selector: str, properties: tuple[str, ...]) -> dict[str, Any]:
    result = _checked(
        await page.evaluate(STYLES_JS, [selector, list(properties), MAX_STYLE_RULES]), selector
    )
    if result is None:
        return _nothing_matches(selector)
    rules = [
        {
            "selector": str(rule.get("selector", "")),
            "css": clip(mask(str(rule.get("css", ""))), 300),
            "source": clip(mask(str(rule.get("source", ""))), MAX_URL_CHARS),
        }
        for rule in result.get("rules") or []
        if isinstance(rule, dict)
    ]
    payload: dict[str, Any] = {
        "found": True,
        **_first_of(int(result.get("matches") or 1)),
        "tag": result.get("tag"),
        "attributes": {
            str(name): mask(str(value)) for name, value in (result.get("attributes") or {}).items()
        },
        "box": result.get("box"),
        "computed": result.get("computed") or {},
        # Stylesheet order, which is cascade order: at equal specificity the
        # later rule wins, and element.style beats them all.
        "rules": rules,
    }
    unreadable = int(result.get("unreadable") or 0)
    if unreadable:
        payload["note"] = (
            f"{unreadable} stylesheet(s) from other origins cannot be read from the page, so "
            "their rules are not listed; the computed values already include them."
        )
    return payload


_PASSWORD_VALUES_JS = r"""
() => [...document.querySelectorAll('input[type=password]')].map((el) => el.value).filter(Boolean)
"""


def _hide_passwords(snapshot: str, secrets: Any) -> str:
    """Take the password fields' values back out of an accessibility snapshot.

    The snapshot reads every textbox's value out, and for a password field the
    value is the password. The values are fetched only to be found and removed
    here; they never leave this function. Both spellings are looked for, as
    typed and as the snapshot quotes them, and a line that still holds one
    after the value is cut has the secret itself replaced - so an unexpected
    layout costs readability, never the password.
    """

    variants = {
        spelling
        for secret in (secrets if isinstance(secrets, list) else [])
        if isinstance(secret, str) and secret
        for spelling in (secret, json.dumps(secret)[1:-1])
    }
    if not variants:
        return snapshot
    lines = []
    for line in snapshot.splitlines():
        if any(spelling in line for spelling in variants):
            line = re.sub(r"^(\s*- textbox\b.*?: ).*$", r"\1<hidden>", line)
            for spelling in variants:
                line = line.replace(spelling, "<hidden>")
        lines.append(line)
    return "\n".join(lines)


async def _read_accessibility(page: Any, selector: str) -> dict[str, Any]:
    locator = page.locator(selector or "body")
    try:
        count = await locator.count()
    except Exception as exc:  # noqa: BLE001
        message = str(exc).lower()
        if "selector" in message and "closed" not in message:
            raise ToolArgumentError(f'"{selector}" is not a valid selector.') from exc
        raise
    if not count:
        return _nothing_matches(selector)
    snapshot = str(await locator.first.aria_snapshot())
    snapshot = mask(_hide_passwords(snapshot, await page.evaluate(_PASSWORD_VALUES_JS)))
    payload: dict[str, Any] = {"found": True, **_first_of(count)}
    payload["tree"] = clip(snapshot, MAX_TREE_CHARS)
    if len(snapshot) > MAX_TREE_CHARS:
        payload["truncated"] = True
        payload["next"] = "Cut off here. Pass a selector for the part you need."
    return payload


def _expiry(value: Any) -> str:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return "unknown"
    if seconds < 0:
        return "session"
    return datetime.fromtimestamp(seconds, tz=UTC).strftime("%Y-%m-%d %H:%M UTC")


def _storage_area(entries: Any) -> dict[str, Any]:
    pairs = [pair for pair in entries or [] if isinstance(pair, list | tuple) and len(pair) == 2]
    shown = {
        str(key): _storage_value(str(key), str(value)) for key, value in pairs[:MAX_STORAGE_ENTRIES]
    }
    area: dict[str, Any] = {"count": len(pairs), "items": shown}
    if len(pairs) > len(shown):
        area["note"] = f"Showing the first {len(shown)}."
    return area


async def _read_storage(page: Any, context: Any) -> dict[str, Any]:
    cookies: list[dict[str, Any]] = []
    url = str(page.url)
    if context is not None and url.startswith(("http://", "https://")):
        for cookie in (await context.cookies([url]))[:MAX_STORAGE_ENTRIES]:
            cookies.append(
                {
                    "name": cookie.get("name"),
                    "domain": cookie.get("domain"),
                    "path": cookie.get("path"),
                    "expires": _expiry(cookie.get("expires")),
                    "http_only": cookie.get("httpOnly"),
                    "secure": cookie.get("secure"),
                    "same_site": cookie.get("sameSite"),
                    # The value is the login itself. Its length is enough to
                    # tell "set" from "empty", which is all debugging needs.
                    "value": f"<hidden, {len(str(cookie.get('value') or ''))} chars>",
                }
            )
    stored = await page.evaluate(STORAGE_JS)
    stored = stored if isinstance(stored, dict) else {}
    return {
        "cookies": cookies,
        "local_storage": _storage_area(stored.get("local_storage")),
        "session_storage": _storage_area(stored.get("session_storage")),
        "indexeddb": list(stored.get("indexeddb") or []),
        "service_workers": [mask(str(scope)) for scope in stored.get("service_workers") or []],
        "cache_storage": list(stored.get("cache_storage") or []),
        "note": (
            "Cookie values, and stored values that look like credentials, are hidden and "
            "shown only by length."
        ),
    }


def _read_performance(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    for entry in (result.get("resources") or {}).get("slowest") or []:
        if isinstance(entry, dict):
            entry["url"] = mask(str(entry.get("url", "")))
    return result


def render_value(value: Any) -> dict[str, Any]:
    """A console result as bounded text, the way the console prints it."""

    if value is None:
        kind = "null"
    elif isinstance(value, bool):
        kind = "boolean"
    elif isinstance(value, int | float):
        kind = "number"
    elif isinstance(value, str):
        kind = "string"
    elif isinstance(value, list):
        kind = "array"
    else:
        kind = "object"
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = mask(text)
    payload: dict[str, Any] = {"type": kind, "value": text[:MAX_EVAL_CHARS]}
    if len(text) > MAX_EVAL_CHARS:
        payload["value_truncated"] = True
        payload["total_chars"] = len(text)
    return payload
