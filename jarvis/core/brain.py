"""The Brain — turns an utterance into a skill call.

Routing strategy (fast -> smart -> graceful):
  1. Regex patterns from the skill registry (0ms, deterministic).
  2. ML intent classifier: TF-IDF + LinearSVC (scikit-learn). Trained locally
     from every skill's `examples`. This is the statistical understanding layer.
  3. Optional LLM fallback (`llama` via ollama / openai) for chit-chat and
     commands the classifier can't map — keeps JARVIS useful for anything.
"""
from __future__ import annotations

import re
from typing import Optional, Tuple

from jarvis.config import INTENT_MODEL, log
from jarvis.core.neural import NeuralIntent
from jarvis.core.registry import all_commands, ordered_commands, Command
from jarvis.core import ostools


# Common function words: stripped from the fuzzy-resort token sets so a short
# confirmation ("yes it is", "that is right") can't out-score a real command.
_STOP = frozenset("""
a an the and or but if then than that this these those it its is am are was were
be been being do does did doing have has had having of in on at to for with from
by as about into over under again more most other some such no nor not only own
same so too very can will just should now you your yours we our ours they their
he she him her his i me my mine us what when where which who whom why how all any
both each few other own s t re ve ll d m o yes please thanks thank sir""".split())

# Words that end the app-name object in "open/quit <app> [politeness]".
_APP_STOP = {"for", "me", "please", "now", "to", "and", "so", "that", "with",
             "up", "again", "instantly", "window", "windows", "jarvis", "it",
             "the", "a", "an", "my", "him", "her", "them", "this", "check"}
_APP_VERB_RE = re.compile(
    r"\b(?:open|launch|start|fire up|run|quit|close|kill)\b\s+"
    r"(?:the\s+|an?\s+|app\s+|my\s+)?(.*)")
# Nouns that signal a *file* action, not an application — keeps the router's app
# resolver from grabbing "open the pdf invoice" when a PDF viewer shares the token.
_FILE_HINT = re.compile(
    r"\b(?:file|folder|directory|documents?|downloads?|desktop|pdf|docx?|xlsx?|"
    r"pptx?|csv|txt|md|json|png|jpe?g|gif|svg|mp[34]|mov|avi|link|url|invoice|"
    r"report|image|photo|picture|screenshot|resume|receipt)\b")


# How-to / advice questions — the user wants guidance (the LLM's job), not a
# keyword-matched skill to answer "how do I cool down my cpu" with a status dump.
_HOWTO_RE = re.compile(
    r"\b(?:how|what|which)\b.{0,30}?\b(?:do|can|should|could)\s+i\b"
    r"|\bhow\s+to\b|\b(?:ways?|tips?)\s+(?:to|for)\b"
    r"|\bwhy\s+(?:is|are|do|does|can|should|would)\b")


def _app_candidates(text: str):
    """Yield plausible app-name substrings from an open/quit imperative, longest
    first (max 4 words), so the router can test each against the OS app resolver."""
    m = _APP_VERB_RE.search(text)
    if not m:
        return
    seq = []
    for w in re.findall(r"[a-z0-9][a-z0-9._+-]*", m.group(1)):
        if w in _APP_STOP:
            break
        seq.append(w)
    for n in range(min(4, len(seq)), 0, -1):
        yield " ".join(seq[:n])


class IntentModel:
    def __init__(self):
        self.pipe = None
        self.ready = False
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.svm import LinearSVC
            from sklearn.pipeline import Pipeline
            from sklearn.model_selection import cross_val_score
            self._sk = (TfidfVectorizer, LinearSVC, Pipeline, cross_val_score)
        except Exception as exc:
            log(f"scikit-learn unavailable, regex routing only ({exc})")
            self._sk = None

    # ------------------------------------------------------------ training
    def train(self) -> bool:
        if self._sk is None:
            return False
        Tfidf, SVC, Pipeline, cv = self._sk
        docs, labels = [], []
        for cmd in all_commands().values():
            for ex in cmd.examples:
                docs.append(ex.lower())
                labels.append(cmd.name)
        if len(set(labels)) < 2 or len(docs) < 10:
            log("Not enough intent examples to train; using regex routing.")
            return False
        self.pipe = Pipeline([
            ("tfidf", Tfidf(ngram_range=(1, 2), sublinear_tf=True,
                            stop_words="english")),
            ("clf", SVC(class_weight="balanced", max_iter=3000)),
        ])
        try:
            from collections import Counter
            per_class = Counter(labels)
            if min(per_class.values()) >= 5:               # CV needs 5+ examples/class
                folds = min(3, min(per_class.values()))
                scores = cv(self.pipe, docs, labels, cv=folds)
                log(f"Intent model CV accuracy: {scores.mean():.0%}")
        except Exception:
            pass
        self.pipe.fit(docs, labels)
        self.ready = True
        try:
            import joblib
            joblib.dump(self.pipe, INTENT_MODEL)
        except Exception:
            pass
        return True

    def load(self) -> bool:
        try:
            import joblib
            if INTENT_MODEL.exists():
                self.pipe = joblib.load(INTENT_MODEL)
                self.ready = True
                return True
        except Exception:
            pass
        return False

    # ------------------------------------------------------------ inference
    def predict(self, text: str) -> Tuple[Optional[str], float]:
        if not self.ready:
            return None, 0.0
        try:
            label = self.pipe.predict([text.lower()])[0]
            conf = 0.8  # LinearSVC has no proba; distance surrogate below
            try:
                d = self.pipe.decision_function([text.lower()])[0]
                import numpy as np
                conf = float(np.clip(d.max() / 3.0, 0, 1))
            except Exception:
                pass
            return label, conf
        except Exception:
            return None, 0.0


def _normalize(utterance: str) -> str:
    """Lowercase the command words (so routing is case-insensitive) but keep the
    case of quoted payloads — NER and summarisation need proper nouns intact."""
    text = utterance.strip().lower()
    original = utterance.strip()

    def restore(m: re.Match) -> str:
        return original[m.start():m.end()]

    return re.sub(r'["“][^"”]{6,}["”]', restore, text)


class Brain:
    def __init__(self, train: bool = True):
        self.intents = IntentModel()
        loaded = self.intents.load()
        if not loaded:
            self.intents.train()
        self.neural = NeuralIntent()
        if not self.neural.load():
            self.neural.train()

    def route(self, utterance: str) -> Tuple[Optional[Command], str]:
        """Return (command, argument_text). (None, utterance) if unrouted."""
        text = _normalize(utterance)

        # 0. advice / how-to questions — the user wants guidance, not a keyword-matched
        #    status dump ("how do I cool down my cpu" ≠ report my load). The LLM answers.
        #    Carve-out: "how do I open <app>" is really a launch request, so let any
        #    phrase that names an app action fall through to steps 1 / 1b below.
        if _HOWTO_RE.search(text) and not re.search(
                r"\b(?:open|launch|start|fire up|run|quit|close|kill|set|click)\b", text):
            cmd = all_commands().get("jarvis.chat")
            if cmd:
                return cmd, text

        # 1. regex first — deterministic and instant, highest-priority skills win
        for cmd in ordered_commands():
            for pat in cmd.patterns:
                if re.search(pat, text):
                    return cmd, text

        # 1b. an imperative aimed at a *specific installed application* — hand it to
        #     sys.open_app so the app really launches, instead of the chat model only
        #     pretending to. The OS app resolver gates this, so a stray "open/start"
        #     whose object isn't a real app (file, timer, shell cmd) never gets grabbed.
        if (re.search(r"\b(?:open|launch|start|fire up|quit|close|kill)\b", text)
                and not re.search(_FILE_HINT, text)):
            for cand in _app_candidates(text):
                if ostools.find_app_path(cand):
                    cmd = all_commands().get("sys.open_app")
                    if cmd:
                        return cmd, text

        # 2. statistical + neural classifiers — agreement boosts confidence
        label, conf = self.intents.predict(text)
        n_label, n_conf = self.neural.predict(text)
        if n_label and n_label == label:
            conf = min(1.0, conf + n_conf)          # both models agree
        elif n_conf > 0.65 and (n_conf > conf or not label):
            label, conf = n_label, n_conf           # neural is more certain
        if label and conf >= 0.25:
            cmd = all_commands().get(label)
            if cmd:
                return cmd, text

        # 3. fuzzy keyword score as last heuristic resort — content words only,
        #    and at least TWO shared words, so a single generic overlap ("check",
        #    "it is") can't out-score a real command and hijack a skill.
        best, best_score = None, 0.0
        tokens = {t for t in re.findall(r"\w+", text) if t not in _STOP}
        for cmd in all_commands().values():
            for ex in cmd.examples:
                ex_tokens = {t for t in re.findall(r"\w+", ex) if t not in _STOP}
                shared = tokens & ex_tokens
                if len(shared) < 2 or not ex_tokens:
                    continue
                overlap = len(shared) / len(ex_tokens)
                best_score, best = (overlap, cmd) if overlap > best_score else (best_score, best)
        if best and best_score >= 0.5:
            return best, text

        # 4. fallback: conversational skill (LLM if wired, polite canned reply if not)
        chat = all_commands().get("jarvis.chat")
        if chat:
            return chat, text
        return None, text
