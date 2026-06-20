from __future__ import annotations

import asyncio
import html
import ipaddress
import json
import re
import shutil
import ssl
import subprocess
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urlencode, urlparse
from urllib.request import HTTPSHandler, Request, build_opener

MAX_RESPONSE_BYTES = 1_000_000
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)
DUCKDUCKGO_HTML_ENDPOINT = "https://html.duckduckgo.com/html/"
GOOGLE_SEARCH_ENDPOINT = "https://www.google.com/search"
SEARXNG_ENDPOINTS = (
    "https://searx.be/search",
    "https://search.inetol.net/search",
    "https://searxng.site/search",
)


@dataclass(slots=True)
class WebSearchResult:
    title: str
    url: str
    snippet: str
    source: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


class WebSearchBackend(Protocol):
    async def search(
        self,
        query: str,
        *,
        max_results: int,
        region: str | None = None,
        time_filter: str | None = None,
        timeout: float,
    ) -> list[WebSearchResult]:
        raise NotImplementedError


@dataclass(slots=True)
class WebPageText:
    url: str
    title: str
    text: str
    source: str = "fetch"

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SearchRequest:
    query: str
    max_results: int
    region: str | None
    time_filter: str | None
    timeout: float

    @property
    def duckduckgo_region(self) -> str:
        return self.region or "br-pt"

    @property
    def google_hl(self) -> str:
        region = (self.region or "").lower()
        return "pt-BR" if region.startswith("br") or "pt" in region else "en"

    @property
    def google_gl(self) -> str:
        region = (self.region or "").lower()
        return "BR" if region.startswith("br") or "pt" in region else "US"


class DDGSWebSearchBackend:
    """Search backend with direct-provider fallbacks before the ddgs package.

    The old implementation delegated directly to `ddgs`, which may route through
    providers such as Yahoo and still return weak or stale results. This backend
    mirrors the previously validated local-agent approach: try direct HTML/JSON
    providers with browser-like headers, parse and normalize links locally, then
    use `ddgs` only as a final fallback.
    """

    async def search(
        self,
        query: str,
        *,
        max_results: int,
        region: str | None = None,
        time_filter: str | None = None,
        timeout: float,
    ) -> list[WebSearchResult]:
        request = SearchRequest(
            query=query,
            max_results=max_results,
            region=region,
            time_filter=time_filter,
            timeout=timeout,
        )
        return await asyncio.wait_for(
            asyncio.to_thread(_search_with_cascade, request),
            timeout=timeout + 2.0,
        )


def _search_with_cascade(request: SearchRequest) -> list[WebSearchResult]:
    providers = []
    if shutil.which("curl"):
        providers.append(("duckduckgo_curl", _search_duckduckgo_curl))
    providers.extend(
        [
            ("duckduckgo_post", _search_duckduckgo_post),
            ("searxng", _search_searxng),
            ("google", _search_google),
            ("ddgs", _search_ddgs_package),
        ]
    )

    failures: list[str] = []
    for provider_name, provider in providers:
        try:
            results = provider(request)
        except Exception as exc:
            failures.append(f"{provider_name}: {exc}")
            continue
        if results:
            return results[: request.max_results]
        failures.append(f"{provider_name}: no usable results")

    detail = "; ".join(failures[-4:]) if failures else "no providers were available"
    raise RuntimeError(f"No web search provider returned usable results. {detail}")


def _search_duckduckgo_curl(request: SearchRequest) -> list[WebSearchResult]:
    post_data = urlencode({"q": request.query, "kl": request.duckduckgo_region})
    try:
        proc = subprocess.run(
            [
                "curl",
                "-s",
                "--insecure",
                "--max-time",
                str(min(int(request.timeout), 20)),
                "-X",
                "POST",
                DUCKDUCKGO_HTML_ENDPOINT,
                "--data",
                post_data,
                "--compressed",
                "-H",
                "Content-Type: application/x-www-form-urlencoded",
                "-H",
                f"User-Agent: {DEFAULT_USER_AGENT}",
                "-H",
                "Referer: https://duckduckgo.com/",
                "-H",
                "Origin: https://duckduckgo.com",
                "-H",
                "Accept-Language: pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
                "-H",
                "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            ],
            capture_output=True,
            text=True,
            timeout=request.timeout + 5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"curl failed: {exc}") from exc
    if proc.returncode != 0:
        raise RuntimeError(f"curl exited with status {proc.returncode}: {proc.stderr[:200]}")

    return _parse_duckduckgo_results(proc.stdout or "", request.max_results, "duckduckgo_curl")


def _search_duckduckgo_post(request: SearchRequest) -> list[WebSearchResult]:
    body = urlencode({"q": request.query, "kl": request.duckduckgo_region}).encode("utf-8")
    text, final_url, content_type = _http_request(
        DUCKDUCKGO_HTML_ENDPOINT,
        request,
        method="POST",
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": "https://duckduckgo.com/",
            "Origin": "https://duckduckgo.com",
        },
    )
    _raise_if_blocked_search_response(text, final_url, content_type)
    return _parse_duckduckgo_results(text, request.max_results, "duckduckgo")


def _search_searxng(request: SearchRequest) -> list[WebSearchResult]:
    last_error = ""
    for endpoint in SEARXNG_ENDPOINTS:
        try:
            params = {
                "q": request.query,
                "format": "json",
                "language": "pt-BR" if request.google_gl == "BR" else "en",
                "safesearch": "0",
            }
            text, _final_url, _content_type = _http_request(
                f"{endpoint}?{urlencode(params)}",
                request,
            )
            data = json.loads(text)
            results = []
            seen: set[str] = set()
            for item in data.get("results", []):
                url = _normalize_result_url(str(item.get("url", "")))
                if not _is_usable_result_url(url) or url in seen:
                    continue
                seen.add(url)
                results.append(
                    WebSearchResult(
                        title=_compact_text(str(item.get("title", "Result")), 240),
                        url=url,
                        snippet=_compact_text(str(item.get("content", "")), 700),
                        source="searxng",
                    )
                )
                if len(results) >= request.max_results:
                    break
            if results:
                return results
        except Exception as exc:
            last_error = str(exc)
            continue
    raise RuntimeError(f"SearXNG returned no usable results. Last error: {last_error}")


def _search_google(request: SearchRequest) -> list[WebSearchResult]:
    params = {
        "q": request.query,
        "hl": request.google_hl,
        "gl": request.google_gl,
        "num": str(request.max_results),
    }
    text, final_url, content_type = _http_request(
        f"{GOOGLE_SEARCH_ENDPOINT}?{urlencode(params)}",
        request,
        headers={"Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8"},
    )
    _raise_if_blocked_search_response(text, final_url, content_type)

    entries = _parse_google_results_fallback(text, request.max_results)
    results = []
    seen: set[str] = set()
    for entry in entries:
        url = _extract_google_url(entry.get("url", ""))
        if not url:
            continue
        url = _normalize_result_url(url)
        if not _is_usable_result_url(url) or url in seen:
            continue
        seen.add(url)
        results.append(
            WebSearchResult(
                title=_compact_text(entry.get("title", "Search result"), 240),
                url=url,
                snippet=_compact_text(entry.get("snippet", ""), 700),
                source="google",
            )
        )
        if len(results) >= request.max_results:
            break
    if not results:
        raise RuntimeError("Google returned no parseable public result URLs")
    return results


def _search_ddgs_package(request: SearchRequest) -> list[WebSearchResult]:
    try:
        from ddgs import DDGS  # type: ignore
    except Exception as exc:
        raise RuntimeError("ddgs is not installed") from exc

    with DDGS(timeout=request.timeout) as ddgs:
        raw = ddgs.text(
            request.query,
            region=request.region,
            timelimit=request.time_filter,
            max_results=request.max_results,
        )
        results = []
        seen: set[str] = set()
        for item in raw:
            url = _normalize_result_url(str(item.get("href") or item.get("url") or ""))
            if not _is_usable_result_url(url) or url in seen:
                continue
            seen.add(url)
            results.append(
                WebSearchResult(
                    title=_compact_text(str(item.get("title", "")), 240),
                    url=url,
                    snippet=_compact_text(str(item.get("body") or item.get("snippet") or ""), 700),
                    source="ddgs",
                )
            )
        return results


def _parse_duckduckgo_results(
    body: str, max_results: int, provider_name: str
) -> list[WebSearchResult]:
    _raise_if_bot_challenge(body)
    parser = DuckDuckGoParser()
    parser.feed(body)

    results: list[WebSearchResult] = []
    seen: set[str] = set()
    for item in parser.results:
        url = _normalize_result_url(item.get("url", ""))
        if not _is_usable_result_url(url) or url in seen:
            continue
        seen.add(url)
        title = _compact_text(item.get("title", ""), 240)
        if not title:
            continue
        results.append(
            WebSearchResult(
                title=title,
                url=url,
                snippet=_compact_text(item.get("snippet", ""), 700),
                source=provider_name,
            )
        )
        if len(results) >= max_results:
            break
    if not results:
        raise RuntimeError(f"{provider_name} returned no usable results")
    return results


def _http_request(
    url: str,
    request: SearchRequest,
    *,
    method: str = "GET",
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[str, str, str]:
    _validate_public_url(url)
    merged_headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/json,text/plain;q=0.9,*/*;q=0.2",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    if headers:
        merged_headers.update(headers)

    opener = build_opener(HTTPSHandler(context=ssl._create_unverified_context()))
    http_request = Request(url, data=data, headers=merged_headers, method=method)
    try:
        with opener.open(http_request, timeout=request.timeout) as response:
            final_url = response.geturl() or url
            _validate_public_url(final_url)
            content_type = response.headers.get("Content-Type", "")
            if not _content_type_allowed(content_type):
                raise RuntimeError(f"Blocked non-text content type: {content_type}")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} while fetching {url}") from exc
    except URLError as exc:
        raise RuntimeError(f"Network error while fetching {url}: {exc.reason!r}") from exc
    except OSError as exc:
        raise RuntimeError(f"OS/socket error while fetching {url}: {exc!r}") from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raw = raw[:MAX_RESPONSE_BYTES]
    return _decode_bytes(raw), final_url, content_type


def _raise_if_blocked_search_response(body: str, final_url: str, content_type: str) -> None:
    content_type_lower = content_type.lower()
    if (
        not content_type_lower.startswith(("text/", "application/json"))
        and "html" not in content_type_lower
    ):
        raise RuntimeError(f"Unsupported search content type: {content_type}")
    if final_url and "sorry" in urlparse(final_url).path.lower():
        raise RuntimeError(f"Search provider returned anti-bot page: {final_url}")
    _raise_if_bot_challenge(body)


def _raise_if_bot_challenge(body: str) -> None:
    text = (body or "").lower()
    markers = (
        "captcha",
        "anti-bot",
        "detected unusual traffic",
        "please verify you are a human",
        "confirm this search was made by a human",
        "bots use duckduckgo too",
        "your access to this page has been blocked",
        "automated query",
    )
    if any(marker in text for marker in markers):
        raise RuntimeError("search provider returned an anti-bot challenge")


def _validate_public_url(url: str) -> None:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in {"http", "https"}:
        raise RuntimeError("Only http and https URLs are allowed")
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise RuntimeError("URL host is required")
    if host in {"localhost", "localhost.localdomain", "0.0.0.0"} or host.endswith(".local"):
        raise RuntimeError(f"Blocked local host: {host}")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return
    if (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        raise RuntimeError(f"Blocked non-public IP address: {address}")


def _content_type_allowed(content_type: str) -> bool:
    if not content_type:
        return True
    normalized = content_type.split(";", 1)[0].strip().lower()
    return (
        normalized.startswith("text/")
        or normalized in {"application/json", "application/ld+json", "application/xhtml+xml"}
    )


def _parse_google_results_fallback(body: str, limit: int) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    h3_patterns = [
        re.compile(
            r'<a[^>]+href="(?P<href>[^"]+)"[^>]*>.*?<h3[^>]*>(?P<title>.*?)</h3>.*?</a>',
            re.IGNORECASE | re.DOTALL,
        ),
        re.compile(
            r"<a[^>]+href='(?P<href>[^']+)'[^>]*>.*?<h3[^>]*>(?P<title>.*?)</h3>.*?</a>",
            re.IGNORECASE | re.DOTALL,
        ),
    ]
    for pattern in h3_patterns:
        for match in pattern.finditer(body):
            href = html.unescape((match.group("href") or "").strip())
            title = _html_to_text(match.group("title") or "")
            url = _extract_google_url(href)
            if not url or not title or not _looks_like_google_result_title(title):
                continue
            if any(existing["url"] == url for existing in results):
                continue
            results.append({"title": title, "url": url, "snippet": ""})
            if len(results) >= limit:
                return results
        if results:
            break
    if results:
        return results

    patterns = [
        re.compile(
            r'<a[^>]+href="(?P<href>[^"]+)"[^>]*>'
            r"(?:(?:(?!</a>).)*?<h3[^>]*>(?P<title>.*?)</h3>)?"
            r"(?:(?:(?!</a>).)*?(?P<text>[^<]{8,260}))?</a>",
            re.IGNORECASE | re.DOTALL,
        ),
        re.compile(
            r"<a[^>]+href='(?P<href>[^']+)'[^>]*>"
            r"(?:(?:(?!</a>).)*?<h3[^>]*>(?P<title>.*?)</h3>)?"
            r"(?:(?:(?!</a>).)*?(?P<text>[^<]{8,260}))?</a>",
            re.IGNORECASE | re.DOTALL,
        ),
    ]
    for pattern in patterns:
        for match in pattern.finditer(body):
            href = html.unescape((match.group("href") or "").strip())
            title = _html_to_text(match.group("title") or match.group("text") or "")
            url = _extract_google_url(href)
            if not url or not title or not _looks_like_google_result_title(title):
                continue
            if any(existing["url"] == url for existing in results):
                continue
            results.append({"title": title, "url": url, "snippet": ""})
            if len(results) >= limit:
                return results
        if results:
            break

    for match in re.finditer(
        r'href=[\'"](?P<href>https?://[^\'" \s]+)[\'"][^>]*>(?P<text>[^<]{8,260})',
        body,
    ):
        href = html.unescape((match.group("href") or "").strip())
        title = _html_to_text(match.group("text") or "")
        url = _extract_google_url(href)
        if not url or not title or not _looks_like_google_result_title(title):
            continue
        if any(existing["url"] == url for existing in results):
            continue
        results.append({"title": title, "url": url, "snippet": ""})
        if len(results) >= limit:
            break
    return results


def _extract_google_url(href: str) -> str | None:
    value = html.unescape(str(href or "").strip())
    if not value:
        return None
    if value.startswith("//"):
        value = "https:" + value
    if value.startswith("/url?"):
        query = parse_qs(urlparse(value).query)
        for key in ("q", "url", "source", "adurl"):
            candidate = query.get(key, [None])[0]
            if candidate:
                return candidate
    if value.startswith(("http://", "https://")):
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        query = parse_qs(parsed.query)
        if host.endswith(
            ("google.com", "google.com.br", "googleadservices.com", "googleusercontent.com")
        ) and parsed.path.startswith(("/url", "/aclk")):
            for key in ("q", "url", "source", "adurl"):
                candidate = query.get(key, [None])[0]
                if candidate:
                    return candidate
            return None
        return value
    return None


def _normalize_result_url(url: str) -> str:
    value = html.unescape(str(url or "")).strip()
    if not value:
        return ""
    value = re.sub(r"^(https?):\s*//\s*", r"\1://", value, flags=re.IGNORECASE)
    if value.startswith("//"):
        value = "https:" + value
    parsed = urlparse(value)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        if target:
            return _normalize_result_url(unquote(target))
    return value


def _is_usable_result_url(url: str) -> bool:
    if not url:
        return False
    try:
        _validate_public_url(url)
    except RuntimeError:
        return False
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path.lower()
    if host.endswith("duckduckgo.com") and path.startswith(("/y.js", "/html")):
        return False
    if host.endswith("bing.com") and path.startswith("/aclick"):
        return False
    if host.endswith(
        (
            "google.com",
            "google.com.br",
            "googleadservices.com",
            "googlesyndication.com",
            "doubleclick.net",
            "adservice.google.com",
            "gstatic.com",
        )
    ):
        return False
    return True


def _looks_like_google_result_title(title: str) -> bool:
    return bool(title and len(title.strip()) >= 4 and any(char.isalnum() for char in title))


def _html_to_text(value: str) -> str:
    text = re.sub(r"<[^>]+>", "", value or "")
    return html.unescape(re.sub(r"\s+", " ", text)).strip()


def _compact_text(value: str, limit: int) -> str:
    text = _html_to_text(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 20)].rstrip() + " ...[truncated]"


def _decode_bytes(raw: bytes) -> str:
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


class DuckDuckGoParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._capture_title = False
        self._capture_snippet = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key: value or "" for key, value in attrs}
        classes = attributes.get("class", "")
        if tag == "a" and "result__a" in classes:
            self._current = {"title": "", "url": attributes.get("href", ""), "snippet": ""}
            self._capture_title = True
        elif self._current is not None and tag in {"a", "div", "span"} and (
            "result__snippet" in classes or "result__body" in classes
        ):
            self._capture_snippet = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._capture_title:
            self._capture_title = False
            if self._current is not None:
                self.results.append(self._current)
        if tag in {"div", "span", "a"}:
            self._capture_snippet = False

    def handle_data(self, data: str) -> None:
        if self._current is None:
            return
        if self._capture_title:
            self._current["title"] += data
        elif self._capture_snippet:
            self._current["snippet"] += data


def fetch_page_text(url: str, *, timeout: float, max_chars: int = 6000) -> WebPageText:
    request = SearchRequest(
        query=url,
        max_results=1,
        region=None,
        time_filter=None,
        timeout=timeout,
    )
    body, final_url, content_type = _http_request(url, request)
    title = ""
    text = body
    if "html" in content_type.lower() or _looks_like_html(body):
        parser = PageTextParser()
        parser.feed(body)
        title = _compact_text(parser.title, 240)
        text = parser.text()
    return WebPageText(
        url=final_url,
        title=title,
        text=_compact_text(text, max_chars),
    )


def _looks_like_html(value: str) -> bool:
    return bool(re.search(r"<\s*(html|body|main|article|p|div|title)\b", value, re.IGNORECASE))


class PageTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._title_depth = 0
        self._skip_depth = 0
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1
            return
        if tag == "title":
            self._title_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self._skip_depth:
            self._skip_depth -= 1
        if tag == "title" and self._title_depth:
            self._title_depth -= 1
        if tag in {"p", "div", "section", "article", "br", "li", "h1", "h2", "h3"}:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._title_depth:
            self.title += data
            return
        text = data.strip()
        if text:
            self._chunks.append(text)

    def text(self) -> str:
        return _compact_text(" ".join(self._chunks), 20_000)
