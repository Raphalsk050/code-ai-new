from __future__ import annotations

import asyncio
import json

import pytest

from code_ai.config.models import AppConfig
from code_ai.core.errors import ToolArgumentError
from code_ai.events.bus import AsyncEventBus
from code_ai.tools.base import TOOL_IMAGES_KEY, ToolCapability, ToolContext
from code_ai.tools.design import DesignSystemTool, UiAuditTool, UiPreviewTool, tokens
from code_ai.tools.design import color as c
from code_ai.util.paths import WorkspacePolicy

FLAWED = """<!doctype html>
<html>
<head><style>
  body { font-family: Arial; margin: 0; }
  .ghost { color: #bbbbbb; background: #ffffff; }
  .tiny { font-size: 10px; }
  .wide { width: 1400px; height: 20px; background: #eee; }
  .icon { width: 16px; height: 16px; display: inline-block; background: #333; border: 0; }
  button:focus { outline: none; }
  .clip { width: 60px; overflow: hidden; white-space: nowrap; }
  .cover { position: absolute; top: 0; left: 0; width: 300px; height: 80px; }
  p a { color: #000; text-decoration: none; }
</style></head>
<body>
  <h1>Shop</h1>
  <h3>Deals</h3>
  <p class="ghost">Low contrast copy that nobody can read comfortably.</p>
  <p class="tiny">Fine print in ten pixels.</p>
  <img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=">
  <input type="text" placeholder="Email">
  <button class="icon"></button>
  <button id="dup">One</button><button id="dup">Two</button>
  <div class="wide"></div>
  <div class="clip">This text is far too long for the box</div>
  <p>Read the <a href="#terms">terms</a> before buying anything at all today.</p>
  <div tabindex="3">Odd tab order</div>
  <div role="bogus">Unknown role</div>
</body>
</html>"""

CLEAN = """<!doctype html>
<html lang="en">
<head><meta name="viewport" content="width=device-width, initial-scale=1"><title>Clean</title>
<style>
  body { font-family: Arial, sans-serif; font-size: 16px; color: #1f2328; background: #fff; }
  body { margin: 0; }
  main { max-width: 640px; margin: 0 auto; padding: 24px; }
  label { display: block; font-weight: 600; margin-bottom: 4px; }
  input { font-size: 16px; min-height: 44px; width: 100%; box-sizing: border-box; }
  button { min-height: 44px; padding: 0 16px; font-size: 16px; border: 0; }
  button { background: #0b3d91; color: #fff; }
  button:focus-visible, input:focus-visible, a:focus-visible {
    outline: 3px solid #1c7ed6; outline-offset: 2px;
  }
  a { color: #0b3d91; text-decoration: underline; }
</style></head>
<body><main>
  <h1>Sign in</h1>
  <p>Use your work account. <a href="#help">Need help?</a></p>
  <label for="email">Email</label><input id="email" type="email">
  <p><button type="button">Continue</button></p>
</main></body></html>"""


def make_context(tmp_path) -> ToolContext:
    config = AppConfig.from_mapping({"api_mode": "ollama", "workspace": str(tmp_path)})
    return ToolContext(
        config=config,
        workspace=WorkspacePolicy.from_path(tmp_path),
        event_bus=AsyncEventBus(session_id="session"),
        cancel_event=asyncio.Event(),
    )


def test_capabilities() -> None:
    assert ToolCapability.WEB in UiAuditTool.capabilities
    assert ToolCapability.LOCAL_WRITE not in UiAuditTool.capabilities


def test_colour_math_matches_references() -> None:
    white, black = c.parse("#fff"), c.parse("black")
    assert round(c.wcag_ratio(c.parse("#777777"), white), 2) == 4.48
    assert round(c.wcag_ratio(black, white), 1) == 21.0
    assert c.apca(black, white) == pytest.approx(106.0, abs=0.2)
    assert c.apca(white, black) == pytest.approx(-107.9, abs=0.2)
    lightness, chroma, hue = c.rgb_to_oklch(c.parse("#ff0000"))
    assert (round(lightness, 3), round(chroma, 3), round(hue, 1)) == (0.628, 0.258, 29.2)
    assert c.oklch_to_rgb(*c.rgb_to_oklch(c.parse("#3b82f6"))).hex == "#3b82f6"
    assert c.parse("rgb(255, 0, 0)").hex == "#ff0000"
    assert c.parse("hsl(120, 100%, 25%)").hex == "#008000"
    with pytest.raises(ToolArgumentError):
        c.parse("not-a-colour")


def test_out_of_gamut_oklch_is_mapped() -> None:
    mapped = c.oklch_to_rgb(0.7, 0.4, 145)
    assert all(0 <= channel <= 1 for channel in (mapped.r, mapped.g, mapped.b))


def test_fix_contrast_reaches_target() -> None:
    fixed = c.fix_contrast(c.parse("#60a5fa"), c.parse("#ffffff"), 4.5)
    assert c.wcag_ratio(fixed, c.parse("#ffffff")) >= 4.5


def test_simulation_flags_red_green_for_achromatopsia() -> None:
    result = tokens.simulate_colors(["#d32f2f", "#2e7d32"])
    assert set(result["simulated"]) == set(c.CVD_TYPES)
    assert any(item["vision"] == "achromatopsia" for item in result["hard_to_tell_apart"])


@pytest.mark.parametrize(
    "brand", ["#0b3d91", "#facc15", "#22c55e", "#7c3aed", "#ef4444", "#111111"]
)
def test_generated_themes_always_pass_contrast(brand) -> None:
    system = tokens.build([brand])
    for rows in system.contrast.values():
        assert rows and all(row["pass"] for row in rows)
    scale = system.colors["primary"]
    lightness = [c.rgb_to_oklch(c.parse(scale[step]))[0] for step in tokens.STEPS]
    assert lightness == sorted(lightness, reverse=True)
    assert brand.lower() in scale.values()


def test_type_scale_spacing_and_validation() -> None:
    typography = tokens.typography(16, "major-third")
    assert typography["sizes"]["base"]["size"] == "1.000rem"
    assert typography["sizes"]["xs"]["px"] == "12.0px"
    assert typography["sizes"]["3xl"]["fluid"].startswith("clamp(")
    assert tokens.spacing(8)["4"] == "2rem"
    with pytest.raises(ToolArgumentError):
        tokens.typography(16, "cosmic")
    with pytest.raises(ToolArgumentError):
        tokens.build([])


async def test_generate_writes_every_format(tmp_path) -> None:
    result = await DesignSystemTool().execute(
        {
            "brand_colors": ["#0b3d91", "#e8590c"],
            "harmony": "triadic",
            "output_dir": "ds",
            "prefix": "ds-",
        },
        make_context(tmp_path),
    )
    assert len(result["files"]) == 6
    assert (
        result["contrast"]["light"]["failing"] == [] and result["contrast"]["dark"]["failing"] == []
    )
    css = (tmp_path / "ds/tokens.css").read_text(encoding="utf-8")
    assert (
        "--ds-primary:" in css
        and '[data-theme="dark"]' in css
        and "prefers-color-scheme: dark" in css
    )
    data = json.loads((tmp_path / "ds/tokens.json").read_text(encoding="utf-8"))
    assert data["color"]["primary"]["500"]["$type"] == "color"
    assert "module.exports" in (tmp_path / "ds/tailwind.config.js").read_text(encoding="utf-8")
    assert "@theme {" in (tmp_path / "ds/theme.css").read_text(encoding="utf-8")
    assert "$ds-theme-dark" in (tmp_path / "ds/_tokens.scss").read_text(encoding="utf-8")
    assert "<main>" in (tmp_path / "ds/styleguide.html").read_text(encoding="utf-8")


async def test_check_and_simulate_modes(tmp_path) -> None:
    context = make_context(tmp_path)
    checked = await DesignSystemTool().execute(
        {"mode": "check", "pairs": ["#999 on #fff", "#000/#fff"]}, context
    )
    assert checked["failing"] == 1
    assert checked["results"][0]["suggested_foreground_aa"]
    simulated = await DesignSystemTool().execute(
        {"mode": "simulate", "colors": ["#d32f2f", "#2e7d32"]}, context
    )
    assert "protanopia" in simulated["simulated"]
    with pytest.raises(ToolArgumentError):
        await DesignSystemTool().execute({"mode": "check"}, context)


async def _browser_or_skip(coro):
    try:
        return await coro
    except Exception as exc:  # noqa: BLE001
        text = str(exc).lower()
        if "browser" in text or "playwright" in text or "chromium" in text:
            pytest.skip(f"Chromium unavailable: {exc}")
        raise


async def test_audit_catches_the_flawed_page(tmp_path) -> None:
    (tmp_path / "flawed.html").write_text(FLAWED, encoding="utf-8")
    result = await _browser_or_skip(
        UiAuditTool().execute(
            {"path": "flawed.html", "viewports": ["mobile"], "max_findings": 200},
            make_context(tmp_path),
        )
    )
    rules = {finding["rule"] for finding in result["findings"]}
    expected = {
        "html-lang",
        "meta-viewport",
        "page-title",
        "landmark-main",
        "heading-order",
        "duplicate-id",
        "color-contrast",
        "small-text",
        "image-alt",
        "form-label",
        "control-name",
        "target-size",
        "horizontal-overflow",
        "clipped-text",
        "tabindex-positive",
        "aria-role",
        "link-distinguishable",
        "focus-visible",
        "input-zoom",
    }
    assert expected <= rules, expected - rules
    assert result["summary"]["score"] < 70
    assert len(result[TOOL_IMAGES_KEY]) == 1
    contrast = next(f for f in result["findings"] if f["rule"] == "color-contrast")
    assert contrast["selector"] and contrast["wcag"] == "1.4.3"


async def test_audit_stays_quiet_on_a_clean_page(tmp_path) -> None:
    result = await _browser_or_skip(
        UiAuditTool().execute(
            {"html": CLEAN, "viewports": ["mobile", "desktop"], "annotate": False},
            make_context(tmp_path),
        )
    )
    serious = [f for f in result["findings"] if f["severity"] in {"serious", "moderate"}]
    assert serious == [], serious


async def test_generated_style_guide_passes_its_own_audit(tmp_path) -> None:
    context = make_context(tmp_path)
    await DesignSystemTool().execute(
        {"brand_colors": ["#7c3aed"], "output_dir": "ds", "formats": ["styleguide"]}, context
    )
    for scheme in ("light", "dark"):
        result = await _browser_or_skip(
            UiAuditTool().execute(
                {
                    "path": "ds/styleguide.html",
                    "viewports": ["desktop"],
                    "color_scheme": scheme,
                    "annotate": False,
                },
                context,
            )
        )
        assert result["findings"] == [], (scheme, result["findings"])


async def test_preview_captures_and_diagnoses(tmp_path) -> None:
    broken = (
        "<html><head><title>P</title></head><body><h1>Hi</h1>"
        "<img src='missing.png' alt='x'><script>boom()</script></body></html>"
    )
    (tmp_path / "page.html").write_text(broken, encoding="utf-8")
    result = await _browser_or_skip(
        UiPreviewTool().execute(
            {
                "path": "page.html",
                "viewports": ["mobile", "1024x768"],
                "color_scheme": "both",
                "save_dir": "shots",
            },
            make_context(tmp_path),
        )
    )
    assert len(result[TOOL_IMAGES_KEY]) == 4
    assert len(result["saved"]) == 4 and (tmp_path / result["saved"][0]).exists()
    capture = result["captures"][0]
    assert capture["title"] == "P" and capture["console_errors"] and capture["failed_requests"]


async def test_preview_rejects_bad_input(tmp_path) -> None:
    context = make_context(tmp_path)
    with pytest.raises(ToolArgumentError, match="exactly one"):
        await UiPreviewTool().execute({}, context)
    with pytest.raises(ToolArgumentError, match="Viewport"):
        await UiPreviewTool().execute({"html": "<p>x</p>", "viewports": ["huge"]}, context)
