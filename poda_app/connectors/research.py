"""Public-web evidence tools. Connections never carry a browser login."""
from __future__ import annotations

import http.client
import ipaddress
import io
import re
import socket
import ssl
import time
from html.parser import HTMLParser
from urllib.parse import quote, urlencode, urljoin, urlsplit

from ..runtime.db import get_setting
from ..security import egress, keychain

SEARCH_SERVICE = "PODA.Public.Search"
SEARCH_ACCOUNT = "brave"
MAX_BYTES = 2_000_000


def status():
    return {"enabled": get_setting("public_web") == "true", "search": get_setting("brave_search") == "true"}


def _ok(body, verification):
    return {"ok": True, "result": body, "verification": verification}


def search(query, limit=5):
    state = status()
    if not state["enabled"] or not state["search"]:
        raise PermissionError("Configure public web search in Files & access.")
    key = keychain.keychain_get(SEARCH_SERVICE, SEARCH_ACCOUNT)
    if not key:
        raise PermissionError("Reconnect the saved search API key.")
    response = egress.guarded_request("brave_search", "GET",
        "https://api.search.brave.com/res/v1/web/search?" + urlencode({"q": query, "count": limit}),
        headers={"Accept": "application/json", "X-Subscription-Token": key}, timeout=12,
        purpose="User-requested public web search")
    if not response.ok:
        raise RuntimeError(f"The search provider returned HTTP {response.status_code}.")
    items = response.json().get("web", {}).get("results") or []
    return _ok({"query": query, "results": [{"title": e.get("title", "")[:180], "url": e.get("url", ""),
                                             "snippet": e.get("description", "")[:370]} for e in items[:limit]]},
               "Read results from the configured search provider. Open sources to verify what they say.")


class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.title = ""
        self.in_title = False
        self.ignore = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "svg"}:
            self.ignore += 1
        if tag == "title":
            self.in_title = True

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "svg"}:
            self.ignore = max(0, self.ignore - 1)
        if tag == "title":
            self.in_title = False

    def handle_data(self, text):
        if self.ignore:
            return
        value = " ".join(text.split())
        if not value:
            return
        if self.in_title:
            self.title += value
        if len(self.parts) < 12000:
            self.parts.append(value)


def _address(host):
    ips = [item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)]
    addresses = [ipaddress.ip_address(ip) for ip in ips]
    if not addresses or any(not ip.is_global or ip.is_multicast or ip.is_reserved for ip in addresses):
        raise ValueError("The URL must resolve exclusively to public internet addresses.")
    return ips[0]


def _download(url):
    current = url
    deadline = time.monotonic() + 18
    for _ in range(4):
        u = urlsplit(current)
        if (u.scheme != "https" or not u.hostname or u.port not in {None, 443}
                or u.username or u.password or len(current) > 3000 or len(u.hostname) > 253):
            raise ValueError("Use a public HTTPS URL without login information or a custom port.")
        host = u.hostname.encode("idna").decode("ascii")
        if not re.fullmatch(r"[A-Za-z0-9.-]+", host):
            raise ValueError("Use a public hostname.")
        address = _address(host)
        timeout = min(8, deadline - time.monotonic())
        if timeout <= 0:
            raise TimeoutError("The page read timed out.")
        connection = http.client.HTTPSConnection(host, timeout=timeout, context=ssl.create_default_context())
        # TLS still uses the original hostname for certificate validation and SNI.
        # The socket connects to the validated address; it never resolves it again.
        connection._create_connection = lambda addr, timeout, source_address: socket.create_connection(
            (address, addr[1]), timeout, source_address)
        try:
            path = quote(u.path or "/", safe="/%:@!$&'()*+,;=-._~")
            query = quote(u.query, safe="%=&/?+:@!$'()*,-._~;")
            connection.request("GET", path + ("?" + query if query else ""), headers={
                "User-Agent": "PODA Public Web Reader", "Accept": "text/html, text/plain, application/pdf",
                "Accept-Encoding": "identity",
            })
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                if not location:
                    raise ValueError("The server returned a redirect without a destination.")
                current = urljoin(current, location)
                continue
            if response.status != 200:
                raise RuntimeError(f"The public page returned HTTP {response.status}.")
            kind = (response.getheader("Content-Type") or "").split(";", 1)[0].strip().lower()
            if kind not in {"text/html", "application/xhtml+xml", "text/plain", "application/pdf"}:
                raise ValueError("Only public HTML, plain text, and PDF sources are supported.")
            blocks = []
            size = 0
            while time.monotonic() < deadline:
                block = response.read1(min(65536, MAX_BYTES + 1 - size))
                if not block:
                    return current, kind, b"".join(blocks)
                blocks.append(block)
                size += len(block)
                if size > MAX_BYTES:
                    raise ValueError("The public source exceeds the reader's 2 MB limit.")
            raise TimeoutError("The page read timed out.")
        finally:
            connection.close()
    raise ValueError("The source has too many redirects.")


def read(url, topic=""):
    if not status()["enabled"] or egress.offline_mode():
        raise PermissionError("Enable public web reading in Files & access and turn off offline mode.")
    started = time.perf_counter()
    fetched, kind, raw = _download(url)
    egress._log("public_web", "GET", urlsplit(fetched).hostname, "/",
                200, 0, len(raw), "Read a public source", int((time.perf_counter()-started)*1000), False)
    title = ""
    if kind == "application/pdf":
        from pypdf import PdfReader
        pdf = PdfReader(io.BytesIO(raw))
        text = "\n".join(f"Page {i+1}: {page.extract_text()}" for i, page in enumerate(pdf.pages[:20]))
        title = str((pdf.metadata or {}).get("/Title", ""))[:220]
    elif kind in {"text/html", "application/xhtml+xml"}:
        page = _Page()
        page.feed(raw.decode("utf-8", errors="replace"))
        title = page.title[:220]
        text = "\n".join(page.parts)
    else:
        text = raw.decode("utf-8", errors="replace")
    if not text.strip():
        raise ValueError("The source did not contain extractable text.")
    words = set(re.findall(r"[a-z0-9]{4,}", topic.lower()))
    passages = re.findall(r"[\s\S]{1,900}", text)
    if words:
        passages = sorted(passages, key=lambda line: -len(words.intersection(re.findall(r"[a-z0-9]{4,}", line.lower()))))
    return _ok({"url": fetched, "title": title, "excerpt": "\n\n".join(passages[:4]),
                "truncated": len(passages) > 4}, "Retrieved and extracted text from the public source.")
