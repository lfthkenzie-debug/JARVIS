"""Neural intent classifier — an optional deep-learning layer for the Brain.

Backend preference: PyTorch (with Apple MPS acceleration) → TensorFlow/Keras → none.
Trained on the same utterance examples as the scikit-learn model, so JARVIS has
both a fast statistical router and a small neural router. The neural model wins
when both agree; otherwise the sklearn routing is used.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

from jarvis.config import DATA_DIR, log
from jarvis.core.registry import all_commands

VOCAB_FILE = DATA_DIR / "neural_vocab.json"
TORCH_WEIGHTS = DATA_DIR / "neural_intent.pt"
TF_WEIGHTS = DATA_DIR / "neural_intent_tf"


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


class NeuralIntent:
    """Bag-of-words → hidden → intent softmax. Tiny, trains in seconds."""

    def __init__(self):
        self.backend = "none"
        self.ready = False
        self.vocab: dict[str, int] = {}
        self.labels: list[str] = []
        self.model = None
        self._torch = None
        try:
            import torch  # type: ignore
            self._torch = torch
            self.backend = "torch"
        except ImportError:
            try:
                import tensorflow as tf  # type: ignore
                self._tf = tf
                self.backend = "tf"
            except ImportError:
                log("No PyTorch/TensorFlow — neural layer offline.")

    # ------------------------------------------------------------------ data
    def _dataset(self):
        docs, labels = [], []
        for cmd in all_commands().values():
            for ex in cmd.examples:
                docs.append(_tokenize(ex))
                labels.append(cmd.name)
        self.labels = sorted({*labels, *[l for l in self.labels if l]})
        idx = {l: i for i, l in enumerate(self.labels)}
        vocab = defaultdict(int)
        for toks in docs:
            for t in toks:
                vocab[t] += 1
        self.vocab = {t: i for i, t in enumerate(sorted(vocab), start=1)}
        X = [[self.vocab.get(t, 0) for t in toks] for toks in docs]
        maxlen = max((len(x) for x in X), default=1)
        X = [x + [0] * (maxlen - len(x)) for x in X]
        y = [idx[l] for l in labels]
        return X, y, maxlen

    def _encode(self, text: str, maxlen: int) -> list[int]:
        v = [_tokenize(text)]
        return [self.vocab.get(t, 0) for t in v[0]] + [0] * (maxlen - len(v[0]))

    # ------------------------------------------------------------------ train
    def train(self) -> bool:
        if self.backend == "none":
            return False
        X, y, maxlen = self._dataset()
        if not X or len(set(y)) < 2:
            return False
        try:
            if self.backend == "torch":
                return self._train_torch(X, y, maxlen)
            return self._train_tf(X, y, maxlen)
        except Exception as exc:
            log(f"Neural training failed ({exc}); statistical routing only.")
            return False

    def _train_torch(self, X, y, maxlen) -> bool:
        import torch
        import torch.nn as nn
        n_class = len(self.labels)
        Xv = torch.tensor(X)
        # multi-hot presence map, robust to token order
        vocab_n = max(self.vocab.values(), default=1) + 1
        B = torch.zeros(Xv.shape[0], min(vocab_n, 8000))
        for i, row in enumerate(Xv.tolist()):
            for t in row:
                if 0 < t < B.shape[1]:
                    B[i, t] = 1.0
        Y = torch.tensor(y)
        model = nn.Sequential(nn.Linear(B.shape[1], 128), nn.ReLU(),
                              nn.Dropout(0.25), nn.Linear(128, n_class))
        opt = torch.optim.Adam(model.parameters(), lr=1e-3)
        lossf = nn.CrossEntropyLoss()
        for epoch in range(180):
            opt.zero_grad()
            loss = lossf(model(B), Y)
            loss.backward()
            opt.step()
        with torch.no_grad():
            acc = (model(B).argmax(1) == Y).float().mean().item()
        torch.save({"state": model.state_dict(), "n_in": B.shape[1],
                    "n_class": n_class}, TORCH_WEIGHTS)
        self.model = model
        self.ready = True
        self._persist_meta(maxlen)
        log(f"PyTorch intent net trained — train accuracy {acc:.0%}.")
        return True

    def _train_tf(self, X, y, maxlen) -> bool:
        import numpy as np
        import tensorflow as tf
        from tensorflow.keras.models import Sequential
        from tensorflow.keras.layers import Dense, Dropout
        vocab_n = max(self.vocab.values(), default=1) + 1
        n_in = min(vocab_n, 8000)
        B = np.zeros((len(X), n_in), dtype="float32")
        for i, row in enumerate(X):
            for t in row:
                if 0 < t < n_in:
                    B[i, t] = 1.0
        model = Sequential([Dense(128, activation="relu", input_shape=(n_in,)),
                            Dropout(0.25),
                            Dense(len(self.labels), activation="softmax")])
        model.compile("adam", "sparse_categorical_crossentropy", metrics=["acc"])
        model.fit(B, np.array(y), epochs=60, verbose=0)
        model.save(TF_WEIGHTS)
        self.model = model
        self.ready = True
        self._persist_meta(maxlen)
        log("Keras intent net trained.")
        return True

    def _persist_meta(self, maxlen: int) -> None:
        VOCAB_FILE.write_text(json.dumps(
            {"vocab": self.vocab, "labels": self.labels, "maxlen": maxlen}))

    # ------------------------------------------------------------------ load / infer
    def load(self) -> bool:
        if self.backend == "none" or not VOCAB_FILE.exists():
            return False
        try:
            meta = json.loads(VOCAB_FILE.read_text())
            self.vocab, self.labels, self.maxlen = (
                meta["vocab"], meta["labels"], meta["maxlen"])
            if self.backend == "torch" and TORCH_WEIGHTS.exists():
                import torch
                import torch.nn as nn
                blob = torch.load(TORCH_WEIGHTS, map_location="cpu")
                m = nn.Sequential(nn.Linear(blob["n_in"], 128), nn.ReLU(),
                                  nn.Dropout(0.25), nn.Linear(128, blob["n_class"]))
                m.load_state_dict(blob["state"])
                m.eval()
                self.model, self.ready = m, True
                return True
            if self.backend == "tf" and Path(TF_WEIGHTS).exists():
                self.model = self._tf.keras.models.load_model(TF_WEIGHTS)
                self.ready = True
                return True
        except Exception as exc:
            log(f"Neural load skipped: {exc}")
        return False

    def predict(self, text: str) -> tuple[str | None, float]:
        if not self.ready:
            return None, 0.0
        toks = _tokenize(text)
        n_in = min(max(self.vocab.values(), default=1) + 1, 8000)
        present = sorted({self.vocab[t] for t in toks if t in self.vocab and
                          self.vocab[t] < n_in})
        if not present:
            return None, 0.0
        try:
            if self.backend == "torch":
                import torch
                x = torch.zeros(1, n_in)
                x[0, present] = 1.0
                probs = torch.softmax(self.model(x), dim=1)[0]
                p, i = probs.max()
                return self.labels[int(i)], float(p)
            x = self._tf.one_hot(present, n_in)
            x = self._tf.reduce_sum(x, axis=0, keepdims=True)
            x = self._tf.clip_by_value(x, 0, 1)
            probs = self.model.predict(x, verbose=0)[0]
            i = int(probs.argmax())
            return self.labels[i], float(probs[i])
        except Exception:
            return None, 0.0
