"""Image generation — text to a PNG file, from whichever provider is configured.

JARVIS is provider-agnostic here: `resolve()` picks the first backend whose key
is present (OpenAI → Stability → Replicate → a local Stable-Diffusion model if
`diffusers` is installed), and `generate()` returns the saved PNG path. Every
provider is reduced to a pure request-body builder + a thin network call, so the
payloads are testable without spending any credits. A deterministic `test`
backend (pure Pillow) renders a real image from the prompt so the whole
command→save→reveal pipeline can be exercised with no key and no network.

Keys are read in config.py from env or a file in jarvis_data/ (see there).
Nothing is uploaded except the prompt text to the provider you enable."""
from __future__ import annotations

import base64
import io
import re
import time
from datetime import datetime

from jarvis.config import IMAGES_DIR, OPENAI_API_KEY, OPENAI_IMAGE_MODEL
from jarvis.config import STABILITY_API_KEY, REPLICATE_API_TOKEN
from jarvis.config import IMAGE_BACKEND, IMAGE_LOCAL_MODEL, log
from jarvis.core import net


class NoBackend(Exception):
    """Raised when no usable image provider is configured."""


# ------------------------------------------------------------------ availability
def _local_available() -> bool:
    try:
        import diffusers  # noqa: F401
        import torch       # noqa: F401
        return True
    except Exception:
        return False


def _has(name: str) -> bool:
    return {"openai": bool(OPENAI_API_KEY),
            "stability": bool(STABILITY_API_KEY),
            "replicate": bool(REPLICATE_API_TOKEN),
            "local": _local_available(),
            "test": True}[name]


AUTO_ORDER = ["openai", "stability", "replicate", "local"]


def _howto(reason: str) -> str:
    return (f"{reason} To give me a brush, drop a key in jarvis_data/ or set an env "
            "var: openai_api_key.txt (JARVIS_OPENAI_KEY) is best at following detailed "
            "prompts, or stability_api_key.txt / replicate_token.txt, or install "
            "diffusers for a local model — then say 'generate an image of ...'.")


def resolve() -> str:
    """Pick the active backend name, honouring JARVIS_IMAGE_BACKEND if set."""
    if IMAGE_BACKEND:
        if IMAGE_BACKEND == "test":
            return "test"
        if IMAGE_BACKEND in _PROVIDERS and _has(IMAGE_BACKEND):
            return IMAGE_BACKEND
        raise NoBackend(_howto(f"The '{IMAGE_BACKEND}' backend isn't ready (no key?)"))
    for name in AUTO_ORDER:
        if _has(name):
            return name
    raise NoBackend(_howto("No image provider is configured, sir."))


# ------------------------------------------------------------------ size mapping
# aspect ('square'|'wide'|'tall') → each provider's own size vocabulary.
_OPENAI_SIZE = {"square": "1024x1024", "wide": "1536x1024", "tall": "1024x1536"}
_DALLE_SIZE = {"square": "1024x1024", "wide": "1792x1024", "tall": "1024x1792"}
_STAB_AR = {"square": "1:1", "wide": "16:9", "tall": "9:16"}
_REP_AR = {"square": "1:1", "wide": "16:9", "tall": "9:16"}
_LOCAL_PX = {"square": (512, 512), "wide": (768, 512), "tall": (512, 768)}
_TEST_PX = {"square": (1024, 1024), "wide": (1536, 1024), "tall": (1024, 1536)}


# ------------------------------------------------------------------ request builders
def openai_payload(prompt: str, aspect: str, model: str = OPENAI_IMAGE_MODEL) -> dict:
    if model.startswith("dall-e"):
        return {"model": model, "prompt": prompt, "size": _DALLE_SIZE[aspect],
                "n": 1, "response_format": "b64_json"}
    return {"model": model, "prompt": prompt, "size": _OPENAI_SIZE.get(aspect, "1024x1024"),
            "n": 1}


def stability_fields(prompt: str, aspect: str) -> dict:
    return {"prompt": prompt, "output_format": "png",
            "aspect_ratio": _STAB_AR.get(aspect, "1:1")}


def replicate_payload(prompt: str, aspect: str, salt: int = 0) -> dict:
    return {"input": {"prompt": prompt, "aspect_ratio": _REP_AR.get(aspect, "1:1"),
                      "output_format": "png", "seed": (salt * 7919) % 4294967295}}


# ------------------------------------------------------------------ providers
def _openai(prompt: str, aspect: str, salt: int = 0) -> bytes:
    resp = net.post_json("https://api.openai.com/v1/images/generations",
                         openai_payload(prompt, aspect),
                         headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                         timeout=180)
    item = resp["data"][0]
    if item.get("b64_json"):
        return base64.b64decode(item["b64_json"])
    return net.get_bytes(item["url"])


def _stability(prompt: str, aspect: str, salt: int = 0) -> bytes:
    return net.post_multipart(
        "https://api.stability.ai/v2beta/stable-image/generate/core",
        stability_fields(prompt, aspect),
        headers={"Authorization": f"Bearer {STABILITY_API_KEY}", "Accept": "image/png"},
        timeout=180)


def _replicate(prompt: str, aspect: str, salt: int = 0) -> bytes:
    auth = {"Authorization": f"Token {REPLICATE_API_TOKEN}"}
    url = "https://api.replicate.com/v1/models/black-forest-labs/flux-dev/predictions"
    resp = net.post_json(url, replicate_payload(prompt, aspect, salt),
                         headers={**auth, "Prefer": "wait=30"}, timeout=120)
    import json
    if resp.get("status") not in ("succeeded",):
        poll = resp.get("urls", {}).get("get")
        for _ in range(30):                     # up to ~60s of polling
            time.sleep(2)
            resp = json.loads(net.get(poll, headers=auth))
            if resp.get("status") == "succeeded":
                break
            if resp.get("status") in ("failed", "canceled"):
                raise RuntimeError(f"Replicate said: {resp.get('error', 'failed')}")
    out = resp.get("output")
    img_url = out if isinstance(out, str) else out[0]
    return net.get_bytes(img_url)


_LOCAL_PIPE = None


def _local(prompt: str, aspect: str, salt: int = 0) -> bytes:
    global _LOCAL_PIPE
    import torch
    if _LOCAL_PIPE is None:
        from diffusers import StableDiffusionPipeline
        device = "mps" if torch.backends.mps.is_available() else "cpu"
        _LOCAL_PIPE = StableDiffusionPipeline.from_pretrained(
            IMAGE_LOCAL_MODEL, torch_dtype=torch.float16 if device == "mps" else torch.float32
        ).to(device)
    w, h = _LOCAL_PX.get(aspect, (512, 512))
    img = _LOCAL_PIPE(prompt, height=h, width=w,
                      generator=torch.manual_seed(salt)).images[0]
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _test(prompt: str, aspect: str, salt: int = 0) -> bytes:
    """Offline, deterministic stand-in: a real PNG whose colours derive from the
    prompt hash, so the pipeline can be proven with no key and no network."""
    import hashlib
    from PIL import Image, ImageDraw
    w, h = _TEST_PX.get(aspect, (1024, 1024))
    seed = int(hashlib.sha256(f"{prompt}{salt}".encode()).hexdigest()[:8], 16)
    img = Image.new("RGB", (w, h))
    px = img.load()
    base = [(seed >> (i * 3)) % 256 for i in range(3)]
    for y in range(h):                         # vertical gradient
        for x in range(0, w, 4):               # step 4px columns for speed
            t = (x / w + y / h) / 2
            col = tuple(int(c * (1 - t) + ((c * 3 + seed) % 256) * t) % 256 for c in base)
            for dx in range(4):
                if x + dx < w:
                    px[x + dx, y] = col
    d = ImageDraw.Draw(img)
    for i in range(6):                         # seeded shapes echo the prompt
        sx = (seed >> (i * 2)) % w
        sy = (seed >> (i * 3)) % h
        r = 40 + (seed >> i) % 160
        d.ellipse([sx - r, sy - r, sx + r, sy + r], outline=(255, 255, 255, 220), width=3)
    d.rectangle([0, h - 150, w, h], fill=(10, 12, 20))
    d.multiline_text((32, h - 140), "JARVIS (test render)\n" + prompt[:70],
                     fill=(235, 235, 235), spacing=6)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


_PROVIDERS = {"openai": _openai, "stability": _stability,
              "replicate": _replicate, "local": _local, "test": _test}


# ------------------------------------------------------------------ output
def _slug(prompt: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", prompt.lower()).strip("-")[:40] or "image"


def _save(data: bytes, prompt: str, salt: int = 0) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = IMAGES_DIR / f"{ts}_{_slug(prompt)}{'_' + str(salt) if salt else ''}.png"
    out.write_bytes(data)
    return str(out)


def generate(prompt: str, aspect: str = "square", n: int = 1) -> dict:
    """Render n images; returns {'backend', 'paths': [...]}. Raises NoBackend if
    nothing is configured, RuntimeError if the provider call itself fails."""
    prompt = (prompt or "").strip()
    if not prompt:
        raise ValueError("empty prompt")
    name = resolve()
    fn = _PROVIDERS[name]
    n = max(1, min(int(n), 4))
    paths = []
    for i in range(n):
        data = fn(prompt, aspect, i)
        paths.append(_save(data, prompt, i))
    log(f"imagegen[{name}] → {len(paths)} image(s) for {prompt[:50]!r}")
    return {"backend": name, "paths": paths}
