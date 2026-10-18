"""JARVIS entrypoint — boots the brain, wires speech, runs the loop.

Usage:
  python -m jarvis                 voice mode (say "jarvis" to wake)
  python -m jarvis --voice         Auto-Instant hands-free — no wake word, instant answers
  python -m jarvis --text          keyboard mode, same brain
  python -m jarvis --ui            holographic browser interface (localhost server)
  python -m jarvis --oneshot "open safari"
  python -m jarvis --selftest      route a battery of phrases, no side effects
  python -m jarvis --quiet         suppress spoken output
"""
from __future__ import annotations

import argparse
import sys
import threading

from jarvis.config import ASSISTANT_NAME, EXIT_PHRASES, WAKE_WORDS, log


def _boot():
    import warnings
    warnings.filterwarnings("ignore", category=FutureWarning)
    warnings.filterwarnings("ignore", category=UserWarning,
                            message=".*statsmodels.*")
    warnings.filterwarnings("ignore", message=".*plotly.*")
    import jarvis.skills                      # noqa: F401  (registers all commands)
    from jarvis.core.brain import Brain
    from jarvis.core.memory import Memory
    log("Loading skills and training intent models…")
    brain = Brain()
    return brain, Memory()


def handle(brain, memory, utterance: str, speak) -> str:
    cmd, text = brain.route(utterance)
    if cmd is None:
        reply = "I didn't catch a command in that, sir."
    else:
        try:
            reply = cmd.handler(text)
        except Exception as exc:
            reply = f"{cmd.name} failed: {exc}"
            log(f"Skill error in {cmd.name}: {exc!r}")
    memory.record(utterance, reply, cmd.name if cmd else "")
    speak(reply)
    return reply


def voice_loop(brain, memory, speaker, listener) -> None:
    stop = threading.Event()
    log(f"Listening for wake word: {', '.join(WAKE_WORDS[:2])}. Ctrl-C to quit.")
    speaker.say(f"{ASSISTANT_NAME} online.", block=False)
    heard_ok = 0
    while not stop.is_set():
        try:
            if listener.backend == "none":
                log("No microphone STT available — switch to --text mode.")
                return
            heard = listener.listen(timeout=10, phrase_limit=14)
            if heard is None:
                heard_ok += 1
                if heard_ok >= 3:
                    log("Three silent windows — standing by.")
                    heard_ok = 0
                continue
            heard_ok = 0
            log(f"Heard: {heard}")
            if any(p in heard for p in EXIT_PHRASES):
                speaker.say("Powering down. Goodbye, sir.")
                return
            if not any(w in heard for w in ("jarvis", "jarvi", "jervis")):
                continue        # ignore ambient speech until wake word
            speaker.say("Yes, sir?", block=False)
            order = listener.listen(timeout=8, phrase_limit=15)
            if order:
                log(f"Order: {order}")
                handle(brain, memory, order, lambda t: speaker.say(t, block=False))
            else:
                speaker.say("I didn't catch that.")
        except KeyboardInterrupt:
            return


def handsfree_loop(brain, memory, speaker, listener) -> None:
    """Auto-Instant voice: no wake word, no button — talk and JARVIS answers,
    then listens again immediately. Runs until a farewell phrase or Ctrl-C."""
    log("Auto-Instant voice mode — speak freely, no wake word. Ctrl-C to stop.")
    speaker.say("Auto-Instant mode engaged. Just speak, sir.")
    idle_windows = 0
    try:
        while True:
            order = listener.listen(timeout=12, phrase_limit=16)
            if order is None:
                idle_windows += 1
                if idle_windows == 5:
                    log("Still listening, sir.")
                    idle_windows = 0
                continue
            idle_windows = 0
            order = order.strip()
            if not order:
                continue
            log(f"Heard: {order}")
            if any(p in order for p in EXIT_PHRASES):
                speaker.say("Disengaging hands-free. Goodbye, sir.")
                return
            # blocking speech (speaker.say default): JARVIS finishes replying
            # before listening again, so he can't hear his own voice and self-answer
            handle(brain, memory, order, speaker.say)
    except KeyboardInterrupt:
        log("Auto-Instant voice mode stopped.")


def text_mode(brain, memory, speaker) -> None:
    log("Text mode. Type an order; 'exit' to quit, 'help' for skills.")
    while True:
        try:
            line = input("\x1b[32msir ▸ \x1b[0m").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not line:
            continue
        if line in ("exit", "quit", "goodbye"):
            log("Powering down.")
            return
        handle(brain, memory, line, lambda t: print(f"\x1b[1;34mJARVIS\x1b[0m: {t}"))


SELFTEST_PHRASES = [
    "what time is it",
    "what can you do",
    "set volume to forty",
    "summarize my downloads",
    "organize my downloads",
    "find the file config.py",
    "open safari",
    "what's the weather",
    "who is ada lovelace",
    "forecast sales for the next 30 days",
    "profile the dataset",
    "check the ml stack",
    "summarize this text: \"Artificial intelligence is a wide field of computer "
    "science concerned with building systems that can perform tasks that normally "
    "require human intelligence such as visual perception speech recognition "
    "decision making and translation between languages.\"",
    "extract entities from \"Apple Inc. released the iPhone in Cupertino, "
    "California, in June 2007. Steve Jobs presented it in San Francisco.\"",
    "are \"the cat sat on the mat\" and \"a cat rested upon a mat\" similar?",
    "scrape https://quotes.toscrape.com",
    "remember that my favourite editor is Neovim",
    "what did I ask earlier",
    "start monitoring my activity",
    "what have I been doing today",
    "look at my screen",
    "open youtube",
    "what's trending on youtube",
    "react to that video",
    "react to my youtube in real time",
    "explore https://example.com",
    "read this page",
    "what links are here",
    "follow link 2",
    "go back",
    "browse the web with me",
    "stop browsing with me",
    "set a timer for 10 minutes",
    "how much time is left on my timer",
    "cancel the timer",
    "generate an image of a red fox in falling snow",
    "draw a mountain landscape at sunrise",
]


def selftest(brain, memory, execute: bool = False, untrusted: bool = True) -> int:
    """Route every phrase and report the owning skill.

    By default skills are NOT executed — this verifies wiring without touching
    your files or system. Add --execute to run them (destructive ones are still
    skipped unless --execute --allow-side-effects)."""
    from jarvis.core.registry import all_commands
    risky = ("files.organize", "files.trash", "sys.volume", "sys.power",
             "sys.dnd", "sys.open_app", "sys.screenshot", "files.open",
             "screen.watch_on", "screen.watch_off", "screen.look",
             "yt.open", "yt.play", "yt.pick", "yt.upload", "yt.auth",
             "yt.watch_on", "yt.watch_off",
             "browser.explore", "browser.current", "browser.follow", "browser.back",
             "browser.watch_on", "browser.watch_off",
             "timer.set", "image.generate")
    print(f"\n{'PHRASE':<52} {'ROUTED TO':<16} RESULT")
    print("-" * 108)
    failures = 0
    for phrase in SELFTEST_PHRASES:
        cmd, text = brain.route(phrase)
        target = cmd.name if cmd else "UNROUTED"
        if cmd is None:
            status = "NO ROUTE"
            failures += 1
        elif execute and not (untrusted and target in risky):
            try:
                status = cmd.handler(text)[:52].replace("\n", " ")
            except Exception as exc:
                status = f"ERROR {type(exc).__name__}: {exc}"[:52]
                failures += 1
        else:
            status = "routed (not executed)"
        print(f"{phrase[:50]:<52} {target:<16} {status}")
    print("-" * 108)
    print(f"Skills registered: {len(all_commands())}. "
          f"Intent model: {'sklearn ' + ('ready' if brain.intents.ready else 'off')} "
          f"+ {brain.neural.backend} "
          f"{('ready' if brain.neural.ready else 'off')}. "
          f"Problems: {failures}.")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jarvis", description="JARVIS voice assistant")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--text", action="store_true", help="keyboard input")
    mode.add_argument("--ui", action="store_true",
                      help="holographic web interface in your browser")
    mode.add_argument("--selftest", action="store_true", help="routing self-test")
    mode.add_argument("--oneshot", metavar="ORDER", help="execute one command and exit")
    mode.add_argument("--voice", action="store_true",
                      help="hands-free Auto-Instant voice — no wake word, answers instantly")
    ap.add_argument("--quiet", action="store_true", help="no spoken output")
    ap.add_argument("--no-wake", action="store_true", help="voice mode without wake word")
    ap.add_argument("--execute", action="store_true",
                    help="with --selftest: actually run the skills")
    ap.add_argument("--allow-side-effects", action="store_true",
                    help="with --selftest --execute: include file-moving/volume/app skills")
    args = ap.parse_args(argv)

    brain, memory = _boot()

    from jarvis.core.speech import Speaker
    speaker = Speaker(enabled=not args.quiet and not args.selftest)
    from jarvis.core import activity
    activity.set_speaker(speaker)      # lets the watcher react with voice, if on

    if args.selftest:
        return selftest(brain, memory, execute=args.execute,
                        untrusted=not args.allow_side_effects)
    if args.oneshot:
        handle(brain, memory, args.oneshot, lambda t: speaker.say(t))
        return 0

    if args.ui:
        from jarvis.webserver import serve
        httpd = serve(brain, memory, speaker)
        log("Ctrl-C to shut the interface down.")
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            httpd.shutdown()
            log("Interface closed.")
        return 0

    if args.text:
        text_mode(brain, memory, speaker)
        return 0

    from jarvis.core.speech import Listener
    listener = Listener()
    if listener.backend == "none":
        log("Voice input unavailable — falling back to text mode.")
        text_mode(brain, memory, speaker)
        return 0
    if args.voice or args.no_wake:
        # Auto-Instant hands-free loop — no wake word, answer, then listen again
        handsfree_loop(brain, memory, speaker, listener)
        return 0
    voice_loop(brain, memory, speaker, listener)
    return 0


if __name__ == "__main__":
    sys.exit(main())
