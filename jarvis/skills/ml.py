"""Machine-learning toolbox commands — report the state of the ML stack and
retrain JARVIS's own intent models on demand."""
from __future__ import annotations

import importlib

from jarvis.config import log
from jarvis.core.registry import command


def _ver(mod: str) -> str:
    try:
        m = importlib.import_module(mod)
        return getattr(m, "__version__", "ok")
    except Exception:
        return "—"


@command("ml.status", "Report the ML/data stack: PyTorch, TensorFlow, Keras, NumPy, SciPy, sktime, and Apple GPU.",
         patterns=[r"\bml (status|stack|checkup)|tensorflow|keras|pytorch|torch\b|\bgpu\b|\bmps\b"],
         examples=["check the ml stack", "is pytorch installed", "tensorflow status",
                   "do you have gpu access", "mps available", "list library versions"])
def ml_status(utterance: str) -> str:
    parts = []
    try:
        import torch
        mps = getattr(torch.backends, "mps", None)
        if mps and mps.is_available():
            accel = "Apple MPS ready"
        elif mps and mps.is_built():
            accel = "MPS built, GPU unavailable in this session"
        else:
            accel = "CPU"
        parts.append(f"PyTorch {torch.__version__} ({accel})")
    except ImportError:
        parts.append("PyTorch —")
    try:
        import tensorflow as tf
        parts.append(f"TensorFlow {tf.__version__} / Keras {tf.keras.__version__}")
    except ImportError:
        parts.append("TensorFlow —")
    others = [("numpy", "NumPy"), ("scipy", "SciPy"), ("sklearn", "scikit-learn"),
              ("pandas", "Pandas"), ("matplotlib", "Matplotlib"), ("spacy", "spaCy"),
              ("nltk", "NLTK"), ("sktime", "sktime"), ("prophet", "Prophet"),
              ("kats", "Kats"), ("dask", "Dask"), ("pyspark", "PySpark"),
              ("ray", "Ray"), ("kafka", "Kafka"), ("scrapy", "Scrapy"),
              ("mechanicalsoup", "MechanicalSoup"), ("transformers", "Transformers")]
    for mod, label in others:
        v = _ver(mod.strip())
        if v != "—":
            parts.append(f"{label} {v}")
    return "ML stack live: " + "; ".join(parts) + "."


@command("ml.retrain", "Re-train the intent classifier (statistical + neural) from skill examples.",
         patterns=[r"retrain|train (?:your|the) (?:brain|model)|learn (?:my )?(?:new )?commands"],
         examples=["retrain your brain", "train the model", "learn new commands",
                   "retrain intent classifier"])
def retrain(utterance: str) -> str:
    from jarvis.core.brain import Brain
    b = Brain(train=True)
    stats = []
    stats.append("scikit-learn TF-IDF classifier " + ("updated" if b.intents.ready else "offline"))
    stats.append(f"{b.neural.backend} neural classifier " + ("updated" if b.neural.ready else "offline"))
    return "Retraining complete — " + "; ".join(stats) + "."
