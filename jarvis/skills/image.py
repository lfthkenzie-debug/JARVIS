"""Image-generation skills — turn a spoken request into a picture, saved and opened.

The actual rendering is provider-agnostic (jarvis/core/imagegen.py). This module
just understands the request: pull the subject out of the command words, decide a
portrait/square/landscape aspect, count how many variations, and phrase the
reply. Like the data-chart skills, the finished PNG lands in jarvis_data/images
and is opened with the OS viewer via reveal()."""
from __future__ import annotations

import re
from pathlib import Path

from jarvis.config import IMAGES_DIR, log, reveal
from jarvis.core import imagegen
from jarvis.core.registry import command

# Shared vocabulary, used by BOTH the routing patterns and the request parser so
# they can never disagree about what counts as an image request. Plural-aware
# nouns matter: a bare 'wallpaper\b' would never match 'wallpapers'.
_VERB = (r"(?:generate|create|make|draw|paint|render|produce|design|sketch|imagine"
         r"|envision|whip\s+up|cook\s+up)")
_NOUN = (r"(?:images?|pictures?|photos?|photographs?|artworks?|arts?|illustrations?"
         r"|drawings?|graphics?|posters?|logos?|wallpapers?|portraits?|renderings?|sketches?)")
_COUNT = r"(?:\d{1,2}|one|two|three|four|five|six|several|a\s+few|couple\s+of)"
_ASPECTW = (r"(?:wide|tall|landscape|portrait|vertical|horizontal|panoramic|widescreen|square)")
_CON = r"(?:of|showing|depicting|featuring|displaying|that\s+(?:shows|is|depicts))"

# Leading 'do the thing' word + an optional [article][count][aspect] image-noun
# [connector]. The whole descriptor block is optional and is only consumed when an
# image-noun is actually present, so 'draw a fox' keeps its subject while 'create
# three wide wallpapers of X' sheds the count and aspect words that belong to the
# command, not the picture. Groups backtrack, so 'a portrait of Y' resolves
# 'portrait' as the noun (not the aspect) when no noun follows it.
_LEAD = re.compile(
    rf"^\W*(?:hey\s+)?(?:jarvis[,\s]+)?(?:please\s+)?(?:can\s+you\s+|could\s+you\s+)?"
    rf"{_VERB}\b[\s,]*(?:me[\s,]+)?"
    rf"(?:(?:an?\s+|the\s+|some\s+|any\s+)?(?:{_COUNT}\b[\s,]+)?(?:{_ASPECTW}\b[\s,]+)?"
    rf"{_NOUN}\b[\s,]*{_CON}?[\s,]*)?",
    re.I)

_FILLER = re.compile(r"\b(?:please|for me|right now|cheers|thanks|jarvis)\b", re.I)


def _clean_prompt(utterance: str) -> str:
    p = _LEAD.sub("", utterance or "", count=1)
    p = _FILLER.sub("", p)
    return p.strip(" ?.,!'\"]").strip()


def _aspect_from(text: str) -> str:
    t = (text or "").lower()
    if re.search(r"\b(?:wide|landscape|panoramic|horizontal|widescreen|16\s*(?:[:/x×]|by)\s*9)\b", t):
        return "wide"
    if re.search(r"\b(?:tall|portrait|vertical|9\s*(?:[:/x×]|by)\s*16|story|phone)\b", t):
        return "tall"
    m = re.search(r"(\d{3,4})\s*[x×]\s*(\d{3,4})", t)
    if m:
        w, h = int(m.group(1)), int(m.group(2))
        return "wide" if w > h * 1.1 else "tall" if h > w * 1.1 else "square"
    return "square"


def _count_from(text: str) -> int:
    t = (text or "").lower()
    # allow an optional 'more' and an optional aspect word between the number and
    # the image noun, so 'three wide wallpapers' still counts as 3.
    gap = rf"[\s,]+(?:more\s+)?(?:{_ASPECTW}\b[\s,]+)?"
    m = re.search(rf"\b(\d{{1,2}})\b{gap}{_NOUN}\b", t)
    if m:
        return max(1, min(int(m.group(1)), 4))
    for word, val in (("a few", 3), ("couple of", 2), ("several", 3), ("one", 1),
                      ("two", 2), ("three", 3), ("four", 4), ("five", 5)):
        if re.search(rf"\b{word}\b{gap}{_NOUN}\b", t):
            return val
    return 1


@command("image.generate", "Generate an image from a text prompt and open it.",
         patterns=[
             rf"\b{_VERB}\b[^.]{{0,32}}{_NOUN}\b",
             rf"\b{_NOUN}\b[^.]{{0,16}}\bof\b",
             r"\btext[\s-]?to[\s-]?image\b",
             rf"\b(?:imagine|envision)\b[^.]{{0,10}}\b(?:a|an|the|scene|world|place)\b",
             r"\b(?:draw|sketch|paint)\b\s+(?:me\s+)?(?:a|an|the|some|up\s+a)\b",
             r"\b(?:photorealistic|hyper[\s-]?realistic|in the style of|oil painting"
             r"|watercolor|concept art|digital art|anime style|3d render)\b",
             # a render verb + an explicit aspect ratio or pixel size is an image
             # request even without an image-noun ('render a 16x9 scene'); gated to
             # real ratios so 'draw a 4x4 matrix' / 'make a 3x3 grid' stay put.
             rf"\b(?:generate|create|make|render|draw|paint|produce|design)\b[^.]{{0,20}}"
             rf"\b(?:\d{{3,4}}\s*[:x×]\s*\d{{3,4}}|16\s*[:/x×]\s*9|9\s*[:/x×]\s*16"
             rf"|4\s*[:/x×]\s*3|3\s*[:/x×]\s*2|21\s*[:/x×]\s*9)\b",
         ],
         examples=["generate an image of a sunset over the ocean", "draw a futuristic city",
                   "make a picture of a cat wearing sunglasses", "create a logo for a coffee shop",
                   "paint a watercolor of the mountains", "imagine a dragon on a mountain",
                   "text to image of a robot chef", "render a portrait of an old sea captain",
                   "generate two images of a cozy cabin in the snow",
                   "create three wide wallpapers of neon city streets at night",
                   "make a tall portrait of an old sea captain",
                   "design a minimalist mountain logo",
                   "render a 16x9 scene of a cyberpunk market"],
         priority=0)
def image_generate(utterance: str) -> str:
    prompt = _clean_prompt(utterance)
    if len(prompt) < 2:
        return ("Of what, sir? Try something like 'generate an image of a red fox "
                "in falling snow'.")
    aspect = _aspect_from(utterance)
    n = _count_from(utterance)
    try:
        res = imagegen.generate(prompt, aspect=aspect, n=n)
    except imagegen.NoBackend as exc:
        return str(exc)
    except Exception as exc:
        log(f"image generate: {exc!r}")
        return f"The image service balked, sir — {str(exc)[:180]}"
    paths = res["paths"]
    reveal(paths[0] if len(paths) == 1 else str(IMAGES_DIR))
    names = ", ".join(Path(p).name for p in paths)
    many = f" {len(paths)} images" if len(paths) > 1 else " an image"
    return (f"Made{many} with {res['backend']}: {names}. Saved in "
            f"jarvis_data/images and opening it now, sir.")
