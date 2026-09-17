"""A self-contained, accessible HTML style guide for a generated design system."""

# ruff: noqa: E501 - embedded CSS and HTML templates.

from __future__ import annotations

import html

from code_ai.tools.design import color as c
from code_ai.tools.design.tokens import DesignSystem

_CSS = """
*, *::before, *::after { box-sizing: border-box; }
body { margin: 0; font-family: var(--font-body); background: var(--background); color: var(--text);
  font-size: var(--font-size-base); line-height: var(--line-height-base); }
.page { max-width: 1180px; margin: 0 auto; padding: var(--space-8) var(--space-6) var(--space-16); }
header.top { display: flex; flex-wrap: wrap; gap: var(--space-4); align-items: center; justify-content: space-between;
  padding-bottom: var(--space-6); border-bottom: 1px solid var(--border); }
h1, h2, h3 { font-family: var(--font-heading); line-height: 1.2; margin: 0; }
h1 { font-size: var(--font-size-4xl); letter-spacing: var(--letter-spacing-4xl); }
h2 { font-size: var(--font-size-2xl); margin: var(--space-12) 0 var(--space-4); }
h3 { font-size: var(--font-size-lg); margin: var(--space-6) 0 var(--space-3); }
p.lead { color: var(--text-muted); margin: var(--space-2) 0 0; max-width: 65ch; }
.swatches { display: grid; grid-template-columns: repeat(auto-fill, minmax(92px, 1fr)); gap: var(--space-2); }
.swatch { border-radius: var(--radius-md); padding: var(--space-3) var(--space-2); min-height: 96px;
  display: flex; flex-direction: column; justify-content: space-between; font-size: var(--font-size-sm); }
.swatch strong { font-size: var(--font-size-base); }
.swatch code { font-family: var(--font-mono); font-size: 0.8rem; }
.base { outline: 3px solid var(--text); outline-offset: 2px; }
.themes { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: var(--space-6); }
.theme-panel { background: var(--background); color: var(--text); border: 1px solid var(--border);
  border-radius: var(--radius-xl); padding: var(--space-6); }
table { width: 100%; border-collapse: collapse; font-size: var(--font-size-sm); }
th, td { text-align: left; padding: var(--space-2); border-bottom: 1px solid var(--border); }
.dot { display: inline-block; width: 1.1rem; height: 1.1rem; border-radius: var(--radius-sm);
  border: 1px solid var(--border-strong); vertical-align: middle; margin-right: var(--space-2); }
.pass { color: var(--success); font-weight: 600; }
.fail { color: var(--danger); font-weight: 600; }
.type-row { display: grid; grid-template-columns: 110px 1fr; gap: var(--space-4); align-items: baseline;
  padding: var(--space-3) 0; border-bottom: 1px solid var(--border); }
.type-row code { color: var(--text-muted); font-family: var(--font-mono); font-size: 0.8rem; }
.bars div { display: flex; align-items: center; gap: var(--space-3); margin: var(--space-1) 0; font-size: var(--font-size-sm); }
.bar { height: 0.9rem; background: var(--primary); border-radius: 2px; }
.grid3 { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: var(--space-4); }
.tile { background: var(--surface-raised); border: 1px solid var(--border); padding: var(--space-4);
  min-height: 88px; font-size: var(--font-size-sm); }
.stack { display: flex; flex-wrap: wrap; gap: var(--space-3); align-items: center; }
.btn { font: inherit; font-weight: 600; font-size: var(--font-size-sm); min-height: 44px; padding: 0 var(--space-5);
  border-radius: var(--radius-md); border: 1px solid transparent; cursor: pointer;
  transition: background var(--duration-fast) var(--ease-standard); }
.btn:focus-visible, input:focus-visible, select:focus-visible, a:focus-visible, .switch input:focus-visible + span {
  outline: 3px solid var(--focus-ring); outline-offset: 2px; }
.btn-primary { background: var(--primary); color: var(--on-primary); }
.btn-primary:hover { background: var(--primary-hover); }
.btn-secondary { background: var(--primary-soft); color: var(--on-primary-soft); }
.btn-outline { background: transparent; color: var(--text); border-color: var(--border-strong); }
.btn-ghost { background: transparent; color: var(--link); }
.btn-danger { background: var(--danger); color: var(--on-danger); }
.btn[disabled] { opacity: 0.55; cursor: not-allowed; }
.field { display: grid; gap: var(--space-1); max-width: 360px; margin-bottom: var(--space-4); }
.field label { font-weight: 600; font-size: var(--font-size-sm); }
.field input, .field select { font: inherit; min-height: 44px; padding: 0 var(--space-3); border-radius: var(--radius-md);
  border: 1px solid var(--border-strong); background: var(--surface-raised); color: var(--text); }
.field .help { color: var(--text-muted); font-size: var(--font-size-sm); }
.field .error { color: var(--danger); font-size: var(--font-size-sm); }
.field input[aria-invalid="true"] { border-color: var(--danger); }
.card { background: var(--surface-raised); border: 1px solid var(--border); border-radius: var(--radius-lg);
  box-shadow: var(--shadow-md); padding: var(--space-6); max-width: 360px; }
.card p { color: var(--text-muted); }
.alert { border-radius: var(--radius-md); padding: var(--space-3) var(--space-4); border: 1px solid; margin: var(--space-2) 0; }
.alert-success { background: var(--success-soft); color: var(--on-success-soft); border-color: var(--success-border); }
.alert-warning { background: var(--warning-soft); color: var(--on-warning-soft); border-color: var(--warning-border); }
.alert-danger { background: var(--danger-soft); color: var(--on-danger-soft); border-color: var(--danger-border); }
.alert-info { background: var(--info-soft); color: var(--on-info-soft); border-color: var(--info-border); }
.badge { display: inline-flex; align-items: center; min-height: 24px; padding: 0 var(--space-2); border-radius: var(--radius-full);
  font-size: var(--font-size-xs); font-weight: 600; background: var(--primary-soft); color: var(--on-primary-soft); }
a { color: var(--link); text-decoration: underline; text-underline-offset: 2px; }
.stack a { display: inline-flex; align-items: center; min-height: 44px; }
.switch { display: inline-flex; gap: var(--space-2); align-items: center; min-height: 44px; font-size: var(--font-size-sm); }
.switch input { width: 24px; height: 24px; accent-color: var(--primary); }
"""


def _swatch(step: int, value: str, is_base: bool) -> str:
    rgb = c.parse(value)
    white, black = c.RGB(1, 1, 1), c.RGB(0, 0, 0)
    on_white = c.wcag_ratio(rgb, white)
    ink = "#ffffff" if on_white >= c.wcag_ratio(rgb, black) else "#000000"
    usable = "text" if on_white >= 4.5 else "large text" if on_white >= 3 else "fill only"
    classes = "swatch base" if is_base else "swatch"
    return (
        f'<div class="{classes}" style="background:{value};color:{ink}">'
        f"<strong>{step}</strong><code>{value}</code>"
        f"<span>{on_white:.1f}:1 on white · {usable}</span></div>"
    )


def _theme_panel(ds: DesignSystem, name: str) -> str:
    rows = []
    for row in ds.contrast[name]:
        fg, bg = row["pair"].split(" on ")
        fg_value, bg_value = ds.themes[name][fg], ds.themes[name][bg]
        status = (
            '<span class="pass">pass</span>' if row["pass"] else '<span class="fail">fail</span>'
        )
        if row["target"] >= 4.5:
            sample = (
                f'<span style="background:{bg_value};color:{fg_value};padding:2px 6px;'
                f'border-radius:4px">{html.escape(fg)}</span>'
            )
        else:
            # Borders, focus rings and large-text roles are shown as colour, not as body text.
            sample = f'<span class="dot" style="background:{fg_value}" aria-hidden="true"></span>{html.escape(fg)}'
        rows.append(
            f"<tr><td>{sample}</td><td>{html.escape(bg)}</td><td>{row['ratio']}:1</td>"
            f"<td>{row['apca']}</td><td>{status}</td></tr>"
        )
    return (
        f'<section class="theme-panel" data-theme="{name}" aria-labelledby="theme-{name}">'
        f'<h3 id="theme-{name}">{name.title()} theme</h3>'
        '<table><thead><tr><th scope="col">Foreground</th><th scope="col">Background</th>'
        '<th scope="col">WCAG</th><th scope="col">APCA Lc</th><th scope="col">Result</th></tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table>"
        '<div class="stack" style="margin-top:var(--space-4)">'
        '<button class="btn btn-primary" type="button">Primary</button>'
        '<button class="btn btn-secondary" type="button">Secondary</button>'
        '<a href="#components">Link text</a></div></section>'
    )


def styleguide(ds: DesignSystem, tokens_css: str) -> str:
    colors = []
    for group, scale in ds.colors.items():
        base = ds.base_steps.get(group)
        swatches = "".join(_swatch(step, value, step == base) for step, value in scale.items())
        colors.append(
            f'<h3>{html.escape(group.title())}</h3><div class="swatches">{swatches}</div>'
        )
    type_rows = "".join(
        f'<div class="type-row"><code>{key} · {spec["px"]}</code>'
        f'<span style="font-size:var(--font-size-{key});line-height:var(--line-height-{key});'
        f'letter-spacing:var(--letter-spacing-{key})">The quick brown fox jumps over the lazy dog</span></div>'
        for key, spec in reversed(list(ds.typography["sizes"].items()))
    )
    bars = "".join(
        f'<div><code style="width:4rem;display:inline-block">{key.replace("_", ".")}</code>'
        f'<span class="bar" style="width:var(--space-{key})"></span><span>{value}</span></div>'
        for key, value in ds.spacing.items()
        if key != "0"
    )
    radii = "".join(
        f'<div class="tile" style="border-radius:var(--radius-{key})"><strong>{key}</strong><br>{value}</div>'
        for key, value in ds.radii.items()
    )
    shadows = "".join(
        f'<div class="tile" style="box-shadow:var(--shadow-{key});border-radius:var(--radius-lg)"><strong>{key}</strong></div>'
        for key in ds.shadows
    )
    notes = "".join(f"<li>{html.escape(note)}</li>" for note in ds.notes)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(ds.name)} design system</title>
<style>{tokens_css}{_CSS}</style>
</head>
<body>
<div class="page">
<header class="top">
  <div>
    <h1>{html.escape(ds.name)} design system</h1>
    <p class="lead">Colour scales in OKLCH, light and dark roles checked against WCAG 2.2 and APCA,
    a modular type scale, spacing, radii, shadows and components built only from these tokens.</p>
  </div>
  <button class="btn btn-outline" type="button" id="theme-toggle" aria-pressed="false">Switch to dark theme</button>
</header>
<main>
  <h2 id="colors">Colour</h2>
  <p class="lead">Outlined swatches are the brand colours you supplied.</p>
  {"".join(colors)}
  {f"<ul>{notes}</ul>" if notes else ""}
  <h2 id="themes">Themes and contrast</h2>
  <div class="themes">{_theme_panel(ds, "light")}{_theme_panel(ds, "dark")}</div>
  <h2 id="type">Typography</h2>
  <p class="lead">Ratio {ds.typography["ratio"]} from a {ds.typography["base"]} base; sizes are fluid between 360px and 1280px viewports.</p>
  {type_rows}
  <h2 id="space">Spacing</h2>
  <div class="bars">{bars}</div>
  <h2 id="shape">Radii and elevation</h2>
  <div class="grid3">{radii}</div>
  <div class="grid3" style="margin-top:var(--space-6)">{shadows}</div>
  <h2 id="components">Components</h2>
  <h3>Buttons</h3>
  <div class="stack">
    <button class="btn btn-primary" type="button">Save changes</button>
    <button class="btn btn-secondary" type="button">Preview</button>
    <button class="btn btn-outline" type="button">Cancel</button>
    <button class="btn btn-ghost" type="button">Learn more</button>
    <button class="btn btn-danger" type="button">Delete</button>
    <button class="btn btn-primary" type="button" disabled>Disabled</button>
  </div>
  <h3>Form</h3>
  <form onsubmit="return false">
    <div class="field"><label for="email">Email</label>
      <input id="email" type="email" autocomplete="email" aria-describedby="email-help">
      <span class="help" id="email-help">We never share your address.</span></div>
    <div class="field"><label for="name">Full name</label>
      <input id="name" type="text" aria-invalid="true" aria-describedby="name-error">
      <span class="error" id="name-error">Enter your full name.</span></div>
    <div class="field"><label for="role">Role</label>
      <select id="role"><option>Engineer</option><option>Designer</option></select></div>
    <label class="switch"><input type="checkbox" checked> Email me product updates</label>
  </form>
  <h3>Card</h3>
  <article class="card"><h4 style="margin:0 0 var(--space-2);font-size:var(--font-size-lg)">Quarterly report</h4>
    <p>Revenue grew 18% with churn at a record low.</p>
    <span class="badge">New</span></article>
  <h3>Alerts</h3>
  <div class="alert alert-success" role="status">Changes saved.</div>
  <div class="alert alert-info" role="status">A new version is available.</div>
  <div class="alert alert-warning" role="status">Your trial ends in 3 days.</div>
  <div class="alert alert-danger" role="alert">Payment failed. Update your card.</div>
</main>
</div>
<script>
  const toggle = document.getElementById("theme-toggle");
  const systemDark = matchMedia("(prefers-color-scheme: dark)").matches;
  const label = (dark) => {{
    toggle.setAttribute("aria-pressed", String(dark));
    toggle.textContent = dark ? "Switch to light theme" : "Switch to dark theme";
  }};
  label(systemDark);
  toggle.addEventListener("click", () => {{
    const current = document.documentElement.getAttribute("data-theme");
    const dark = current ? current !== "dark" : !systemDark;
    document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
    toggle.setAttribute("aria-pressed", String(dark));
    toggle.textContent = dark ? "Switch to light theme" : "Switch to dark theme";
  }});
</script>
</body>
</html>
"""
