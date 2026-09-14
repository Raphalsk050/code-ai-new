"""Finding things on a page, and naming them in a way that survives being used.

The model picks elements by number from the last read. That number has to mean
the same node when the action runs a moment later, on a page that may have
scrolled, re-rendered or replaced the node entirely. Coordinates do not survive
any of that; a stamped attribute does, and it lets Playwright do the work it is
good at - scrolling into view, waiting for the thing to be clickable, refusing
when something covers it.

Collection crosses shadow roots here and iframes in session.py, because the
controls of a real editor - a slide deck, a rich text document - live in both.
"""

from __future__ import annotations

from typing import Any

# The attribute stamped on every element offered to the model. Read back as a
# selector, so it must be something no page would use for its own purposes.
STAMP = "data-codeai-el"

# Elements offered per read. Enough for a real application's toolbar without
# the list becoming the expensive part of the turn.
MAX_ELEMENTS = 120

# Longest label kept per element.
MAX_LABEL_CHARS = 120

# Options listed for a <select>. A country dropdown should not cost the turn.
MAX_OPTIONS = 30

# Nodes the shadow-host walk may look at. An application-sized page has tens
# of thousands, and past this the components not yet found are not worth the
# time the read is spending.
MAX_SCANNED_NODES = 30_000

# What counts as interactive. Wider than "things with an onclick": a document
# editor's canvas is a contenteditable div, a slide thumbnail is a role=option,
# and a toolbar button is as likely to be a div with a role as a <button>.
INTERACTIVE_SELECTOR = ", ".join(
    (
        "a[href]",
        "button",
        "input:not([type=hidden])",
        "select",
        "textarea",
        "summary",
        "label[for]",
        "[contenteditable='']",
        "[contenteditable=true]",
        "[onclick]",
        "[tabindex]:not([tabindex='-1'])",
        "[role=button]",
        "[role=link]",
        "[role=tab]",
        "[role=checkbox]",
        "[role=radio]",
        "[role=switch]",
        "[role=menuitem]",
        "[role=menuitemcheckbox]",
        "[role=menuitemradio]",
        "[role=option]",
        "[role=combobox]",
        "[role=listbox]",
        "[role=textbox]",
        "[role=searchbox]",
        "[role=slider]",
        "[role=spinbutton]",
        "[role=treeitem]",
    )
)

# Runs in the page. Stamps every interactive element with a number and reports
# what it is, what it says and what state it is in, so the model can choose
# from what is there rather than invent a selector for a DOM it cannot see.
#
# Stamps from the previous read are cleared first: a number that still pointed
# at a node from two pages ago would be the one thing worse than no number.
COLLECT_JS = """
(args) => {
  const [selector, limit, labelChars, maxOptions, nodeBudget] = args;
  const ATTR = 'data-codeai-el';
  const seen = new Set();
  const found = [];

  // Matching is left to querySelectorAll. Testing every node against the
  // selector by hand instead costs enough on an application-sized page - tens
  // of thousands of nodes - to push the read itself past its timeout.
  //
  // querySelectorAll stops at a shadow boundary, so each root is searched on
  // its own and every open root is queued. Finding the hosts does need a walk;
  // it is one pass reading one property, and it stops at a budget.
  let scanned = 0;
  const roots = [document];
  for (let i = 0; i < roots.length; i++) {
    const root = roots[i];
    try {
      // Cleared per root, not once over the document: a stamp left inside a
      // shadow tree would still answer to its number, and the selector would
      // find it before the element this read meant.
      for (const old of root.querySelectorAll('[' + ATTR + ']')) old.removeAttribute(ATTR);
      for (const el of root.querySelectorAll(selector)) {
        if (seen.has(el)) continue;
        seen.add(el);
        found.push(el);
      }
      if (scanned < nodeBudget) {
        for (const el of root.querySelectorAll('*')) {
          if (++scanned > nodeBudget) break;
          if (el.shadowRoot) roots.push(el.shadowRoot);
        }
      }
    } catch (err) {
      continue;
    }
  }

  const label = (el) => {
    const pick = (
      el.getAttribute('aria-label') ||
      el.getAttribute('placeholder') ||
      el.getAttribute('title') ||
      el.getAttribute('alt') ||
      (el.labels && el.labels[0] && el.labels[0].innerText) ||
      el.innerText ||
      el.value ||
      el.getAttribute('name') ||
      ''
    );
    return String(pick).trim().replace(/\\s+/g, ' ').slice(0, labelChars);
  };

  const out = [];
  for (const el of found) {
    const rect = el.getBoundingClientRect();
    // No box means display:none, collapsed, or detached. A user cannot reach
    // it, so it must not be offered.
    if (rect.width <= 0 || rect.height <= 0) continue;
    const style = window.getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none') continue;
    if (style.opacity === '0') continue;

    const ref = out.length;
    el.setAttribute(ATTR, String(ref));
    const tag = el.tagName.toLowerCase();
    const record = {
      ref: ref,
      tag: tag,
      type: (el.getAttribute('type') || '').toLowerCase(),
      role: el.getAttribute('role') || '',
      text: label(el),
      x: Math.round(rect.left + rect.width / 2),
      y: Math.round(rect.top + rect.height / 2),
      w: Math.round(rect.width),
      h: Math.round(rect.height),
    };

    if (el.disabled === true || el.getAttribute('aria-disabled') === 'true') {
      record.disabled = true;
    }
    const editable = el.isContentEditable;
    if (editable) record.editable = true;
    if (tag === 'input' || tag === 'textarea') {
      record.value = String(el.value == null ? '' : el.value).slice(0, labelChars);
    }
    if (el.checked === true) record.checked = true;
    else if (el.checked === false && (el.type === 'checkbox' || el.type === 'radio')) {
      record.checked = false;
    }
    const ariaChecked = el.getAttribute('aria-checked');
    if (ariaChecked === 'true' || ariaChecked === 'false') record.checked = ariaChecked === 'true';
    const expanded = el.getAttribute('aria-expanded');
    if (expanded === 'true' || expanded === 'false') record.expanded = expanded === 'true';
    if (tag === 'select') {
      record.options = Array.from(el.options).slice(0, maxOptions).map((opt) => ({
        value: opt.value,
        text: String(opt.text || '').trim().slice(0, labelChars),
        selected: opt.selected === true,
      }));
      record.multiple = el.multiple === true;
    }
    if (tag === 'input' && el.type === 'file') record.accepts_files = true;
    if (document.activeElement === el) record.focused = true;
    // Off-screen is not hidden: it is a thing the model must scroll to, and
    // Playwright will, but saying so stops it guessing from coordinates.
    record.in_viewport = (
      rect.bottom > 0 &&
      rect.right > 0 &&
      rect.top < (window.innerHeight || 0) &&
      rect.left < (window.innerWidth || 0)
    );

    out.push(record);
    if (out.length >= limit) break;
  }
  return out;
}
"""

# Where the page is scrolled and how big it is. Enough for the model to know
# there is more below without reading the whole document again.
VIEWPORT_JS = """
() => ({
  scroll_x: Math.round(window.scrollX),
  scroll_y: Math.round(window.scrollY),
  width: window.innerWidth,
  height: window.innerHeight,
  page_height: Math.round(document.documentElement.scrollHeight),
  at_bottom: (window.innerHeight + window.scrollY) >= (document.documentElement.scrollHeight - 2),
})
"""


def stamp_selector(ref: int) -> str:
    """The selector that finds the element a read stamped with this number."""

    return f"[{STAMP}='{int(ref)}']"


def describe(element: dict[str, Any]) -> str:
    """One line naming an element, for an error the model has to act on."""

    what = element.get("role") or element.get("tag") or "element"
    text = str(element.get("text") or "").strip()
    return f"{what} {text!r}" if text else str(what)
