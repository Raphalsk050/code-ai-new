"""The in-page half of ui_audit. Kept as a Python string so the frozen binary ships it for free."""

# ruff: noqa: E501 - JavaScript source, wrapped for the browser, not for Python.

AUDIT_JS = r"""
async (options) => {
  const findings = [];
  // The requested viewport, not innerWidth: a page without a viewport meta tag lays out at 980px on phones.
  const viewportWidth = options.viewport_width || window.innerWidth;
  const mobile = viewportWidth < 600;
  const push = (rule, severity, category, message, el, extra = {}) => {
    const rect = el && el.getBoundingClientRect ? el.getBoundingClientRect() : null;
    findings.push({
      rule, severity, category, message,
      selector: el && el.nodeType === 1 ? selectorOf(el) : null,
      snippet: el && el.outerHTML ? el.outerHTML.slice(0, 160) : null,
      rect: rect ? [Math.round(rect.left + scrollX), Math.round(rect.top + scrollY),
                    Math.round(rect.width), Math.round(rect.height)] : null,
      ...extra,
    });
  };

  function selectorOf(el) {
    if (el.id && document.querySelectorAll('#' + CSS.escape(el.id)).length === 1) return '#' + CSS.escape(el.id);
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && node !== document.body) {
      let part = node.tagName.toLowerCase();
      if (node.id && document.querySelectorAll('#' + CSS.escape(node.id)).length === 1) {
        parts.unshift('#' + CSS.escape(node.id));
        break;
      }
      const cls = [...node.classList].filter(c => !/^\d/.test(c)).slice(0, 2);
      if (cls.length) part += '.' + cls.map(c => CSS.escape(c)).join('.');
      const parent = node.parentElement;
      if (parent) {
        const same = [...parent.children].filter(c => c.tagName === node.tagName);
        if (same.length > 1) part += `:nth-of-type(${same.indexOf(node) + 1})`;
      }
      parts.unshift(part);
      const candidate = parts.join(' > ');
      try { if (document.querySelectorAll(candidate).length === 1) return candidate; } catch (e) {}
      node = node.parentElement;
    }
    return parts.join(' > ');
  }

  const visible = (el) => {
    const style = getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none' || parseFloat(style.opacity) === 0) return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };

  const parseColor = (value) => {
    const m = value.match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const p = m[1].split(/[ ,/]+/).filter(Boolean).map(Number);
    return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
  };
  const blend = (top, bottom) => ({
    r: top.r * top.a + bottom.r * (1 - top.a),
    g: top.g * top.a + bottom.g * (1 - top.a),
    b: top.b * top.a + bottom.b * (1 - top.a), a: 1,
  });
  const luminance = (c) => {
    const ch = [c.r, c.g, c.b].map(v => { v /= 255; return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); });
    return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2];
  };
  const ratio = (a, b) => {
    const [l1, l2] = [luminance(a), luminance(b)].sort((x, y) => y - x);
    return (l1 + 0.05) / (l2 + 0.05);
  };
  const backgroundOf = (el) => {
    const layers = [];
    let node = el;
    while (node && node.nodeType === 1) {
      const style = getComputedStyle(node);
      if (style.backgroundImage && style.backgroundImage !== 'none') return { unknown: true };
      const color = parseColor(style.backgroundColor);
      if (color && color.a > 0) {
        layers.push(color);
        if (color.a >= 1) break;
      }
      node = node.parentElement;
    }
    let result = { r: 255, g: 255, b: 255, a: 1 };
    for (const layer of layers.reverse()) result = blend(layer, result);
    return result;
  };
  const accessibleName = (el) => {
    const label = el.getAttribute('aria-label');
    if (label && label.trim()) return label.trim();
    const by = el.getAttribute('aria-labelledby');
    if (by) {
      const text = by.split(/\s+/).map(id => document.getElementById(id)?.textContent || '').join(' ').trim();
      if (text) return text;
    }
    if (el.labels && el.labels.length) {
      const text = [...el.labels].map(l => l.textContent).join(' ').trim();
      if (text) return text;
    }
    const text = (el.innerText || el.textContent || '').trim();
    if (text) return text;
    const img = el.querySelector && el.querySelector('img[alt]:not([alt=""]), svg title');
    if (img) return img.getAttribute('alt') || img.textContent;
    return (el.getAttribute('title') || el.getAttribute('value') || '').trim();
  };

  const all = [...document.querySelectorAll('body *')];
  const enabled = (name) => !options.checks || options.checks.includes(name);

  // Page-level structure
  if (enabled('structure')) {
    if (!document.documentElement.getAttribute('lang')) push('html-lang', 'serious', 'accessibility', 'The <html> element has no lang attribute, so screen readers guess the language.', document.documentElement, { wcag: '3.1.1' });
    if (!document.querySelector('meta[name="viewport"]')) push('meta-viewport', 'serious', 'responsive', 'No viewport meta tag: mobile browsers render the desktop layout zoomed out.', document.head);
    if (!document.title.trim()) push('page-title', 'moderate', 'accessibility', 'The page has no <title>.', document.head, { wcag: '2.4.2' });
    if (!document.querySelector('main, [role="main"]')) push('landmark-main', 'moderate', 'accessibility', 'No <main> landmark for skipping straight to the content.', document.body, { wcag: '1.3.1' });
    const h1s = [...document.querySelectorAll('h1')].filter(visible);
    if (h1s.length === 0) push('heading-h1', 'moderate', 'accessibility', 'No visible <h1> heading.', document.body);
    if (h1s.length > 1) push('heading-h1', 'minor', 'accessibility', `${h1s.length} <h1> headings; one per page reads clearest.`, h1s[1]);
    let last = 0;
    for (const h of [...document.querySelectorAll('h1,h2,h3,h4,h5,h6')].filter(visible)) {
      const level = Number(h.tagName[1]);
      if (last && level > last + 1) push('heading-order', 'moderate', 'accessibility', `Heading jumps from h${last} to h${level}.`, h, { wcag: '1.3.1' });
      last = level;
    }
    const ids = {};
    for (const el of document.querySelectorAll('[id]')) ids[el.id] = (ids[el.id] || 0) + 1;
    for (const [id, count] of Object.entries(ids)) if (count > 1) push('duplicate-id', 'moderate', 'accessibility', `id "${id}" is used ${count} times; labels and ARIA references break.`, document.getElementById(id), { wcag: '4.1.1' });
  }

  // Text contrast and size
  if (enabled('contrast') || enabled('typography')) {
    let checked = 0;
    for (const el of all) {
      if (checked > 3000) break;
      const hasText = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim().length > 1);
      if (!hasText || !visible(el)) continue;
      checked++;
      const style = getComputedStyle(el);
      const size = parseFloat(style.fontSize);
      const weight = Number(style.fontWeight) || 400;
      if (enabled('contrast')) {
        const fg = parseColor(style.color);
        const bg = backgroundOf(el);
        if (fg && !bg.unknown) {
          const effective = fg.a < 1 ? blend(fg, bg) : fg;
          const value = ratio(effective, bg);
          const large = size >= 24 || (size >= 18.66 && weight >= 700);
          const needed = large ? 3 : 4.5;
          if (value < needed && !el.closest('[disabled], [aria-disabled="true"]')) {
            push('color-contrast', value < needed - 1.5 ? 'serious' : 'moderate', 'accessibility',
              `Text contrast ${value.toFixed(2)}:1, needs ${needed}:1${large ? ' (large text)' : ''}.`, el,
              { wcag: '1.4.3', measured: Number(value.toFixed(2)), text: el.textContent.trim().slice(0, 60) });
          }
        } else if (bg.unknown && options.report_manual) {
          push('contrast-manual', 'info', 'accessibility', 'Text over an image or gradient: check contrast by eye.', el);
        }
      }
      if (enabled('typography')) {
        if (size < 12 && el.textContent.trim().length > 3) push('small-text', 'moderate', 'readability', `Text at ${size}px is hard to read; 12px is a practical minimum.`, el, { measured: size });
        if (['P', 'LI', 'DD', 'BLOCKQUOTE'].includes(el.tagName)) {
          const chars = el.getBoundingClientRect().width / (size * 0.5);
          if (chars > 95 && el.textContent.length > 200) push('line-length', 'minor', 'readability', `Lines run about ${Math.round(chars)} characters; 45 to 90 reads comfortably.`, el, { measured: Math.round(chars) });
        }
      }
    }
  }

  // Images and names
  if (enabled('names')) {
    for (const img of document.querySelectorAll('img')) {
      if (!img.hasAttribute('alt') && visible(img)) push('image-alt', 'serious', 'accessibility', 'Image without alt attribute (use alt="" if decorative).', img, { wcag: '1.1.1' });
    }
    for (const svg of document.querySelectorAll('svg[role="img"]')) {
      if (!accessibleName(svg) && !svg.querySelector('title')) push('svg-name', 'moderate', 'accessibility', 'svg role="img" without a title or aria-label.', svg, { wcag: '1.1.1' });
    }
    for (const control of document.querySelectorAll('input:not([type="hidden"]):not([type="submit"]):not([type="button"]):not([type="reset"]), select, textarea')) {
      if (!visible(control)) continue;
      const hasLabel = (control.labels && control.labels.length) || control.getAttribute('aria-label') || control.getAttribute('aria-labelledby') || control.getAttribute('title');
      if (!hasLabel) {
        const placeholder = control.getAttribute('placeholder');
        push('form-label', 'serious', 'accessibility', placeholder ? 'Field labelled only by its placeholder, which disappears while typing.' : 'Form field without a label.', control, { wcag: '3.3.2' });
      }
    }
    for (const el of document.querySelectorAll('button, a[href], [role="button"], [role="link"], input[type="submit"], input[type="button"]')) {
      if (visible(el) && !accessibleName(el)) push('control-name', 'serious', 'accessibility', 'Button or link with no accessible name (icon-only needs aria-label).', el, { wcag: '4.1.2' });
    }
  }

  // Interaction
  const interactive = [...document.querySelectorAll('a[href], button, input:not([type="hidden"]), select, textarea, [role="button"], [role="link"], [role="checkbox"], [role="tab"], [tabindex]:not([tabindex="-1"])')].filter(visible);
  if (enabled('targets')) {
    for (const el of interactive) {
      const rect = el.getBoundingClientRect();
      // A wrapping or linked label is the real target of a checkbox or radio.
      const labelRects = el.labels ? [...el.labels].map(l => l.getBoundingClientRect()) : [];
      if (labelRects.some(r => r.width >= 44 && r.height >= 44) || (labelRects.some(r => r.width >= 24 && r.height >= 24) && !mobile)) continue;
      const inlineLink = el.tagName === 'A' && getComputedStyle(el).display === 'inline' && el.parentElement && el.parentElement.textContent.trim().length > el.textContent.trim().length + 20;
      if (inlineLink) continue;
      if (rect.width < 24 || rect.height < 24) {
        push('target-size', 'serious', 'usability', `Target ${Math.round(rect.width)}x${Math.round(rect.height)}px is below the 24x24px minimum.`, el, { wcag: '2.5.8' });
      } else if (mobile && (rect.width < 44 || rect.height < 44)) {
        push('target-size-mobile', 'minor', 'usability', `Target ${Math.round(rect.width)}x${Math.round(rect.height)}px; 44x44px is comfortable for touch.`, el);
      }
      const cx = rect.left + rect.width / 2, cy = rect.top + rect.height / 2;
      if (cy >= 0 && cy < innerHeight && cx >= 0 && cx < innerWidth) {
        const hit = document.elementFromPoint(cx, cy);
        if (hit && hit !== el && !el.contains(hit) && !hit.contains(el) && !(el.labels && [...el.labels].some(l => l.contains(hit)))) {
          push('target-obscured', 'serious', 'usability', 'Something else covers the centre of this control, so clicks land on it instead.', el, { covered_by: selectorOf(hit) });
        }
      }
    }
    if (mobile) {
      for (const input of document.querySelectorAll('input:not([type="checkbox"]):not([type="radio"]):not([type="range"]):not([type="color"]):not([type="file"]), select, textarea')) {
        if (visible(input) && parseFloat(getComputedStyle(input).fontSize) < 16) push('input-zoom', 'minor', 'responsive', 'Inputs under 16px make iOS zoom the page on focus.', input);
      }
    }
    for (const el of document.querySelectorAll('[tabindex]')) {
      if (Number(el.getAttribute('tabindex')) > 0) push('tabindex-positive', 'moderate', 'accessibility', 'Positive tabindex reorders keyboard navigation unpredictably.', el, { wcag: '2.4.3' });
    }
  }

  if (enabled('aria')) {
    const roles = new Set('alert alertdialog application article banner button cell checkbox columnheader combobox complementary contentinfo definition dialog directory document feed figure form grid gridcell group heading img link list listbox listitem log main marquee math menu menubar menuitem menuitemcheckbox menuitemradio meter navigation none note option presentation progressbar radio radiogroup region row rowgroup rowheader scrollbar search searchbox separator slider spinbutton status switch tab table tablist tabpanel term textbox timer toolbar tooltip tree treegrid treeitem generic'.split(' '));
    for (const el of document.querySelectorAll('[role]')) {
      for (const role of el.getAttribute('role').split(/\s+/)) if (role && !roles.has(role)) push('aria-role', 'moderate', 'accessibility', `Unknown role "${role}".`, el, { wcag: '4.1.2' });
    }
    for (const el of document.querySelectorAll('[aria-hidden="true"]')) {
      const focusable = el.matches('a[href], button, input, select, textarea, [tabindex]:not([tabindex="-1"])') ? el : el.querySelector('a[href], button, input, select, textarea, [tabindex]:not([tabindex="-1"])');
      if (focusable) push('aria-hidden-focus', 'serious', 'accessibility', 'Focusable element hidden from screen readers with aria-hidden.', focusable, { wcag: '4.1.2' });
    }
    for (const media of document.querySelectorAll('video[autoplay], audio[autoplay]')) {
      if (!media.muted) push('autoplay-audio', 'serious', 'accessibility', 'Media plays sound automatically.', media, { wcag: '1.4.2' });
    }
    for (const link of document.querySelectorAll('p a[href], li a[href]')) {
      if (!visible(link)) continue;
      const style = getComputedStyle(link);
      const parent = getComputedStyle(link.parentElement);
      const underlined = style.textDecorationLine.includes('underline') || style.borderBottomStyle !== 'none';
      const fg = parseColor(style.color), around = parseColor(parent.color);
      if (!underlined && fg && around && ratio(fg, around) < 3) push('link-distinguishable', 'moderate', 'accessibility', 'Link inside text is neither underlined nor 3:1 different from the surrounding text.', link, { wcag: '1.4.1' });
    }
  }

  // Layout
  if (enabled('layout')) {
    const doc = document.documentElement;
    const limit = Math.min(innerWidth, viewportWidth);
    if (doc.scrollWidth > limit + 1) {
      const offenders = all.filter(el => visible(el) && el.getBoundingClientRect().right > limit + 1)
        .filter(el => ![...el.children].some(child => child.getBoundingClientRect().right > limit + 1))
        .slice(0, 5);
      push('horizontal-overflow', 'serious', 'responsive', `The page is ${doc.scrollWidth}px wide in a ${viewportWidth}px viewport and scrolls sideways.`, offenders[0] || document.body, { offenders: offenders.map(selectorOf) });
    }
    let clipped = 0;
    for (const el of all) {
      if (clipped >= 25) break;
      const style = getComputedStyle(el);
      if (!['hidden', 'clip'].includes(style.overflowX) && !['hidden', 'clip'].includes(style.overflow)) continue;
      if (style.textOverflow === 'ellipsis' || !visible(el) || !el.textContent.trim()) continue;
      if (el.scrollWidth > el.clientWidth + 2 || el.scrollHeight > el.clientHeight + 2) {
        const text = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
        if (text) { push('clipped-text', 'moderate', 'usability', 'Text is cut off by overflow: hidden.', el); clipped++; }
      }
    }
  }

  // Performance
  if (enabled('performance')) {
    let cls = 0;
    try {
      cls = await new Promise((resolve) => {
        let total = 0;
        const observer = new PerformanceObserver((list) => {
          for (const entry of list.getEntries()) if (!entry.hadRecentInput) total += entry.value;
        });
        observer.observe({ type: 'layout-shift', buffered: true });
        setTimeout(() => { observer.disconnect(); resolve(total); }, 200);
      });
    } catch (e) { cls = 0; }
    if (cls > 0.1) push('layout-shift', cls > 0.25 ? 'serious' : 'moderate', 'performance', `Cumulative layout shift ${cls.toFixed(3)}; content jumps while loading (good is under 0.1).`, document.body, { measured: Number(cls.toFixed(3)) });
    for (const img of document.querySelectorAll('img')) {
      const rect = img.getBoundingClientRect();
      if (img.naturalWidth > 600 && rect.width > 0 && img.naturalWidth > rect.width * devicePixelRatio * 2.5) {
        push('image-oversized', 'minor', 'performance', `Image is ${img.naturalWidth}px wide but shown at ${Math.round(rect.width)}px.`, img);
      }
      if (!img.getAttribute('width') && !img.getAttribute('height') && !getComputedStyle(img).aspectRatio.includes('/')) {
        push('image-dimensions', 'minor', 'performance', 'Image without width/height attributes causes layout shift while loading.', img);
      }
    }
    const bytes = performance.getEntriesByType('resource').reduce((sum, r) => sum + (r.transferSize || 0), 0);
    if (bytes > 3 * 1024 * 1024) push('page-weight', 'moderate', 'performance', `Resources weigh ${(bytes / 1048576).toFixed(1)} MB.`, document.body, { measured: bytes });
  }

  // Focus styles: snapshot now, compare after each Tab from Python.
  const focusables = interactive.slice(0, options.focus_limit || 25);
  focusables.forEach((el, i) => {
    el.setAttribute('data-codeai-focus', String(i));
    const s = getComputedStyle(el);
    el.__codeaiBefore = [s.outlineStyle + s.outlineWidth + s.outlineColor, s.boxShadow, s.borderColor, s.backgroundColor, s.color, s.textDecorationLine].join('|');
  });
  window.__codeaiFocusCount = focusables.length;
  return { findings, focusables: focusables.length, url: location.href, title: document.title,
           width: innerWidth, height: innerHeight, scroll_height: document.documentElement.scrollHeight };
}
"""

FOCUS_PROBE_JS = r"""
() => {
  const el = document.activeElement;
  if (!el || !el.hasAttribute('data-codeai-focus')) return null;
  const s = getComputedStyle(el);
  const now = [s.outlineStyle + s.outlineWidth + s.outlineColor, s.boxShadow, s.borderColor, s.backgroundColor, s.color, s.textDecorationLine].join('|');
  const visibleOutline = s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) > 0;
  const r = el.getBoundingClientRect();
  return { index: Number(el.getAttribute('data-codeai-focus')), changed: now !== el.__codeaiBefore || visibleOutline,
           selector: el.id ? '#' + el.id : el.tagName.toLowerCase() + (el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\s+/).slice(0, 2).join('.') : ''),
           snippet: el.outerHTML.slice(0, 160),
           rect: [Math.round(r.left + scrollX), Math.round(r.top + scrollY), Math.round(r.width), Math.round(r.height)] };
}
"""

ANNOTATE_JS = r"""
(boxes) => {
  const layer = document.createElement('div');
  layer.id = '__codeai_annotations';
  layer.style.cssText = 'position:absolute;left:0;top:0;width:0;height:0;z-index:2147483647;pointer-events:none';
  boxes.forEach(([n, x, y, w, h, color]) => {
    const box = document.createElement('div');
    box.style.cssText = `position:absolute;left:${x - 2}px;top:${y - 2}px;width:${w + 4}px;height:${h + 4}px;border:3px solid ${color};border-radius:4px;box-sizing:border-box`;
    const tag = document.createElement('span');
    tag.textContent = n;
    tag.style.cssText = `position:absolute;left:-3px;top:-22px;background:${color};color:#fff;font:700 12px/18px Arial,sans-serif;padding:0 6px;border-radius:9px`;
    box.appendChild(tag);
    layer.appendChild(box);
  });
  document.body.appendChild(layer);
}
"""
