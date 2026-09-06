"""Bounded webpage reading tool for Agent-side configuration workflows."""

from __future__ import annotations

import ipaddress
import json
import logging
import socket
import threading
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any

from agent.errors import AgentRunTimeout
from config.product_identity import PRODUCT_VERSION, SERVICE_ID


_MAX_READ_BYTES = 768_000
log = logging.getLogger(__name__)


class _PageTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False
        self._skip_depth = 0
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lower = tag.lower()
        if lower == "title":
            self._in_title = True
        if lower in {"script", "style", "noscript", "svg", "canvas"}:
            self._skip_depth += 1
        if lower in {"p", "div", "section", "article", "br", "li", "tr", "h1", "h2", "h3"}:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        lower = tag.lower()
        if lower == "title":
            self._in_title = False
        if lower in {"script", "style", "noscript", "svg", "canvas"} and self._skip_depth:
            self._skip_depth -= 1
        if lower in {"p", "div", "section", "article", "li", "tr", "h1", "h2", "h3"}:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        text = " ".join(str(data or "").split())
        if not text:
            return
        if self._in_title:
            self.title = (self.title + " " + text).strip()
        if not self._skip_depth:
            self._chunks.append(text)
            self._chunks.append(" ")

    def text(self) -> str:
        lines: list[str] = []
        for raw in "".join(self._chunks).splitlines():
            line = " ".join(raw.split())
            if line:
                lines.append(line)
        return "\n".join(lines)


def _run_bounded_fetch(
    operation,
    *,
    timeout: float | None,
    abort_check=None,
):
    """Run a blocking HTTP read while allowing the owner to close its response."""
    if timeout is None and abort_check is None:
        return operation(lambda _response: None)

    state_lock = threading.Lock()
    active_response = [None]
    result: dict[str, Any] = {}

    def set_active(response) -> None:
        with state_lock:
            active_response[0] = response

    def clear_active() -> None:
        with state_lock:
            active_response[0] = None

    def close_active() -> None:
        with state_lock:
            response = active_response[0]
        close = getattr(response, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                log.debug("[web] response close during cancellation failed", exc_info=True)

    def worker() -> None:
        try:
            result["value"] = operation(set_active)
        except BaseException as exc:
            result["error"] = exc
        finally:
            clear_active()

    thread = threading.Thread(target=worker, name="pfs-web-fetch", daemon=True)
    thread.start()
    deadline = None if timeout is None else time.monotonic() + max(0.0, float(timeout))

    def stop_fetch() -> None:
        close_active()
        thread.join(timeout=1.0)
        if thread.is_alive():
            log.warning("[web] fetch worker did not stop after response close")

    while thread.is_alive():
        try:
            if abort_check is not None:
                abort_check()
        except BaseException:
            stop_fetch()
            raise
        if deadline is not None and time.monotonic() >= deadline:
            stop_fetch()
            raise AgentRunTimeout
        wait = 0.05
        if deadline is not None:
            wait = min(wait, max(0.001, deadline - time.monotonic()))
        thread.join(timeout=wait)

    error = result.get("error")
    if error is not None:
        raise error
    if abort_check is not None:
        abort_check()
    return result.get("value")


def browse_webpage(
    url: str,
    *,
    max_chars: int = 12000,
    timeout: float = 20,
    abort_check=None,
) -> str:
    """Fetch an HTTP(S) page and return bounded readable text."""
    if abort_check is not None:
        abort_check()
    clean_url = _validate_url(url)
    max_chars = max(1000, min(int(max_chars or 12000), 30000))
    request = urllib.request.Request(
        clean_url,
        headers={
            "User-Agent": f"{SERVICE_ID}/{PRODUCT_VERSION}",
            "Accept": "text/html,application/json,text/plain,*/*;q=0.8",
        },
    )
    request_timeout = max(0.001, min(float(timeout or 20), 60.0))
    def fetch_page(set_active):
        with urllib.request.urlopen(request, timeout=request_timeout) as response:
            set_active(response)
            content_type = str(response.headers.get("Content-Type") or "")
            raw = response.read(_MAX_READ_BYTES + 1)
            if len(raw) > _MAX_READ_BYTES:
                raw = raw[:_MAX_READ_BYTES]
            charset = response.headers.get_content_charset() or "utf-8"
            text = raw.decode(charset, errors="replace")
            status = getattr(response, "status", 200)
        return content_type, text, status

    content_type, text, status = _run_bounded_fetch(
        fetch_page,
        timeout=request_timeout,
        abort_check=abort_check,
    )

    if "json" in content_type.lower():
        body = _format_json_text(text)
        title = "JSON response"
    elif "html" in content_type.lower() or "<html" in text[:500].lower():
        parser = _PageTextExtractor()
        parser.feed(text)
        title = parser.title or clean_url
        body = parser.text()
    else:
        title = clean_url
        body = text
    body = body.strip()
    if len(body) > max_chars:
        body = body[:max_chars].rstrip() + "\n...[truncated]"
    return "\n".join([
        f"URL: {clean_url}",
        f"HTTP status: {status}",
        f"Content-Type: {content_type or 'unknown'}",
        f"Title: {title}",
        "",
        body or "(no readable text extracted)",
    ])


def _format_json_text(text: str) -> str:
    try:
        parsed: Any = json.loads(text)
    except Exception:
        return text
    return json.dumps(parsed, ensure_ascii=False, indent=2)


def _validate_url(url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        raise ValueError("url is required")
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("only absolute http/https URLs are supported")
    host = parsed.hostname or ""
    if not host:
        raise ValueError("URL host is required")
    _reject_private_host(host)
    return urllib.parse.urlunparse(parsed)


def _reject_private_host(host: str) -> None:
    lowered = host.lower().strip(".")
    if lowered in {"localhost", "127.0.0.1", "::1"} or lowered.endswith(".localhost"):
        raise ValueError("local/private network URLs are not allowed")
    addresses: set[str] = set()
    try:
        for item in socket.getaddrinfo(host, None):
            addresses.add(item[4][0])
    except socket.gaierror as exc:
        raise ValueError(f"cannot resolve URL host: {host}") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise ValueError("local/private network URLs are not allowed")
