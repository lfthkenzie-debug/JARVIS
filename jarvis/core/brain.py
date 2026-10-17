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

        # 1. regex first — deterministic and instant, highest-priority skills win
        for cmd in ordered_commands():
            for pat in cmd.patterns:
                if re.search(pat, text):
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

        # 3. fuzzy keyword score as last heuristic resort
        best, best_score = None, 0.0
        tokens = set(re.findall(r"\w+", text))
        for cmd in all_commands().values():
            for ex in cmd.examples:
                overlap = len(tokens & set(re.findall(r"\w+", ex))) / max(
                    1, len(set(re.findall(r"\w+", ex))))
                best_score, best = (overlap, cmd) if overlap > best_score else (best_score, best)
        if best and best_score >= 0.5:
            return best, text

        # 4. fallback: conversational skill (LLM if wired, polite canned reply if not)
        chat = all_commands().get("jarvis.chat")
        if chat:
            return chat, text
        return None, text
