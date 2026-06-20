from __future__ import annotations

from importlib import resources

BANNER_RESOURCE = "banner.txt"

FALLBACK_CODE_AI_LOGO = r"""
  ____          _            _    ___
 / ___|___   __| | ___      / \  |_ _|
| |   / _ \ / _` |/ _ \    / _ \  | |
| |__| (_) | (_| |  __/   / ___ \ | |
 \____\___/ \__,_|\___|  /_/   \_\___|
"""


def load_banner_source() -> str:
    try:
        logo = resources.files(__package__).joinpath(BANNER_RESOURCE).read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        return FALLBACK_CODE_AI_LOGO
    if not logo.strip():
        return FALLBACK_CODE_AI_LOGO
    return logo


def load_code_ai_logo() -> str:
    return load_banner_source()


CODE_AI_LOGO = load_code_ai_logo()
