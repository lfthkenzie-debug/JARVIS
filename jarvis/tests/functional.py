"""Headless functional check — executes skill handlers directly against a sample
dataset. Run: python -m jarvis.tests.functional   (no mic, no AppleScript volume/app calls)"""
from __future__ import annotations

import csv
import random
from datetime import datetime, timedelta
from pathlib import Path

from jarvis.config import DATA_DIR, log
from jarvis.core.brain import Brain
from jarvis.main import _boot

SAMPLE = DATA_DIR / "sample_sales.csv"


def make_sample(rows: int = 180) -> Path:
    """Daily sales series with trend, weekly seasonality, and noise."""
    rng = random.Random(7)
    start = datetime.now() - timedelta(days=rows)
    with SAMPLE.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "units", "price", "region", "discount"])
        for i in range(rows):
            d = start + timedelta(days=i)
            units = 120 + 0.9 * i + (35 if d.weekday() in (5, 6) else 0) + rng.gauss(0, 22)
            price = round(9.9 + 0.01 * i + rng.gauss(0, 0.4), 2)
            w.writerow([d.date(), round(units, 1), price,
                        rng.choice(["APAC", "EMEA", "AMER"]), round(rng.uniform(0, 0.3), 3)])
    return SAMPLE


PHRASES = [
    "check the ml stack",
    "profile the dataset sample_sales.csv",
    "plot units from sample_sales.csv",
    "what is the mean of units in sample_sales.csv",
    "correlation between price and units in sample_sales.csv",
    "forecast the next 21 days for sample_sales.csv",
    "is the series in sample_sales.csv stationary",
    "summarize my downloads",
    "find the file sample_sales.csv",
    "read the file sample_sales.csv",
    "what is the battery level",
    "how much disk space is free",
    "system status report",
    "what time is it",
    "what's the weather in tokyo",
    "who is grace hopper",
    "summarize this text: \"Machine learning is a branch of artificial intelligence "
    "that focuses on building applications that learn from data and improve their "
    "accuracy over time without being reprogrammed. Supervised learning uses labelled "
    "examples, unsupervised learning finds structure, and reinforcement learning "
    "optimises through reward signals.\"",
    "extract entities from \"OpenAI launched GPT-4 in San Francisco on March 14, 2023. "
    "Sam Altman announced it from the Microsoft campus in Redmond, Washington.\"",
    "most used words in sample_sales.csv",
    "backend status for dask spark ray kafka",
    "what did I ask earlier",
    "what can you do",
]


def run() -> int:
    sample = make_sample()
    print(f"Sample dataset: {sample} ({sample.stat().st_size} bytes)\n")
    brain, memory = _boot()
    from jarvis.core.registry import all_commands
    bad = 0
    for phrase in PHRASES:
        cmd, text = brain.route(phrase)
        name = cmd.name if cmd else "UNROUTED"
        if cmd is None:
            print(f"✗ {phrase[:44]:<46} {name:<16} no route")
            bad += 1
            continue
        try:
            reply = cmd.handler(text)
        except Exception as exc:
            reply = f"!! {type(exc).__name__}: {exc}"
            bad += 1
        print(f"• {phrase[:44]:<46} {name:<16} {reply[:150]}")
    print(f"\n{len(all_commands())} skills, {len(PHRASES)} checks, {bad} problem(s).")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(run())
