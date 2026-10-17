"""Time-series analysis — forecasting with Prophet, sktime, or Kats when
installed; a NumPy/SciPy baseline otherwise. Charts every forecast."""
from __future__ import annotations

import re
import subprocess
from datetime import datetime, timedelta

import numpy as np

from jarvis.config import CHART_DIR, SAMPLE_CHART_DPI, reveal
from jarvis.core.registry import command


def _sample_series(periods: int = 120, freq_days: int = 1):
    """Deterministic synthetic daily series (trend + seasonality + noise)
    so demo/forecast commands always work."""
    rng = np.random.default_rng(42)
    t = np.arange(periods)
    y = 50 + 0.4 * t + 12 * np.sin(2 * np.pi * t / 30) + rng.normal(0, 3, periods)
    dates = [datetime.now().date() - timedelta(days=freq_days * (periods - 1 - i))
             for i in range(periods)]
    return dates, y


def _load_series(utterance: str):
    """Try a real CSV first (date + numeric column), else synthesize."""
    from jarvis.skills.data import _find_data_file, _load_df
    p = _find_data_file(utterance)
    if p is not None:
        try:
            df = _load_df(p)
            datecol = next((c for c in df.columns
                            if "date" in c.lower() or "time" in c.lower()), None)
            numeric = list(df.select_dtypes("number").columns)
            want = re.search(r"(?:forecast|plot|trend|stationary)\s+(?:the\s+)?([\w_]+)",
                             utterance)
            numcol = next((c for c in numeric if want and want.group(1) == c.lower()),
                          None) or (numeric[0] if numeric else None)
            if numcol is None:
                return _sample_series()
            values = df[numcol].to_numpy(dtype=float)
            if datecol:
                return list(df[datecol]), values
            return list(range(len(df))), values
        except Exception as exc:
            from jarvis.config import log
            log(f"Series load failed for {p.name}, using synthetic: {exc}")
    return _sample_series()


@command("ts.forecast", "Forecast a time series N days ahead and chart it.",
         patterns=[r"forecast|predict.*(next|future|tomorrow|week|month|day)|time series"],
         examples=["forecast sales for the next thirty days", "predict temperature next week",
                   "run a time series forecast", "forecast this csv hundred days",
                   "what will revenue be in two months"])
def forecast(utterance: str) -> str:
    dates, y = _load_series(utterance)
    m = re.search(r"(\d+)\s*(day|week|month)", utterance)
    horizon = (int(m.group(1)) * {"day": 1, "week": 7, "month": 30}[m.group(2)]
               if m else 30)

    method, fut = None, None
    # 1. Prophet (Facebook additive Bayesian model)
    try:
        try:
            from prophet import Prophet
        except ImportError:                      # legacy package name
            from fbprophet import Prophet
        import pandas as pd
        df = pd.DataFrame({"ds": pd.to_datetime(dates), "y": y})
        model = Prophet(yearly_seasonality=False, weekly_seasonality=True,
                        daily_seasonality=False, changepoint_prior_scale=0.1)
        model.fit(df)
        future = model.make_future_dataframe(periods=horizon)
        fc = model.predict(future)
        fut = fc["yhat"].tail(horizon).to_numpy()
        method = "Prophet"
    except Exception:
        pass
    # 2. sktime exponential smoothing
    if method is None:
        try:
            from sktime.forecasting.exp_smoothing import ExponentialSmoothing
            from sktime.forecasting.base import ForecastingHorizon
            import pandas as pd
            try:
                idx = pd.PeriodIndex(pd.to_datetime(list(dates)), freq="D")
            except Exception:
                idx = pd.RangeIndex(len(y))
            s = pd.Series(y, index=idx)
            model = ExponentialSmoothing(sp=7, trend="add", seasonal="add")
            model.fit(s)
            fh = ForecastingHorizon(np.arange(1, horizon + 1), is_relative=True)
            fut = np.asarray(model.predict(fh))
            method = "sktime (ETS)"
        except Exception:
            pass
    # 3. Kats
    if method is None:
        try:
            from kats.consts import TimeSeriesData
            from kats.models import holt_winters
            import pandas as pd
            ts = TimeSeriesData(pd.DataFrame({"time": pd.to_datetime(dates), "value": y}))
            model = holt_winters.HoltWintersParams(trend="add", seasonal="add",
                                                    m=30)
            res = holt_winters.HoltWinters(ts)
            res.fit(forecast_steps=horizon)
            fut = res.fcst["fcst_mean"].to_numpy()
            method = "Kats"
        except Exception:
            pass
    # 4. Baseline: detrended phase means + linear trend (pure NumPy/SciPy)
    if method is None:
        n = len(y)
        t = np.arange(n)
        slope, intercept = np.polyfit(t, y, 1)
        resid = y - (slope * t + intercept)
        # dominant cycle = smallest lag whose autocorrelation is near the ACF peak
        # (the fundamental period, not one of its harmonics)
        lags = np.arange(2, min(61, n // 2))
        acf = np.array([float(np.corrcoef(resid[:-k], resid[k:])[0, 1]) for k in lags])
        period = 1
        if acf.max() > 0.1:
            period = int(lags[np.where(acf >= 0.85 * acf.max())[0][0]])
        seasonal = np.array([resid[i::period].mean() for i in range(period)])
        fut_t = np.arange(n, n + horizon)
        fut = slope * fut_t + intercept + seasonal[fut_t % period]
        method = f"trend + seasonal baseline (period {period}, ACF {acf.max():.2f})"

    last = float(y[-1])
    end = float(fut[-1])
    delta = (end - last) / abs(last) * 100 if last else 0
    _chart_forecast(dates, y, fut)
    return (f"{method} forecast over {horizon} days: "
            f"{last:.1f} → {end:.1f} ({delta:+.1f}%). Chart opened.")


def _chart_forecast(dates, y, fut) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(10, 5))
        ax.plot(range(len(y)), y, label="observed")
        ax.plot(range(len(y) - 1, len(y) + len(fut)),
                np.r_[y[-1], fut], label="forecast", color="tab:red")
        ax.axvline(len(y) - 1, color="k", ls="--", alpha=0.4)
        ax.set_title("JARVIS time-series forecast")
        ax.legend()
        ax.grid(alpha=0.3)
        fig.tight_layout()
        out = CHART_DIR / f"forecast_{int(datetime.now().timestamp())}.png"
        fig.savefig(out, dpi=SAMPLE_CHART_DPI)
        plt.close(fig)
        reveal(out)
    except Exception as exc:
        from jarvis.config import log
        log(f"Forecast chart skipped: {exc}")


@command("ts.analyze", "Decompose a series: trend, seasonality, stationarity.",
         patterns=[r"decompos|stationar|seasonal|autocorrelation|\bacf\b"],
         examples=["is the series stationary", "decompose the time series",
                   "check seasonality", "plot the autocorrelation"])
def analyze(utterance: str) -> str:
    dates, y = _load_series(utterance)
    bits = []
    # trend strength via linear fit residual variance
    t = np.arange(len(y))
    slope, intercept = np.polyfit(t, y, 1)
    resid = y - (slope * t + intercept)
    trend_pct = 100 * (1 - resid.var() / y.var())
    bits.append(f"Trend explains {trend_pct:.0f}% of variance (slope {slope:+.3f}/step).")
    # autocorrelation lag-1 & lag-30
    def acf(k: int) -> float:
        a, b = y[:-k], y[k:]
        return float(np.corrcoef(a, b)[0, 1])
    bits.append(f"ACF lag-1 {acf(1):.2f}, lag-30 {acf(30) if len(y) > 30 else 0:.2f}.")
    # crude stationarity check: compare variance halves
    h = len(y) // 2
    ratio = y[:h].var() / max(y[h:].var(), 1e-9)
    bits.append("Looks stationary" if 0.5 < ratio < 2 else
                "Non-stationary — consider differencing before modelling.")
    return " ".join(bits)
