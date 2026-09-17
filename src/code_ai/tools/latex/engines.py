"""Finding a TeX engine: PATH, the usual MiKTeX and TeX Live places, or Code-AI's own install."""

from __future__ import annotations

import glob
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from code_ai.tools.latex.install import managed_bin

ENGINES = ("pdflatex", "xelatex", "lualatex")
_EXE = ".exe" if sys.platform == "win32" else ""


@dataclass
class TexSetup:
    bin_dir: Path | None
    source: str
    engines: dict[str, str] = field(default_factory=dict)
    latexmk: str | None = None
    bibtex: str | None = None
    biber: str | None = None
    tectonic: str | None = None
    managed: bool = False

    @property
    def usable(self) -> bool:
        return bool(self.engines or self.tectonic)

    def describe(self) -> dict:
        return {
            "source": self.source,
            "bin_dir": str(self.bin_dir) if self.bin_dir else None,
            "engines": sorted(self.engines),
            "latexmk": bool(self.latexmk),
            "bibtex": bool(self.bibtex),
            "biber": bool(self.biber),
            "tectonic": bool(self.tectonic),
            "can_install_missing_packages": self.managed,
        }


def _candidate_dirs() -> list[tuple[Path, str, bool]]:
    dirs: list[tuple[Path, str, bool]] = []
    managed = managed_bin()
    if managed is not None:
        dirs.append((managed, "code-ai managed TeX Live", True))
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA", "")
        program_files = [
            os.environ.get("ProgramFiles", "C:/Program Files"),
            os.environ.get("ProgramFiles(x86)", ""),
        ]
        patterns = [f"{local}/Programs/MiKTeX/miktex/bin/x64"]
        patterns += [f"{base}/MiKTeX/miktex/bin/x64" for base in program_files if base]
        patterns += sorted(glob.glob("C:/texlive/20*/bin/windows"), reverse=True)
        patterns += sorted(glob.glob("C:/texlive/20*/bin/win64"), reverse=True)
        patterns += sorted(glob.glob("C:/texlive/20*/bin/win32"), reverse=True)
        dirs += [(Path(p), "MiKTeX" if "MiKTeX" in p else "TeX Live", False) for p in patterns]
    elif sys.platform == "darwin":
        dirs += [(Path("/Library/TeX/texbin"), "MacTeX", False)]
        dirs += [
            (Path(p), "TeX Live", False)
            for p in sorted(glob.glob("/usr/local/texlive/20*/bin/*"), reverse=True)
        ]
    else:
        dirs += [
            (Path(p), "TeX Live", False)
            for p in sorted(glob.glob("/usr/local/texlive/20*/bin/*"), reverse=True)
        ]
        dirs += [(Path.home() / "texlive" / "bin" / "x86_64-linux", "TeX Live", False)]
    return dirs


def _in(directory: Path, name: str) -> str | None:
    for suffix in (_EXE, ".bat", ""):
        candidate = directory / f"{name}{suffix}"
        if candidate.is_file():
            return str(candidate)
    return None


def discover() -> TexSetup:
    """The managed install first (it can add packages on demand), then PATH, then known dirs."""

    for directory, source, managed in _candidate_dirs()[:1]:
        if managed:
            return _setup_from(directory, source, managed=True)
    on_path = {name: shutil.which(name) for name in ENGINES}
    if any(on_path.values()):
        setup = TexSetup(
            bin_dir=Path(next(v for v in on_path.values() if v)).parent,
            source="PATH",
            engines={k: v for k, v in on_path.items() if v},
            latexmk=shutil.which("latexmk"),
            bibtex=shutil.which("bibtex"),
            biber=shutil.which("biber"),
            tectonic=shutil.which("tectonic"),
        )
        return setup
    for directory, source, managed in _candidate_dirs():
        if directory.is_dir() and any(_in(directory, e) for e in ENGINES):
            return _setup_from(directory, source, managed=managed)
    tectonic = shutil.which("tectonic")
    return TexSetup(bin_dir=None, source="tectonic" if tectonic else "none", tectonic=tectonic)


def _setup_from(directory: Path, source: str, *, managed: bool) -> TexSetup:
    return TexSetup(
        bin_dir=directory,
        source=source,
        engines={e: path for e in ENGINES if (path := _in(directory, e))},
        latexmk=_in(directory, "latexmk"),
        bibtex=_in(directory, "bibtex"),
        biber=_in(directory, "biber"),
        tectonic=shutil.which("tectonic"),
        managed=managed,
    )
