"""Metrics of Section "Metrics": ASR, dQ, FAR, DL, RT, OH (+ a few helpers)."""
import numpy as np
import pandas as pd

from config import Config


def recovery_time(y: np.ndarray, t_start: int, w: int = 100, frac: float = 0.95) -> float:
    """Rounds from attack start until the rolling mean is back to frac * pre-attack level.

    0   -> the rolling mean never dipped below the threshold
    nan -> undefined (attack starts too early) or never recovered
    """
    if t_start is None or t_start < w or t_start >= len(y) - w:
        return np.nan
    pre = y[t_start - w:t_start].mean()
    roll = pd.Series(y).rolling(w).mean().to_numpy()
    thr = frac * pre
    dip = next((i for i in range(t_start + 1, len(y)) if roll[i] < thr), None)
    if dip is None:
        return 0.0
    rec = next((j for j in range(dip + 1, len(y)) if roll[j] >= thr), None)
    return float(rec - t_start) if rec is not None else np.nan


def summarize(res, cfg: Config) -> dict:
    df, meta = res.df, res.meta
    y = df.y.to_numpy()
    out = dict(
        mean_y=float(df.y.mean()),
        asr=float((df.is_bad & (df.y < cfg.y_bad)).mean()),
        dq=float((df.qstar - df.y).mean()),
        bad_share=float(df.is_bad.mean()),
        ms_per_round=meta["ms_per_round"],
    )

    honest = meta["honest"]
    n_h = max(1, len(honest))
    out["far_alert"] = len(honest & meta["ever_alert_phys"]) / n_h
    out["far_cluster"] = len(honest & meta["ever_susp_phys"]) / n_h
    out["far"] = len(honest & (meta["ever_alert_phys"] | meta["ever_susp_phys"])) / n_h

    # detection latency over bad agents whose harmful phase has a defined start
    starts = {a: s for a, s in meta["bad"].items() if s is not None}
    dls = []
    for a, s in starts.items():
        ts = [t for t in meta["alert_by_phys"].get(a, []) if t >= s]
        if ts:
            dls.append(min(ts) - s)
    out["detect_rate"] = len(dls) / len(starts) if starts else np.nan
    out["dl"] = float(np.mean(dls)) if dls else np.nan

    # recovery time after the earliest *late* attack start (two-faced, drifter, sleeper)
    late = [s for s in starts.values() if s > 0]
    out["rt"] = recovery_time(y, min(late)) if late else np.nan

    out["viol_rate"] = float((df.viol > 0).mean())
    nv = int((df.viol > 0).sum())
    out["recovery_rate"] = float(df.recovered.sum() / nv) if nv else np.nan
    out["n_idents"] = meta["n_idents"]
    return out
