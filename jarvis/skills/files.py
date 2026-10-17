"""Full file-system access — find, open, inspect, organize, summarize, trash.
Confined to the user's own folders (see config.FILE_ROOTS) for safety."""
from __future__ import annotations

import os
import re
import subprocess
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

from jarvis.config import FILE_ROOTS, HOME, log, reveal
from jarvis.core.registry import command

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".heic", ".webp", ".tiff"}
DOC_EXT = {".pdf", ".doc", ".docx", ".txt", ".md", ".rtf", ".pages"}
CODE_EXT = {".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".swift",
            ".go", ".rs", ".java", ".c", ".cpp", ".sh", ".sql"}
MEDIA_EXT = {".mp3", ".wav", ".m4a", ".mp4", ".mov", ".avi", ".mkv"}
ARCHIVE_EXT = {".zip", ".rar", ".7z", ".tar", ".gz", ".dmg", ".pkg"}


def _resolve_folder(word: str) -> Path:
    for key, path in FILE_ROOTS.items():
        if key in word or word.strip() in ("folder", "downloads"):
            return path
    return HOME


def _human(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def _safe_roots() -> list[Path]:
    return [p for p in FILE_ROOTS.values() if p.exists()]


# never descend into these when searching the whole home folder
_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "env", "__pycache__",
              ".Trash", "Library", ".cache", ".npm", ".gradle", "build", "dist",
              ".next", ".pytest_cache", "site-packages", ".sdkman", ".docker"}


def _walk_files(roots: list[Path], limit: int = 20000):
    """Yield files under roots, pruning dependency/system directories."""
    counted = 0
    for root in roots:
        stack = [root]
        while stack and counted < limit:
            cur = stack.pop()
            try:
                entries = list(cur.iterdir())
            except (PermissionError, OSError):
                continue
            for e in entries:
                if e.is_dir(follow_symlinks=False):
                    if e.name not in _SKIP_DIRS and not e.name.endswith(".app"):
                        stack.append(e)
                elif e.is_file():
                    counted += 1
                    yield e
                    if counted >= limit:
                        return


# ------------------------------------------------------------------ find
@command("files.find", "Find files by name anywhere in your folders.",
         patterns=[r"\b(?:find|locate|search for)\b.*\b(?:file|files|pdf|csv|png|jpg|photo|python|code|excel|markdown|document)\b",
                   r"\bwhere is\b", r"\blocate\b"],
         examples=["find the invoice pdf", "locate report.csv", "where is my vacation photo",
                   "find all python files", "search for budget xlsx in downloads"])
def find_files(utterance: str) -> str:
    m = re.search(r"(?:find|locate|search for|where is)\s+(?:the\s+|a\s+|my\s+|all\s+)?(?:\w+\s+)*?"
                  r"([\w-]+\.\w{2,5})", utterance)
    ext_m = re.search(r"(python|pdf|image|photo|video|audio|excel|csv|code|word|markdown) "
                      r"(?:files?|s)?", utterance)
    folder = _resolve_folder(utterance)

    if m:
        target = m.group(1).lower()
        results = []
        for p in _walk_files(_safe_roots()):
            if target in p.name.lower():
                results.append(p)
                if len(results) >= 20:
                    break
        if not results:
            return f"No file matching {target} found."
        top = results[:5]
        lines = "; ".join(f"{r} ({_human(r.stat().st_size)})" for r in top)
        extra = f" And {len(results) - 5} more." if len(results) > 5 else ""
        return f"Found {len(results)}: {lines}{extra}"

    extmap = {"python": CODE_EXT, "code": CODE_EXT, "pdf": {".pdf"},
              "image": IMAGE_EXT, "photo": IMAGE_EXT, "video": {".mp4", ".mov", ".avi", ".mkv"},
              "audio": {".mp3", ".wav", ".m4a"}, "excel": {".xlsx", ".xls", ".csv"},
              "csv": {".csv"}, "word": {".docx", ".doc"}, "markdown": {".md", ".txt"}}
    if ext_m:
        exts = extmap[ext_m.group(1)]
        base = folder if folder.exists() else HOME
        hits = [p for p in _walk_files([base]) if p.suffix.lower() in exts]
        if not hits:
            return f"No {ext_m.group(1)} files in {base}."
        newest = max(hits, key=lambda p: p.stat().st_mtime)
        return (f"{len(hits)} {ext_m.group(1)} files in {base}. "
                f"Newest: {newest.name}, {datetime.fromtimestamp(newest.stat().st_mtime):%b %d}.")
    return "What should I look for, sir?"


# ------------------------------------------------------------------ open
@command("files.open", "Open a file or folder in the default app / file manager.",
         patterns=[r"\bopen (?:the )?(?:file|folder|downloads|documents|directory)\b"],
         examples=["open downloads folder", "open the last pdf", "open the file invoice.pdf",
                   "show me my desktop folder"],
         priority=5)
def open_thing(utterance: str) -> str:
    if any(k in utterance for k in FILE_ROOTS):
        folder = _resolve_folder(utterance)
        reveal(folder)
        return f"Opening {folder.name}."
    if "last" in utterance or "recent" in utterance:
        base = _resolve_folder(utterance)
        files = sorted(_walk_files([base]),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            return "Nothing recent found."
        newest = files[0]
        reveal(newest)
        return f"Opening {newest.name}."
    m = re.search(r"([\w.-]+\.\w{2,5})", utterance)
    if m:
        hit = next(_search(m.group(1)), None)
        if hit:
            reveal(hit)
            return f"Opening {hit.name}."
        return f"I couldn't find {m.group(1)}."
    return "Which file, sir?"


def _search(name: str, roots: Optional[list[Path]] = None) -> Iterator[Path]:
    """Yield files whose name contains `name` (case-insensitive), pruned walk."""
    name = name.lower()
    for p in _walk_files(roots or _safe_roots()):
        if name in p.name.lower():
            yield p


# ------------------------------------------------------------------ folder summary
@command("files.summarize", "Summarize a folder: counts, sizes, biggest files.",
         patterns=[r"summar(?:ize|e)\s+(?:my\s+|the\s+)?(downloads|documents|desktop|pictures|music|movies|projects|home|folder)",
                   r"how many files", r"whats in (?:my |the )?\w+",
                   r"folder (?:report|stats)", r"taking (?:up )?space"],
         examples=["summarize my downloads", "how many files on my desktop",
                   "whats in documents", "what is taking space in downloads", "folder stats"])
def summarize_folder(utterance: str) -> str:
    folder = _resolve_folder(utterance)
    if not folder.exists():
        return f"{folder} doesn't exist."
    total, sizes, cats = 0, 0, Counter()
    biggest: tuple[int, Path] = (0, folder)
    for p in _walk_files([folder]):
        try:
            sz = p.stat().st_size
        except OSError:
            continue
        total += 1
        sizes += sz
        cats[_category(p)] += 1
        if sz > biggest[0]:
            biggest = (sz, p)
    if total == 0:
        return f"{folder} is empty."
    top = ", ".join(f"{k}: {v}" for k, v in cats.most_common(4))
    return (f"{folder.name}: {total} files, {_human(sizes)} total. "
            f"Breakdown — {top}. Largest: {biggest[1].name} ({_human(biggest[0])}).")


def _category(p: Path) -> str:
    suf = p.suffix.lower()
    if suf in IMAGE_EXT:
        return "images"
    if suf in DOC_EXT:
        return "documents"
    if suf in CODE_EXT:
        return "code"
    if suf in MEDIA_EXT:
        return "media"
    if suf in ARCHIVE_EXT:
        return "archives"
    return "other"


# ------------------------------------------------------------------ organize downloads
@command("files.organize", "Sort a folder's loose files into typed subfolders.",
         patterns=[r"organize|sort (?:my|the) \w+|clean (?:up )?(?:my |the )?(?:downloads|desktop)"],
         examples=["organize my downloads", "sort the desktop into folders",
                   "clean up downloads by file type"])
def organize(utterance: str) -> str:
    folder = _resolve_folder(utterance)
    if not folder.exists():
        return f"{folder} not found."
    moved = Counter()
    for p in folder.iterdir():
        if not p.is_file() or p.name.startswith("."):
            continue
        cat = _category(p)
        dest = folder / cat
        dest.mkdir(exist_ok=True)
        target = dest / p.name
        if target.exists():
            target = dest / f"{p.stem}_{int(p.stat().st_mtime)}{p.suffix}"
        p.rename(target)
        moved[cat] += 1
    if not moved:
        return f"{folder.name} was already tidy."
    return f"Organized {folder.name}: " + ", ".join(f"{v} {k}" for k, v in moved.items()) + "."


# ------------------------------------------------------------------ trash safely
@command("files.trash", "Move a matched file to the Trash / Recycle Bin.",
         patterns=[r"\btrash|delete (?:this |that |the )?file|remove the file\b"],
         examples=["trash the file report old pdf", "delete that download", "remove invoice draft pdf"])
def trash(utterance: str) -> str:
    m = re.search(r"([\w.-]+\.\w{2,5})", utterance)
    if not m:
        return "Name the file you want in the Trash, sir."
    name = m.group(1).lower()
    for p in _search(name):
        try:
            from send2trash import send2trash  # type: ignore
            send2trash(str(p))
        except Exception:
            subprocess.run(["osascript", "-e",
                            f'tell application "Finder" to delete POSIX file "{p}"'],
                           check=False)
        return f"{p.name} moved to Trash — recoverable if needed."
    return f"No file matching {name}."


# ------------------------------------------------------------------ read text file
@command("files.read", "Read a text file and report its contents briefly.",
         patterns=[r"read (?:the |my )?(?:file|text|note|document)\b"],
         examples=["read the file notes txt", "read my last note", "whats in readme"])
def read_file(utterance: str) -> str:
    m = re.search(r"([\w.-]+\.\w{2,5})", utterance)
    target = None
    if m:
        for root in _safe_roots():
            for p in root.rglob(m.group(1)):
                if p.is_file():
                    target = p
                    break
    else:
        base = _resolve_folder(utterance)
        texts = sorted((p for p in base.rglob("*")
                        if p.is_file() and p.suffix in {".txt", ".md"}),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        target = texts[0] if texts else None
    if target is None:
        return "I couldn't find that file."
    try:
        content = target.read_text(errors="ignore")
    except Exception as exc:
        return f"Couldn't read {target.name}: {exc}"
    if not content.strip():
        return f"{target.name} is empty."
    words = len(content.split())
    preview = " ".join(content.split()[:60])
    return f"{target.name}, {words} words. Opening: {preview}…"
