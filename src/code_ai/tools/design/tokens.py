"""A design system from a few inputs: tonal scales, themed roles that pass contrast, type, space."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from code_ai.core.errors import ToolArgumentError
from code_ai.tools.design import color as c

STEPS = (50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950)
_LIGHTNESS = dict(
    zip(STEPS, (0.975, 0.945, 0.89, 0.82, 0.72, 0.62, 0.54, 0.46, 0.38, 0.30, 0.22), strict=True)
)
_CHROMA = dict(
    zip(STEPS, (0.12, 0.25, 0.45, 0.7, 0.9, 1.0, 1.0, 0.92, 0.8, 0.65, 0.5), strict=True)
)

SEMANTIC_HUES = {"success": 150.0, "warning": 75.0, "danger": 27.0, "info": 250.0}

TYPE_RATIOS = {
    "minor-second": 1.067,
    "major-second": 1.125,
    "minor-third": 1.2,
    "major-third": 1.25,
    "perfect-fourth": 1.333,
    "augmented-fourth": 1.414,
    "perfect-fifth": 1.5,
    "golden": 1.618,
}
TYPE_STEPS = (
    ("xs", -2),
    ("sm", -1),
    ("base", 0),
    ("lg", 1),
    ("xl", 2),
    ("2xl", 3),
    ("3xl", 4),
    ("4xl", 5),
    ("5xl", 6),
)

HARMONIES = {
    "complementary": (180,),
    "analogous": (-30, 30),
    "triadic": (120, 240),
    "split-complementary": (150, 210),
    "tetradic": (90, 180, 270),
    "monochrome": (),
}

FONT_STACKS = {
    "sans": 'Inter, "Segoe UI", Roboto, "Helvetica Neue", Arial, system-ui, sans-serif',
    "serif": 'Georgia, "Times New Roman", Cambria, serif',
    "mono": '"JetBrains Mono", Consolas, "SFMono-Regular", Menlo, monospace',
}


@dataclass
class DesignSystem:
    name: str
    colors: dict[str, dict[int, str]]
    base_steps: dict[str, int]
    themes: dict[str, dict[str, str]]
    contrast: dict[str, list[dict[str, Any]]]
    typography: dict[str, Any]
    spacing: dict[str, str]
    radii: dict[str, str]
    shadows: dict[str, str]
    breakpoints: dict[str, str]
    z_index: dict[str, int]
    motion: dict[str, str]
    fonts: dict[str, str]
    harmony: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def scale(base: c.RGB, *, chroma_factor: float = 1.0) -> tuple[dict[int, str], int]:
    """Eleven perceptually even steps; the base colour itself lands on its nearest step."""

    lightness, chroma, hue = c.rgb_to_oklch(base)
    chroma *= chroma_factor
    nearest = min(STEPS, key=lambda step: abs(_LIGHTNESS[step] - lightness))
    colors = {}
    for step in STEPS:
        if step == nearest and chroma_factor == 1.0:
            colors[step] = base.hex
        else:
            colors[step] = c.oklch_to_rgb(_LIGHTNESS[step], chroma * _CHROMA[step], hue).hex
    return colors, nearest


def neutral(brand: c.RGB | None, tint: float) -> dict[int, str]:
    hue = c.rgb_to_oklch(brand)[2] if brand is not None else 0.0
    return {
        step: c.oklch_to_rgb(
            _LIGHTNESS[step] + (0.01 if step == 50 else 0), tint * (0.6 + _CHROMA[step] * 0.4), hue
        ).hex
        for step in STEPS
    }


def _pick(
    scale_: dict[int, str], background: str, target: float, order: list[int]
) -> tuple[str, int]:
    """First step in ``order`` that reaches ``target`` against ``background``."""

    bg = c.parse(background)
    for step in order:
        if c.wcag_ratio(c.parse(scale_[step]), bg) >= target:
            return scale_[step], step
    best = max(order, key=lambda step: c.wcag_ratio(c.parse(scale_[step]), bg))
    return scale_[best], best


def _on(background: str) -> str:
    bg = c.parse(background)
    return (
        "#ffffff"
        if c.wcag_ratio(c.RGB(1, 1, 1), bg) >= c.wcag_ratio(c.RGB(0, 0, 0), bg)
        else "#0b0b0f"
    )


def build(
    brand_colors: list[str],
    *,
    name: str = "brand",
    harmony: str | None = None,
    neutral_tint: float = 0.012,
    type_base: float = 16,
    type_ratio: str | float = "major-third",
    heading_font: str | None = None,
    body_font: str | None = None,
    spacing_base: int = 4,
    radius: str = "default",
) -> DesignSystem:
    if not brand_colors:
        raise ToolArgumentError("Give at least one brand colour.")
    parsed = [c.parse(value) for value in brand_colors[:3]]
    notes: list[str] = []
    colors: dict[str, dict[int, str]] = {}
    base_steps: dict[str, int] = {}
    labels = ["primary", "secondary", "accent"]
    for label, value in zip(labels, parsed, strict=False):
        colors[label], base_steps[label] = scale(value)

    harmony_colors: dict[str, str] = {}
    if harmony:
        key = harmony.strip().lower()
        if key not in HARMONIES:
            raise ToolArgumentError(f"Unknown harmony {harmony!r}. Use {', '.join(HARMONIES)}.")
        lightness, chroma, hue = c.rgb_to_oklch(parsed[0])
        for index, offset in enumerate(HARMONIES[key]):
            harmony_colors[f"{key}-{index + 1}"] = c.oklch_to_rgb(
                lightness, chroma, (hue + offset) % 360
            ).hex
        if key == "monochrome":
            harmony_colors.update(
                {
                    f"monochrome-{i + 1}": c.oklch_to_rgb(l_, chroma, hue).hex
                    for i, l_ in enumerate((0.35, 0.55, 0.75, 0.9))
                }
            )
        for index, value in enumerate(list(harmony_colors.values())):
            slot = labels[len(parsed) + index] if len(parsed) + index < len(labels) else None
            if slot:
                colors[slot], base_steps[slot] = scale(c.parse(value))
                notes.append(f"{slot} derived from the {key} harmony: {value}")

    colors["neutral"] = neutral(parsed[0], neutral_tint)
    brand_chroma = max(0.09, min(0.16, c.rgb_to_oklch(parsed[0])[1]))
    for label, hue in SEMANTIC_HUES.items():
        lightness = 0.72 if label == "warning" else 0.58
        colors[label], _ = scale(c.oklch_to_rgb(lightness, brand_chroma, hue))

    themes, contrast = _themes(colors, base_steps)
    return DesignSystem(
        name=name,
        colors=colors,
        base_steps=base_steps,
        themes=themes,
        contrast=contrast,
        typography=typography(type_base, type_ratio),
        spacing=spacing(spacing_base),
        radii=radii(radius),
        shadows=shadows(colors["neutral"][950]),
        breakpoints={"sm": "640px", "md": "768px", "lg": "1024px", "xl": "1280px", "2xl": "1536px"},
        z_index={
            "base": 0,
            "dropdown": 1000,
            "sticky": 1100,
            "overlay": 1300,
            "modal": 1400,
            "popover": 1500,
            "toast": 1600,
            "tooltip": 1700,
        },
        motion={
            "duration-fast": "120ms",
            "duration-base": "200ms",
            "duration-slow": "320ms",
            "ease-standard": "cubic-bezier(0.2, 0, 0, 1)",
            "ease-emphasized": "cubic-bezier(0.3, 0, 0, 1)",
            "ease-decelerate": "cubic-bezier(0, 0, 0, 1)",
            "ease-accelerate": "cubic-bezier(0.3, 0, 1, 1)",
        },
        fonts={
            "heading": f'"{heading_font}", {FONT_STACKS["sans"]}'
            if heading_font
            else FONT_STACKS["sans"],
            "body": f'"{body_font}", {FONT_STACKS["sans"]}' if body_font else FONT_STACKS["sans"],
            "mono": FONT_STACKS["mono"],
        },
        harmony=harmony_colors,
        notes=notes,
    )


def _themes(
    colors: dict[str, dict[int, str]], base_steps: dict[str, int]
) -> tuple[dict[str, dict[str, str]], dict[str, list]]:
    n, p = colors["neutral"], colors["primary"]
    light = {
        "background": "#ffffff",
        "surface": n[50],
        "surface-raised": "#ffffff",
        "surface-sunken": n[100],
        "text": n[900],
        "text-muted": n[600],
        "text-subtle": n[500],
        "border": n[200],
        "border-strong": n[300],
        "focus-ring": p[500],
    }
    dark = {
        "background": n[950],
        "surface": n[900],
        "surface-raised": n[800],
        "surface-sunken": "#050507",
        "text": n[50],
        "text-muted": n[300],
        "text-subtle": n[400],
        "border": n[800],
        "border-strong": n[700],
        "focus-ring": p[400],
    }
    # Muted text must still pass 4.5:1 on every surface it sits on.
    light["text-muted"], _ = _pick(n, n[100], 4.5, [600, 700, 800])
    dark["text-muted"], _ = _pick(n, n[800], 4.5, [300, 200, 100])
    light["text-subtle"], _ = _pick(n, "#ffffff", 3.0, [500, 600, 700])
    dark["text-subtle"], _ = _pick(n, n[900], 3.0, [400, 300, 200])

    for label in [k for k in ("primary", "secondary", "accent") if k in colors]:
        scale_ = colors[label]
        # The brand colour itself when its label reads at 4.5:1, else the nearest step that does.
        base = base_steps.get(label, 600)
        order = [base] + [s for s in (600, 700, 500, 800, 900) if s != base]
        fill, step = next(
            (
                (scale_[s], s)
                for s in order
                if c.wcag_ratio(c.parse(_on(scale_[s])), c.parse(scale_[s])) >= 4.5
            ),
            (scale_[700], 700),
        )
        light[label] = fill
        light[f"on-{label}"] = _on(fill)
        light[f"{label}-hover"] = scale_[STEPS[min(len(STEPS) - 1, STEPS.index(step) + 1)]]
        light[f"{label}-soft"] = scale_[100]
        light[f"on-{label}-soft"], _ = _pick(scale_, scale_[100], 4.5, [800, 900, 950])
        dark_fill, dark_step = _pick(scale_, n[950], 4.5, [400, 300, 500, 200])
        dark[label] = dark_fill
        dark[f"on-{label}"] = _on(dark_fill)
        dark[f"{label}-hover"] = scale_[STEPS[max(0, STEPS.index(dark_step) - 1)]]
        dark[f"{label}-soft"] = scale_[900]
        dark[f"on-{label}-soft"], _ = _pick(scale_, scale_[900], 4.5, [200, 100, 50])
    light["link"], _ = _pick(p, "#ffffff", 4.5, [600, 700, 800])
    dark["link"], _ = _pick(p, n[950], 4.5, [300, 400, 200])

    for label in SEMANTIC_HUES:
        scale_ = colors[label]
        light[label], _ = _pick(scale_, "#ffffff", 4.5, [600, 700, 800])
        light[f"on-{label}"] = _on(light[label])
        light[f"{label}-soft"] = scale_[50]
        light[f"on-{label}-soft"], _ = _pick(scale_, scale_[50], 4.5, [800, 900])
        light[f"{label}-border"] = scale_[200]
        dark[label], _ = _pick(scale_, n[950], 4.5, [400, 300])
        dark[f"on-{label}"] = _on(dark[label])
        dark[f"{label}-soft"] = scale_[950]
        dark[f"on-{label}-soft"], _ = _pick(scale_, scale_[950], 4.5, [200, 100])
        dark[f"{label}-border"] = scale_[800]

    pairs = [
        ("text", "background", 4.5),
        ("text", "surface", 4.5),
        ("text-muted", "background", 4.5),
        ("text-muted", "surface-sunken", 4.5),
        ("text-subtle", "background", 3.0),
        ("link", "background", 4.5),
        ("on-primary", "primary", 4.5),
        ("on-primary-soft", "primary-soft", 4.5),
        ("focus-ring", "background", 3.0),
        ("border-strong", "background", 1.0),
    ]
    for label in ("secondary", "accent"):
        if label in light:
            pairs += [(f"on-{label}", label, 4.5), (f"on-{label}-soft", f"{label}-soft", 4.5)]
    for label in SEMANTIC_HUES:
        pairs += [
            (label, "background", 4.5),
            (f"on-{label}", label, 4.5),
            (f"on-{label}-soft", f"{label}-soft", 4.5),
        ]

    report: dict[str, list] = {}
    for theme_name, roles in (("light", light), ("dark", dark)):
        rows = []
        for fg, bg, target in pairs:
            if fg not in roles or bg not in roles:
                continue
            ratio = c.wcag_ratio(c.parse(roles[fg]), c.parse(roles[bg]))
            if ratio < target and target > 1.0:
                fixed = c.fix_contrast(c.parse(roles[fg]), c.parse(roles[bg]), target).hex
                roles[fg] = fixed
                ratio = c.wcag_ratio(c.parse(fixed), c.parse(roles[bg]))
            rows.append(
                {
                    "pair": f"{fg} on {bg}",
                    "ratio": round(ratio, 2),
                    "apca": c.apca(c.parse(roles[fg]), c.parse(roles[bg])),
                    "target": target,
                    "pass": ratio >= target,
                }
            )
        report[theme_name] = rows
    return {"light": light, "dark": dark}, report


def typography(base: float, ratio: str | float) -> dict[str, Any]:
    if isinstance(ratio, str):
        key = ratio.strip().lower()
        if key in TYPE_RATIOS:
            value = TYPE_RATIOS[key]
        else:
            try:
                value = float(key)
            except ValueError:
                raise ToolArgumentError(
                    f"type_ratio is a number or one of {', '.join(TYPE_RATIOS)}."
                ) from None
    else:
        value = float(ratio)
    if not 1.0 < value <= 2.0:
        raise ToolArgumentError("type_ratio must be between 1 and 2.")
    small_ratio = 1 + (value - 1) * 0.6
    sizes: dict[str, dict[str, str]] = {}
    for label, step in TYPE_STEPS:
        # Small steps stop at 12px: below that, body text stops being readable.
        large = max(12.0, base * value**step)
        small = base * small_ratio**step if step > 0 else large
        line_height = 1.5 if step <= 0 else max(1.1, 1.5 - step * 0.07)
        tracking = "0" if step <= 1 else f"{-0.005 * step:.3f}em"
        fluid = f"{large / 16:.3f}rem" if abs(large - small) < 0.5 else _clamp(small, large)
        sizes[label] = {
            "size": f"{large / 16:.3f}rem",
            "px": f"{large:.1f}px",
            "fluid": fluid,
            "line-height": f"{line_height:.2f}",
            "letter-spacing": tracking,
        }
    return {
        "base": f"{base:g}px",
        "ratio": value,
        "sizes": sizes,
        "weights": {"regular": 400, "medium": 500, "semibold": 600, "bold": 700},
    }


def _clamp(small: float, large: float, min_vw: float = 360, max_vw: float = 1280) -> str:
    """CSS clamp() growing linearly from ``small`` at 360px to ``large`` at 1280px."""

    slope = (large - small) / (max_vw - min_vw)
    intercept = small - slope * min_vw
    preferred = f"{intercept / 16:.3f}rem + {slope * 100:.3f}vw"
    return f"clamp({small / 16:.3f}rem, {preferred}, {large / 16:.3f}rem)"


def spacing(base: int) -> dict[str, str]:
    if base not in (2, 4, 5, 6, 8):
        raise ToolArgumentError("spacing_base is 4 or 8 (2, 5, 6 also accepted).")
    unit = base / 4
    keys = (0, 0.5, 1, 1.5, 2, 3, 4, 5, 6, 8, 10, 12, 16, 20, 24, 32)
    return {str(key).replace(".", "_"): f"{key * 4 * unit / 16:g}rem" for key in keys}


def radii(style: str) -> dict[str, str]:
    presets = {
        "sharp": (0, 2, 3, 4, 6),
        "default": (4, 6, 8, 12, 16),
        "round": (6, 10, 14, 20, 28),
    }
    key = (style or "default").strip().lower()
    if key not in presets:
        raise ToolArgumentError(f"radius is one of {', '.join(presets)}.")
    sm, md, lg, xl, xxl = presets[key]
    return {
        "none": "0",
        "sm": f"{sm}px",
        "md": f"{md}px",
        "lg": f"{lg}px",
        "xl": f"{xl}px",
        "2xl": f"{xxl}px",
        "full": "9999px",
    }


def shadows(ink: str) -> dict[str, str]:
    rgb = c.parse(ink)
    tone = f"{round(rgb.r * 255)} {round(rgb.g * 255)} {round(rgb.b * 255)}"
    return {
        "xs": f"0 1px 2px rgb({tone} / 0.06)",
        "sm": f"0 1px 3px rgb({tone} / 0.10), 0 1px 2px rgb({tone} / 0.06)",
        "md": f"0 4px 8px -2px rgb({tone} / 0.10), 0 2px 4px -2px rgb({tone} / 0.06)",
        "lg": f"0 12px 16px -4px rgb({tone} / 0.08), 0 4px 6px -2px rgb({tone} / 0.03)",
        "xl": f"0 20px 24px -4px rgb({tone} / 0.08), 0 8px 8px -4px rgb({tone} / 0.03)",
    }


def check_pairs(pairs: list[str]) -> list[dict[str, Any]]:
    """'#fff on #0b3d91' or '#fff/#0b3d91' pairs, graded, with the nearest passing foreground."""

    results = []
    for raw in pairs:
        text = str(raw)
        parts = [p.strip() for p in (text.split(" on ") if " on " in text else text.split("/"))]
        if len(parts) != 2:
            raise ToolArgumentError(f"Pair {raw!r} must look like '#333 on #fff' or '#333/#fff'.")
        fg, bg = c.parse(parts[0]), c.parse(parts[1])
        ratio = c.wcag_ratio(fg, bg)
        entry: dict[str, Any] = {
            "pair": f"{fg.hex} on {bg.hex}",
            "ratio": round(ratio, 2),
            "apca_lc": c.apca(fg, bg),
            **c.grade(ratio),
        }
        if ratio < 4.5:
            entry["suggested_foreground_aa"] = c.fix_contrast(fg, bg, 4.5).hex
        if ratio < 7:
            entry["suggested_foreground_aaa"] = c.fix_contrast(fg, bg, 7.0).hex
        results.append(entry)
    return results


def simulate_colors(colors: list[str]) -> dict[str, Any]:
    parsed = [(value, c.parse(value)) for value in colors]
    table = {}
    confusions = []
    for kind in c.CVD_TYPES:
        simulated = [(value, c.simulate(rgb, kind)) for value, rgb in parsed]
        table[kind] = {value: rgb.hex for value, rgb in simulated}
        for i, (a_name, a) in enumerate(simulated):
            for b_name, b in simulated[i + 1 :]:
                distance = c.delta_e_ok(a, b)
                if distance < 0.06:
                    confusions.append(
                        {
                            "vision": kind,
                            "colors": [a_name, b_name],
                            "delta_e_ok": round(distance, 3),
                        }
                    )
    return {"simulated": table, "hard_to_tell_apart": confusions}
