"""The images a Markdown file shows, attached when read_file reads it.

A design note, a README or a tutorial carries part of its meaning in pictures:
the architecture diagram, the screenshot of the dialog being described, the
chart of the numbers a paragraph quotes. Read as text, the agent gets the
paragraphs and a file name where each picture was. So read_file hands over the
pictures the returned lines reference, the way capture_screen hands over the
desktop: on the payload under TOOL_IMAGES_KEY, which the orchestrator turns
into an attachment - or, for a main model that cannot see, into a description
written by the vision model.

Two things are never done for a reference. Nothing is fetched from the network:
a file read must not turn into a request to wherever a document points. And
nothing outside the tree the document was read from is opened: every path goes
through the same boundary as read_file's own, and one that escapes is reported,
not followed.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import html
import io
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, unquote_to_bytes

from code_ai.core.errors import WorkspaceBoundaryError
from code_ai.tools.base import TOOL_IMAGES_KEY
from code_ai.tools.locations import ResolvedLocation
from code_ai.tools.office.common import image_entry
from code_ai.util.fileio import (
    NO_RETRY,
    FileOperationError,
    RetryPolicy,
    describe_os_error,
    read_bytes,
)

MARKDOWN_SUFFIXES = frozenset({".md", ".markdown", ".mdx"})

# Images attached to one read. A page of documentation rarely shows more than a
# handful of figures, and each costs on the order of a thousand or two tokens
# on every request that still carries it; past eight, reading a long document
# whole would spend more on pictures than on its text, and an endpoint that
# caps images per request would have most of them transcribed instead. The
# ones past the limit are listed, so a narrower line range picks them up.
MAX_IMAGES_PER_READ = 8

# Longest edge an image is reduced to: the bound the clipboard paste and the
# screen capture use, for the same reason. A vision model reads no more detail
# from a larger image, and every pixel rides along on each later request that
# keeps it.
MAX_IMAGE_EDGE_PX = 1568

# An image file larger than this is not read at all. Nothing that heavy is a
# figure meant to be looked at on a page - it is a raw photo, a scan, a texture
# atlas - and decoding it would cost more time and memory than the downscaled
# copy is worth.
MAX_IMAGE_FILE_BYTES = 16 * 1024 * 1024

# The same bound in decoded pixels, for an image that is small on disk and huge
# in memory: a flat-colour PNG compresses ten-thousand-fold, and decoding is
# what costs.
MAX_IMAGE_PIXELS = 40_000_000

# A document longer than this is parsed only down to the last line returned.
# Parsing runs at about a megabyte a second, and while what comes before the
# range is needed (a code fence opened above it decides what inside it is an
# image), what comes after only contributes reference definitions, which in a
# document this size are not worth seconds on every read of one of its pages.
MAX_SCAN_CHARS = 1024 * 1024

# Skipped references named in the result; the rest are only counted, so a page
# of remote badges cannot crowd the file's own text out of the result.
MAX_REPORTED_SKIPS = 12

# Formats read, by the name Pillow detects from the bytes rather than the file
# name. PNG and JPEG travel as they are when they need no change; the others
# go out as PNG, since few local servers decode WebP or BMP, and a GIF is read
# for its first frame.
_FORMATS = frozenset({"PNG", "JPEG", "GIF", "WEBP", "BMP"})
_EXIF_ORIENTATION = 0x0112

_REMOTE = "remote image, not fetched"
_SVG = "unsupported format (svg): vector images cannot be rasterized here"
_SVG_SUFFIXES = (".svg", ".svgz")

# A scheme has two letters or more, so "C:" in a Windows path is not one.
_SCHEME = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.-]+):")
_DATA_URI = re.compile(r"^data:([^,]*),(.*)$", re.DOTALL | re.IGNORECASE)
# Only spends a parse on a range that could hold an image at all.
_MAY_HOLD_IMAGE = re.compile(r"!\[|<img", re.IGNORECASE)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
# Quoted attribute values may contain ">", so they are matched whole.
_IMG_TAG = re.compile(r"""<img\b(?:[^>"']|"[^"]*"|'[^']*')*>""", re.IGNORECASE)
# Not preceded by a word character or a hyphen, so data-src is not src.
_IMG_ATTR = re.compile(
    r"""(?<![\w-])(src|alt)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+))""",
    re.IGNORECASE,
)
_BREAKS = frozenset({"softbreak", "hardbreak"})


def is_markdown(path: Path) -> bool:
    return path.suffix.lower() in MARKDOWN_SUFFIXES


@dataclass(frozen=True, slots=True)
class ImageReference:
    """One image a document shows: where it points, and the line it is on."""

    source: str
    # 1-based, the way read_file numbers lines.
    line: int
    alt: str = ""


def markdown_image_fields(
    lines: list[str],
    returned: Sequence[tuple[int, int]],
    *,
    document: Path,
    location: ResolvedLocation,
    policy: RetryPolicy = NO_RETRY,
) -> dict[str, Any]:
    """Payload fields for the images on the returned lines of a Markdown file.

    ``lines`` is the whole file as read_file split it, line endings kept, and
    ``returned`` the 1-based inclusive line ranges the result carries. Empty
    when those lines reference no image. Blocking: it parses the document and
    reads image files, so call it from a worker thread.
    """

    references = find_image_references(lines, returned)
    if not references:
        return {}
    return attach_images(references, document=document, location=location, policy=policy)


def find_image_references(
    lines: list[str], returned: Sequence[tuple[int, int]]
) -> list[ImageReference]:
    """Every image reference on the returned lines, in the order they appear.

    Markdown images (``![alt](path "title")``), reference-style ones whose
    definition may be anywhere in the file, and HTML ``<img src>`` tags. What
    sits in a code fence, an indented code block, a code span or an HTML
    comment is text about an image, not an image, and is left out.
    """

    if not any(
        _MAY_HOLD_IMAGE.search("".join(lines[first - 1 : last])) for first, last in returned
    ):
        return []
    last_returned = max(last for _, last in returned)
    if sum(len(line) for line in lines) > MAX_SCAN_CHARS:
        lines = lines[:last_returned]
    # One line per line read_file counted. Its own split also breaks on form
    # feeds and Unicode separators, which the parser treats as text, so the
    # numbers would drift apart if it were handed the raw file.
    source = "\n".join((line.splitlines() or [""])[0] for line in lines)
    parser = _parser()
    env: dict[str, Any] = {}
    found: list[ImageReference] = []
    for token in parser.parse(source, env):
        if token.map is None:
            continue
        first, last = token.map[0] + 1, token.map[1]
        if not any(low <= last and first <= high for low, high in returned):
            continue
        if token.type == "inline":
            token.children = []
            parser.inline.parse(token.content, parser, env, token.children)
            found.extend(_inline_references(token.children, first))
        elif token.type == "html_block":
            found.extend(_html_references(token.content, first))
    return [
        reference
        for reference in found
        if any(low <= reference.line <= high for low, high in returned)
    ]


def _parser() -> Any:
    from markdown_it import MarkdownIt

    parser = MarkdownIt("commonmark", {"html": True}).enable("table")
    # Inline parsing is the costly half, and only the returned blocks need it:
    # the block pass alone settles the code fences and collects every reference
    # definition in the file.
    parser.core.ruler.disable("inline")
    # Destinations exactly as written. The defaults drop some schemes (an SVG
    # data URI would vanish instead of being reported) and percent-encode the
    # rest, which turns a file name back into a URL.
    parser.validateLink = lambda url: True
    parser.normalizeLink = lambda url: url
    return parser


def _inline_references(children: list[Any], first_line: int) -> list[ImageReference]:
    """Images in one block's inline tokens, each on the line it starts.

    Tokens carry no position of their own, so the line is counted from the
    block's first one by the breaks in between. A code span that wraps a line
    is the one thing that hides a break; it puts what follows it a line early.
    """

    found: list[ImageReference] = []
    line = first_line
    for child in children:
        if child.type in _BREAKS:
            line += 1
        elif child.type == "image":
            found.append(ImageReference(str(child.attrGet("src") or ""), line, child.content))
            line += sum(1 for part in child.children or [] if part.type in _BREAKS)
        elif child.type == "html_inline":
            found.extend(_html_references(child.content, line))
            line += child.content.count("\n")
    return found


def _html_references(markup: str, first_line: int) -> list[ImageReference]:
    # A comment becomes the line breaks it spanned, so later lines keep their numbers.
    markup = _HTML_COMMENT.sub(lambda match: "\n" * match.group(0).count("\n"), markup)
    found: list[ImageReference] = []
    for tag in _IMG_TAG.finditer(markup):
        attributes: dict[str, str] = {}
        for match in _IMG_ATTR.finditer(tag.group(0)):
            value = next(part for part in match.groups()[1:] if part is not None)
            attributes.setdefault(match.group(1).lower(), html.unescape(value))
        if "src" in attributes:
            line = first_line + markup.count("\n", 0, tag.start())
            found.append(ImageReference(attributes["src"], line, attributes.get("alt", "")))
    return found


class _Skipped(Exception):
    """Why a reference is listed instead of attached."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class _Target:
    """A reference that names something local: a file, or bytes in the document."""

    # Same picture, same key: a figure shown twice is attached once.
    key: str
    path: Path | None = None
    data: bytes | None = None
    svg: bool = False

    def check(self) -> None:
        """Refuse what can be refused without reading it."""

        if self.svg:
            raise _Skipped(_SVG)
        size = self.path.stat().st_size if self.path is not None else len(self.data or b"")
        if size > MAX_IMAGE_FILE_BYTES:
            raise _Skipped(
                f"file too large ({size / 1048576:.1f} MiB, limit "
                f"{MAX_IMAGE_FILE_BYTES // 1048576} MiB)"
            )

    def read(self, policy: RetryPolicy) -> bytes:
        if self.path is not None:
            return read_bytes(self.path, policy=policy)
        return self.data or b""


def attach_images(
    references: Sequence[ImageReference],
    *,
    document: Path,
    location: ResolvedLocation,
    policy: RetryPolicy = NO_RETRY,
) -> dict[str, Any]:
    """The pictures for ``references``, and an account of each one.

    The account is what lets the model tell the pictures apart: attached ones
    are numbered in the order they are attached, which is the order they
    appear, and named by the reference as written. Everything else is listed
    with the reason it was not attached. Blocking.
    """

    pictures: list[dict[str, str]] = []
    attached: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    # Per key: the number a picture was attached as, or why it was not.
    outcomes: dict[str, int | str] = {}
    for reference in references:
        if not reference.source.strip():
            continue
        entry: dict[str, Any] = {"ref": _name(reference.source), "line": reference.line}
        if reference.alt.strip():
            entry["alt"] = _clip(reference.alt.strip(), 100)
        try:
            target = _locate(reference.source, document, location)
            earlier = outcomes.get(target.key)
            if isinstance(earlier, int):
                raise _Skipped(f"same image as image {earlier}")
            if earlier is not None:
                raise _Skipped(earlier)
            try:
                target.check()
                # Counted before reading, so an image past the limit costs nothing.
                if len(pictures) >= MAX_IMAGES_PER_READ:
                    raise _Skipped(
                        f"over the limit of {MAX_IMAGES_PER_READ} images per read; "
                        "read a narrower line range to see it"
                    )
                picture, original = _encode(target.read(policy))
            except (OSError, FileOperationError) as exc:
                # Permissions, or a file another program holds past the retries:
                # one picture lost, never the read it belongs to.
                cause = exc.__cause__ if isinstance(exc, FileOperationError) else exc
                detail = describe_os_error(cause) if isinstance(cause, OSError) else str(exc)
                raise _Skipped(f"could not be read: {detail}") from None
            except _Skipped as skip:
                outcomes[target.key] = skip.reason
                raise
        except _Skipped as skip:
            skipped.append({**entry, "reason": skip.reason})
            continue
        pictures.append(picture)
        outcomes[target.key] = len(pictures)
        if target.path is not None and location.relative(target.path) != reference.source:
            entry["path"] = location.relative(target.path)
        if original is not None:
            entry["downscaled_from"] = f"{original[0]}x{original[1]}"
        attached.append({"image": len(pictures), **entry})

    report: dict[str, Any] = {}
    if attached:
        report["attached"] = attached
    if skipped:
        report["skipped"] = skipped[:MAX_REPORTED_SKIPS]
        if len(skipped) > MAX_REPORTED_SKIPS:
            report["more_skipped"] = len(skipped) - MAX_REPORTED_SKIPS
    fields: dict[str, Any] = {"images": report} if report else {}
    if pictures:
        fields[TOOL_IMAGES_KEY] = pictures
    return fields


def _locate(source: str, document: Path, location: ResolvedLocation) -> _Target:
    source = source.strip()
    if source.startswith("//"):
        raise _Skipped(_REMOTE)
    scheme = _SCHEME.match(source)
    if scheme is None:
        return _file_target(source, document, location)
    name = scheme.group(1).lower()
    if name in {"http", "https"}:
        raise _Skipped(_REMOTE)
    if name == "data":
        return _data_target(source)
    raise _Skipped(f"unsupported reference ({name}:)")


def _file_target(source: str, document: Path, location: ResolvedLocation) -> _Target:
    """The file a relative or absolute reference names, inside the location only.

    Relative to the document, the way every renderer reads it. A path that
    resolves outside the tree, through ``..``, an absolute path or a symlink,
    is refused by the same policy that bounds read_file's own path, before
    anything is opened.
    """

    outside = False
    for spelling in _spellings(source):
        try:
            path = location.resolve(document.parent / spelling)
            if path.is_file():
                return _Target(key=str(path), path=path, svg=path.suffix.lower() in _SVG_SUFFIXES)
            if path.exists():
                raise _Skipped("not a file")
        except WorkspaceBoundaryError:
            outside = True
        except OSError:
            # A name the filesystem cannot even look up (too long, say) names
            # no file; it must not fail the read it was found in.
            continue
    if outside:
        raise _Skipped(f"outside the {'sandbox' if location.is_sandbox else 'workspace'}")
    raise _Skipped("not found")


def _spellings(source: str) -> list[str]:
    """The file names a renderer could mean by ``source``, as written first.

    Renderers decode ``%20`` and drop a ``?raw=true`` or a ``#gh-dark-mode-only``
    before looking for the file; a file really named with those characters is
    still found, because the literal spelling is tried first.
    """

    bare = re.split(r"[?#]", source, maxsplit=1)[0]
    spellings: list[str] = []
    for spelling in (source, unquote(source), bare, unquote(bare)):
        if spelling and spelling not in spellings:
            spellings.append(spelling)
    return spellings


def _data_target(source: str) -> _Target:
    match = _DATA_URI.match(source)
    if match is None:
        raise _Skipped("invalid data URI")
    parameters = [part.strip().lower() for part in match.group(1).split(";")]
    media_type = parameters[0] or "text/plain"
    if not media_type.startswith("image/"):
        raise _Skipped(f"unsupported reference (data:{media_type})")
    body = match.group(2)
    if "base64" in parameters[1:]:
        compact = "".join(body.split())
        try:
            data = base64.b64decode(compact + "=" * (-len(compact) % 4))
        except (binascii.Error, ValueError):
            raise _Skipped("invalid data URI") from None
    else:
        data = unquote_to_bytes(body)
    return _Target(
        key="data:" + hashlib.sha256(data).hexdigest(),
        data=data,
        svg=media_type.startswith("image/svg"),
    )


def _encode(data: bytes) -> tuple[dict[str, str], tuple[int, int] | None]:
    """The picture as it is sent, and its original size when it had to shrink.

    Decoded either way, even when the bytes go out untouched: a truncated file
    is better found here, as one skipped image, than by an endpoint that
    refuses the whole request over it.
    """

    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - a required dependency
        raise _Skipped("Pillow is not installed to decode it") from None
    try:
        image = Image.open(io.BytesIO(data))
    except Image.DecompressionBombError:
        raise _Skipped("image too large to decode") from None
    except Exception:  # noqa: BLE001 - whatever the bytes are, they are not a picture
        raise _Skipped(_SVG if _looks_like_svg(data) else "not a readable image") from None
    with image:
        kind = image.format or ""
        if kind not in _FORMATS:
            raise _Skipped(f"unsupported format ({kind.lower() or 'unknown'})")
        width, height = image.size
        if width * height > MAX_IMAGE_PIXELS:
            raise _Skipped(f"image too large ({width}x{height} pixels)")
        try:
            return _fit(image, data, kind)
        except Exception:  # noqa: BLE001 - truncated or corrupt past the header
            raise _Skipped("not a readable image") from None


def _fit(image: Any, data: bytes, kind: str) -> tuple[dict[str, str], tuple[int, int] | None]:
    from PIL import Image, ImageOps

    size = image.size
    upright = image.getexif().get(_EXIF_ORIENTATION, 1) == 1
    if kind in {"PNG", "JPEG"} and max(size) <= MAX_IMAGE_EDGE_PX and upright:
        image.load()
        return image_entry(data, "image/png" if kind == "PNG" else "image/jpeg"), None
    if kind == "JPEG":
        # Decodes at a fraction of the size straight away, instead of decoding
        # a camera photo whole only to throw most of it away.
        image.draft("RGB", (MAX_IMAGE_EDGE_PX, MAX_IMAGE_EDGE_PX))
    # The first frame of an animation, turned the way a viewer would show it.
    picture = ImageOps.exif_transpose(image)
    alpha = picture.mode in {"RGBA", "LA", "PA"} or "transparency" in picture.info
    # Converted before shrinking: a palette image would be resized by its
    # nearest pixel, which turns text and thin lines into noise.
    picture = picture.convert("RGBA" if alpha and kind != "JPEG" else "RGB")
    picture.thumbnail((MAX_IMAGE_EDGE_PX, MAX_IMAGE_EDGE_PX), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    if kind == "JPEG":
        # A photo stays a photo: as PNG it would be several times the bytes.
        picture.save(buffer, format="JPEG", quality=85)
        media_type = "image/jpeg"
    else:
        picture.save(buffer, format="PNG")
        media_type = "image/png"
    shrunk = max(size) > MAX_IMAGE_EDGE_PX
    return image_entry(buffer.getvalue(), media_type), (size if shrunk else None)


def _looks_like_svg(data: bytes) -> bool:
    head = data[:4096].lstrip().lower()
    return head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in head)


def _name(source: str) -> str:
    """The reference as the model will recognise it in the text it was given."""

    source = source.strip()
    if source[:5].lower() == "data:":
        # The header says what it is; the payload is noise, and long.
        return _clip(source.split(",", 1)[0], 60) + ",..."
    return _clip(source, 200)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."
