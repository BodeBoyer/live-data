"""Robust summary statistics for a bag of listing prices."""
from __future__ import annotations

import statistics
from typing import Iterable


def percentile(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        raise ValueError("empty")
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = (len(sorted_vals) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def trim_outliers(values: Iterable[float]) -> list[float]:
    """Drop values outside 1.5*IQR. Needs >= 8 points to bother; otherwise returns input sorted."""
    vals = sorted(float(v) for v in values if v is not None and v > 0)
    if len(vals) < 8:
        return vals
    q1, q3 = percentile(vals, 0.25), percentile(vals, 0.75)
    iqr = q3 - q1
    lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    return [v for v in vals if lo <= v <= hi]


def summarize(values: Iterable[float]) -> dict:
    vals = trim_outliers(values)
    if not vals:
        return {"n": 0, "median": None, "p25": None, "p75": None, "low": None, "high": None}
    return {
        "n": len(vals),
        "median": round(statistics.median(vals), 2),
        "p25": round(percentile(vals, 0.25), 2),
        "p75": round(percentile(vals, 0.75), 2),
        "low": round(vals[0], 2),
        "high": round(vals[-1], 2),
    }
