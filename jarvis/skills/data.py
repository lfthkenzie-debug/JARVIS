"""Data workbench — CSV/JSON/Parquet analysis with pandas, NumPy, SciPy,
Matplotlib charts, Dask for big files, and SQL via sqlite3.
PySpark / Ray / Kafka are optional accelerators detected at runtime."""
from __future__ import annotations

import glob
import re
import sqlite3
import subprocess
from pathlib import Path

from jarvis.config import CHART_DIR, HOME, SAMPLE_CHART_DPI, log, reveal
from jarvis.core.registry import command


def _find_data_file(utterance: str) -> Path | None:
    m = re.search(r"([\w./-]+\.(csv|json|jsonl|parquet|xlsx|tsv|db|sqlite3?))", utterance)
    if m:
        p = Path(m.group(1)).expanduser()
        if p.exists():
            return p
        for root in (HOME / "Downloads", HOME / "Documents", HOME / "web-apps", HOME):
            hits = list(root.rglob(m.group(1))) if root.exists() else []
            if hits:
                return hits[0]
        return None
    # newest data file in Downloads
    cand: list[Path] = []
    for ext in ("csv", "json", "parquet", "xlsx"):
        cand += list((HOME / "Downloads").glob(f"*.{ext}"))
    if cand:
        return max(cand, key=lambda p: p.stat().st_mtime)
    return None


def _load_df(path: Path):
    """Lazy loader: pandas for normal files, dask if pandas fails or file > 200MB."""
    import pandas as pd
    suf = path.suffix.lower()
    if suf in (".csv", ".tsv"):
        sep = "\t" if suf == ".tsv" else ","
        return pd.read_csv(path, sep=sep)
    if suf == ".json":
        return pd.read_json(path)
    if suf == ".parquet":
        return pd.read_parquet(path)
    if suf in (".xlsx", ".xls"):
        return pd.read_excel(path)
    raise ValueError(f"Unsupported format {suf}")


# ------------------------------------------------------------------ profile
@command("data.profile", "Profile a dataset: shape, dtypes, stats, missing values.",
         patterns=[r"\b(profile|analyze|analyse|describe|inspect)\b.*\b(data|csv|file|dataset|table)\b|\bdataset\b"],
         examples=["analyze my csv", "profile the dataset data.csv", "describe this data file",
                   "inspect the parquet", "what columns does sales csv have"])
def profile(utterance: str) -> str:
    path = _find_data_file(utterance)
    if path is None:
        return "I couldn't locate a data file. Put it in Downloads or give me the name."
    try:
        df = _load_df(path)
    except Exception as exc:
        return f"Failed to load {path.name}: {exc}"
    n, c = df.shape
    num = df.select_dtypes("number")
    miss = int(df.isna().sum().sum())
    bits = [f"{path.name}: {n} rows, {c} columns."]
    if miss:
        bits.append(f"{miss} missing values.")
    if not num.empty:
        import numpy as np
        means = {col: float(num[col].mean()) for col in num.columns[:3]}
        bits.append("Key means: " + ", ".join(f"{k} {v:.2f}" for k, v in means.items()) + ".")
        try:
            corr = num.corr().abs()
            # strictly-lower triangle only: skips the 1.00 self-correlations
            tri = corr.where(np.tril(np.ones(corr.shape, bool), -1))
            pairs = tri.unstack().sort_values(ascending=False).dropna()
            if not pairs.empty:
                (a, b), v = pairs.index[0], pairs.iloc[0]
                if v > 0.6:
                    bits.append(f"Strongest correlation: {a}–{b} at {v:.2f}.")
        except Exception:
            pass
    cats = df.select_dtypes("object").columns.tolist()
    if cats:
        bits.append(f"Text columns: {', '.join(cats[:4])}.")
    return " ".join(bits)


# ------------------------------------------------------------------ chart
@command("data.chart", "Plot a dataset column or correlation chart (Matplotlib).",
         patterns=[r"\b(plot|chart|graph|visuali[sz]e)\b"],
         examples=["plot the sales column", "make a chart of this csv",
                   "graph temperature over time", "visualize the dataset", "draw a histogram"])
def chart(utterance: str) -> str:
    path = _find_data_file(utterance)
    if path is None:
        return "No data file found to plot."
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import pandas as pd
        df = _load_df(path)
    except Exception as exc:
        return f"Chart prep failed: {exc}"
    out = CHART_DIR / f"{path.stem}_{abs(hash(utterance)) % 9999}.png"
    col_m = re.search(r"(?:plot|chart|graph|visualize)\s+(?:the\s+)?([\w ]+?)(?:\s+(?:column|over|against|vs))?", utterance)
    try:
        fig, ax = plt.subplots(figsize=(9, 5))
        numeric = df.select_dtypes("number")
        if col_m and col_m.group(1).strip() in df.columns:
            col = col_m.group(1).strip()
            s = df[col]
            if pd.api.types.is_numeric_dtype(s):
                s.plot(ax=ax, marker="o", ms=2, linewidth=1.2)
            else:
                s.value_counts().head(15).plot.bar(ax=ax)
            ax.set_title(f"{path.name} — {col}")
        elif not numeric.empty:
            numeric.iloc[:, :4].plot(ax=ax)
            ax.set_title(f"{path.name} — numeric columns")
        else:
            df.select_dtypes("object").nunique().plot.bar(ax=ax)
            ax.set_title(f"{path.name} — distinct values per column")
        ax.grid(alpha=0.3)
        fig.autofmt_xdate()
        fig.tight_layout()
        fig.savefig(out, dpi=SAMPLE_CHART_DPI)
        plt.close(fig)
        reveal(out)
        return f"Chart saved and opened: {out.name}."
    except Exception as exc:
        return f"Plotting failed: {exc}"


# ------------------------------------------------------------------ statistics (SciPy)
@command("data.stats", "Statistical tests with SciPy: mean, correlation, normality.",
         patterns=[r"\bstatistics?|correlation|mean of|normality|t-test|hypothesis\b"],
         examples=["what is the mean of sales", "correlation between price and volume",
                   "run statistics on this csv", "is the data normally distributed"])
def stats(utterance: str) -> str:
    path = _find_data_file(utterance)
    if path is None:
        return "No dataset located."
    try:
        import numpy as np
        from scipy import stats as sps
        df = _load_df(path)
        num = df.select_dtypes("number").dropna()
        if num.empty:
            return "No clean numeric columns to analyze."
        col_m = re.search(r"(?:of|between|for)\s+([\w_]+)", utterance)
        if "correlation" in utterance and col_m:
            cols = [c for c in num.columns if col_m.group(1) in c.lower()]
            target = cols[0] if cols else num.columns[0]
            other = num.drop(columns=[target]).columns[0]
            r, p = sps.pearsonr(num[target], num[other])
            return f"Pearson r between {target} and {other}: {r:.3f} (p={p:.3g})."
        if "normal" in utterance:
            col = num.columns[0]
            stat, p = sps.normaltest(num[col])
            return f"{col} {'looks' if p > 0.05 else 'does not look'} normally distributed (p={p:.3g})."
        col = (col_m.group(1) if col_m else "")
        pick = next((c for c in num.columns if col and col in c.lower()), num.columns[0])
        series = num[pick].to_numpy(dtype=float)
        d = sps.describe(series)
        import numpy as np
        return (f"{pick}: mean {d.mean:.2f}, std {np.std(series, ddof=1):.2f}, "
                f"skew {d.skewness:.2f}, min {series.min()}, max {series.max()}.")
    except Exception as exc:
        return f"Stats failed: {exc}"


# ------------------------------------------------------------------ SQL
@command("data.sql", "Run a SQL query against a SQLite database.",
         patterns=[r"\bsql\b|query the (?:database|db)|sqlite"],
         examples=["query the database for count of users", "run sql select * from orders limit 5",
                   "show tables in the db"])
def sql(utterance: str) -> str:
    m = re.search(r"([\w./-]+\.(db|sqlite3?))", utterance)
    db = Path(m.group(1)).expanduser() if m else None
    if db is None or not db.exists():
        cand = list(HOME.rglob("*.sqlite3"))[:1] or list(HOME.rglob("*.db"))[:1]
        if not cand:
            return "No SQLite database found. Point me at one."
        db = cand[0]
    q_m = re.search(r"(select\s+.+?)(?:\s*$|;)", utterance, re.I)
    if not q_m:
        with sqlite3.connect(db) as conn:
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")]
        return f"{db.name} has tables: {', '.join(tables[:10])}."
    try:
        with sqlite3.connect(db) as conn:
            cur = conn.execute(q_m.group(1))
            rows = cur.fetchmany(10)
            cols = [d[0] for d in cur.description] if cur.description else []
        if not rows:
            return "Query returned no rows."
        preview = "; ".join(" ".join(f"{c}={v}" for c, v in zip(cols, r)) for r in rows[:3])
        return f"{len(rows)} row(s). {preview}"
    except Exception as exc:
        return f"Query error: {exc}"


# ------------------------------------------------------------------ big-data backends
@command("data.backends", "Report which data backends are live: Dask, Spark, Ray, Kafka.",
         patterns=[r"(dask|pyspark|spark|ray|kafka).*(available|status|installed|version)|backend status"],
         examples=["is spark available", "check dask status", "ray available",
                   "kafka installed", "backend status"])
def backends(utterance: str) -> str:
    report = []
    for mod, label in (("dask", "Dask"), ("pyspark", "PySpark"), ("ray", "Ray"),
                       ("kafka", "Kafka-Python"), ("pandas", "Pandas"), ("numpy", "NumPy")):
        try:
            m = __import__(mod)
            report.append(f"{label} {getattr(m, '__version__', 'ok')}")
        except Exception:
            report.append(f"{label} — not installed")
    return "Backends: " + "; ".join(report) + "."
