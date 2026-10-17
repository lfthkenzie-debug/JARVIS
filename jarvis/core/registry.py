"""Command registry + decorators. Skills register themselves here."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional


@dataclass
class Command:
    name: str                       # skill id, e.g. "files.open"
    help: str                       # one-line description
    handler: Callable[[str], str]   # takes the utterance, returns the spoken reply
    patterns: List[str] = field(default_factory=list)    # regex triggers
    examples: List[str] = field(default_factory=list)    # training utterances for intent models
    priority: int = 0               # lower = consulted first when several skills match


_REGISTRY: Dict[str, Command] = {}


def command(name: str, help: str, patterns: Optional[List[str]] = None,
            examples: Optional[List[str]] = None, priority: int = 0):
    """Decorator: register a function as a JARVIS skill."""
    def deco(fn: Callable[[str], str]) -> Callable[[str], str]:
        _REGISTRY[name] = Command(
            name=name, help=help, handler=fn,
            patterns=patterns or [], examples=examples or [], priority=priority)
        return fn
    return deco


def all_commands() -> Dict[str, Command]:
    return _REGISTRY


def ordered_commands() -> List[Command]:
    """Skills sorted by priority — the router consults them in this order."""
    return sorted(_REGISTRY.values(), key=lambda c: (c.priority, c.name))


def get(name: str) -> Optional[Command]:
    return _REGISTRY.get(name)
