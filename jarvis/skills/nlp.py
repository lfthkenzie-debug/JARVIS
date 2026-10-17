"""Natural Language Processing — spaCy (NER, parsing, similarity),
transformers/BERT extractive summarization, NLTK utilities, Polyglot detection.
Every backend is optional; spaCy is the workhorse."""
from __future__ import annotations

import re
from pathlib import Path

from jarvis.config import HOME
from jarvis.core.registry import command


def _text_from(utterance: str) -> str | None:
    """Pull text out of quotes, or load a file the user names."""
    m = re.search(r'["“\'](.{20,})["”\']', utterance)
    if m:
        return m.group(1)
    m = re.search(r"([\w./-]+\.(txt|md|text|rst|csv|log|json))", utterance)
    if m:
        p = Path(m.group(1)).expanduser()
        if not p.exists():
            for root in (HOME / "Documents", HOME / "Desktop", HOME / "Downloads"):
                hits = list(root.glob("**/" + p.name)) if root.exists() else []
                if hits:
                    p = hits[0]
                    break
        if p.exists():
            return p.read_text(errors="ignore")
        from jarvis.skills.data import _find_data_file
        p = _find_data_file(utterance)
        if p is not None:
            return p.read_text(errors="ignore")
    return None


# ------------------------------------------------------------------ summarize (BERT > lexrank)
@command("nlp.summarize", "Summarize text or a document.",
         patterns=[r"summar", r"\btldr?\b", r"shorten (?:this|the)"],
         examples=["summarize this article", "summarize notes txt", "give me the tldr of this text",
                   "shorten the document into three sentences"])
def summarize(utterance: str) -> str:
    text = _text_from(utterance)
    if not text:
        return "Give me text in quotes or a file name, sir."
    # 1. Transformer abstractive (BERT-family) if present
    try:
        from transformers import pipeline  # type: ignore
        key = "jarvis.summarizer"
        from jarvis.core.memory import Memory
        mem = Memory()
        pipe = mem.recall(key)  # cached marker so we know it loads
        if pipe is None:
            mem.remember(key, "loaded")
            import torch
            torch.set_num_threads(4)
            p = pipeline("summarization", model="sshleifer/distilbart-cnn-12-6")
            out = p(text[:6000], max_length=90, do_sample=False)[0]["summary_text"]
            return "BERT-class model: " + out.strip()
    except ImportError:
        pass
    except Exception as exc:
        from jarvis.config import log
        log(f"Transformer summarizer failed, falling back: {exc}")
    # 2. LexRank-style extractive with spaCy sentence splitting
    try:
        import numpy as np
        from jarvis.skills.nlp import _spacy_model
        nlp = _spacy_model()
        sents = [s.text.strip() for s in nlp(text[:8000]).sents
                 if len(s.text.split()) >= 5]
        if not sents:
            return text[:200]
        import re as _re
        words = [set(_re.findall(r"\w+", s.lower())) for s in sents]
        freq = {}
        for w in words:
            for tok in w:
                freq[tok] = freq.get(tok, 0) + 1
        scores = [sum(freq.get(t, 0) for t in w) / (len(w) + 1) for w in words]
        keep = max(1, min(3, len(sents) // 5))
        top = [sents[i] for i in sorted(range(len(sents)),
                                        key=lambda i: scores[i], reverse=True)[:keep]]
        return "Summary: " + " ".join(top)[:400]
    except Exception as exc:
        return "Summary (first 3 sentences): " + " ".join(text.split()[:80])[:350]


def _spacy_model():
    import spacy
    try:
        return spacy.load("en_core_web_sm")
    except OSError:
        import subprocess, sys
        subprocess.run([sys.executable, "-m", "spacy", "download", "en_core_web_sm"],
                       check=False)
        return spacy.load("en_core_web_sm")


# ------------------------------------------------------------------ entities
@command("nlp.entities", "Named-entity recognition over text.",
         patterns=[r"\bentit\w*\b|\bner\b|mentioned(?:\s+in)?\s+(?:this|the|these)",
                   r"names? (?:in|from) (?:this|the|these)"],
         examples=["extract entities from this article", "who is mentioned in notes txt",
                   "find the names and places in this text", "run ner on this paragraph"])
def entities(utterance: str) -> str:
    text = _text_from(utterance)
    if not text:
        return "Supply text in quotes or a file path."
    try:
        nlp = _spacy_model()
        doc = nlp(text[:5000])
        from collections import Counter
        found = Counter(f"{e.text} ({e.label_})" for e in doc.ents)
        if not found:
            return "No named entities detected."
        return "Entities: " + ", ".join(k for k, _ in found.most_common(10))
    except ImportError:
        return "spaCy not installed — run ./setup.sh."
    except Exception as exc:
        return f"NER failed: {exc}"


# ------------------------------------------------------------------ language detect (polyglot/spaCy)
@command("nlp.language", "Detect the language of text.",
         patterns=[r"what language|detect language|language of"],
         examples=["what language is this text", "detect the language of this paragraph",
                   "is this spanish or italian"])
def language(utterance: str) -> str:
    text = _text_from(utterance) or re.sub(r".*language of (?:the )?", "", utterance)
    try:
        from polyglot.detect import Detector  # type: ignore
        return f"Polyglot: {Detector(text[:500], nb_threads=2)[0].name}."
    except Exception:
        pass
    try:
        from langdetect import detect, DetectorFactory  # type: ignore
        DetectorFactory.seed = 0
        return f"Detected language: {detect(text[:800])}."
    except Exception:
        pass
    try:
        nlp = _spacy_model()
        doc = nlp.make_doc(text[:500])
        pipes = nlp.pipe_names
        return "spaCy base model (English) parsed it; use textcat-language for exact ISO codes."
    except ImportError:
        return "No language detector installed — run ./setup.sh."


# ------------------------------------------------------------------ similarity (NLTK/spaCy)
@command("nlp.similar", "Compare two texts for similarity.",
         patterns=[r"similar|same mean|compare (?:the )?(?:text|sentence|paragraph)"],
         examples=["are these two sentences similar", "compare these paragraphs",
                   "do these texts mean the same thing"])
def similar(utterance: str) -> str:
    chunks = re.findall(r'["“\']([^"\u201c\u201d]{6,})["”\']', utterance)
    if len(chunks) < 2:
        return "Quote both texts, sir — like: are \"...\" and \"...\" similar?"
    a, b = chunks[0], chunks[1]
    try:
        nlp = _spacy_model()
        va, vb = nlp(a), nlp(b)
        score = float(va.similarity(vb))
        verdict = "essentially the same" if score > 0.85 else \
                  "closely related" if score > 0.7 else \
                  "partially related" if score > 0.5 else "different"
        return f"spaCy similarity {score:.2f} — {verdict}."
    except ImportError:
        pass
    try:
        from nltk.corpus import stopwords
        from nltk.stem import PorterStemmer
        import numpy as np
        stem = PorterStemmer()
        stop = set(stopwords.words("english"))
        def vec(t):
            toks = [stem.stem(w)
                    for w in re.findall(r"\w+", t.lower()) if w not in stop]
            vocab = sorted(set(toks))
            return np.array([toks.count(v) for v in vocab], float)
        va, vb = vec(a), vec(b)
        cos = float(np.dot(va, vb) / (np.linalg.norm(va) * np.linalg.norm(vb) + 1e-9))
        return f"NLTK/NumPy cosine similarity {cos:.2f}."
    except Exception as exc:
        return f"Comparison failed: {exc}"


# ------------------------------------------------------------------ word stats (NLTK)
@command("nlp.stats", "Word frequency and readability for text or a file.",
         patterns=[r"word (?:frequency|count|cloud)|readability|flesch|most used word"],
         examples=["most used words in notes txt", "word frequency of this article",
                   "what is the readability score"])
def wordstats(utterance: str) -> str:
    text = _text_from(utterance)
    if not text:
        return "Point me at a text file or quote the passage."
    try:
        from nltk.corpus import stopwords
        from nltk.tokenize import word_tokenize, sent_tokenize
        try:
            tokens = word_tokenize(text.lower())
        except LookupError:
            import nltk
            nltk.download("punkt", quiet=True)
            nltk.download("punkt_tab", quiet=True)
            nltk.download("stopwords", quiet=True)
            tokens = word_tokenize(text.lower())
        try:
            stop = set(stopwords.words("english"))
        except LookupError:
            import nltk
            nltk.download("stopwords", quiet=True)
            stop = set(stopwords.words("english"))
        from collections import Counter
        words = [t for t in tokens if t.isalpha() and t not in stop]
        top = Counter(words).most_common(5)
        sents = sent_tokenize(text)
        wps = len(words) / max(len(sents), 1)
        # crude Flesch reading ease
        vowels = sum(1 for w in words if re.search(r"[aeiouy]", w))
        syll_est = sum(max(1, len(re.findall(r"[aeiouy]+", w))) for w in words)
        spe = syll_est / max(len(words), 1)
        flesch = 206.835 - 1.015 * wps - 84.6 * spe
        return (f"{len(words)} content words. Top: " +
                ", ".join(f"{w} x{c}" for w, c in top) +
                f". Reading ease {flesch:.0f}/100 "
                f"({'plain' if flesch > 60 else 'dense' if flesch > 30 else 'specialist'})."
                )
    except ImportError:
        return "NLTK not installed — run ./setup.sh."
    except Exception as exc:
        return f"Analysis failed: {exc}"
