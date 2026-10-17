"""Small HTTP helper — TLS-verified GET with certifi's CA bundle (macOS system
Python ships without usable root certs for urllib), plus a plain-urllib fallback."""
from __future__ import annotations

import json
import ssl
import urllib.request

UA = {"User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                     "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")}


def _context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def get(url: str, timeout: int = 10, headers: dict | None = None) -> str:
    req = urllib.request.Request(url, headers=headers or UA)
    with urllib.request.urlopen(req, timeout=timeout, context=_context()) as r:
        return r.read().decode("utf-8", "ignore")


def get_json(url: str, timeout: int = 10) -> dict:
    return json.loads(get(url, timeout))


def post_json(url: str, payload: dict, timeout: int = 60,
              headers: dict | None = None) -> dict:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json",
                                          **(headers or UA)})
    with urllib.request.urlopen(req, timeout=timeout, context=_context()) as r:
        return json.loads(r.read())


def post_bytes(url: str, payload: dict, timeout: int = 60,
               headers: dict | None = None) -> bytes:
    """POST JSON, read binary response (e.g. audio streams from TTS APIs)."""
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json",
                                          **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout, context=_context()) as r:
        return r.read()
