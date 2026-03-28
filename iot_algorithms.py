"""
IoT telemetry enrichment: algorithms run on every numeric field in the incoming patch,
not on a fixed list of variable names. Reserved internal keys (starting with _) are skipped.

Optional moist-air metrics (dew point, heat index) resolve temperature + humidity from
common alias names so you are not tied to a single naming convention.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Tuple

# Common aliases — first match in merged state wins (any key can still get delta/EMA/z).
_TEMP_ALIASES: Tuple[str, ...] = (
    "temperature",
    "temp",
    "t_c",
    "t",
    "air_temp",
    "ambient_temp",
    "dht_temp",
)
_RH_ALIASES: Tuple[str, ...] = (
    "humidity",
    "rh",
    "relative_humidity",
    "h",
    "r_h",
    "dht_humidity",
)

ZSCORE_ALERT_ABS = 3.0
_HISTORY_MAX_LEN = 12


def _to_float(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _merged_view(patch: Mapping[str, Any], prev: Mapping[str, Any]) -> Dict[str, Any]:
    """Shallow merge for lookups; skips internal blobs."""
    m: Dict[str, Any] = dict(prev)
    for k, v in patch.items():
        if k.startswith("_"):
            continue
        m[k] = v
    return m


def _first_resolved_numeric(
    merged: Mapping[str, Any], aliases: Tuple[str, ...]
) -> Optional[Tuple[str, float]]:
    for name in aliases:
        if name not in merged:
            continue
        x = _to_float(merged.get(name))
        if x is not None:
            return name, x
    return None


def dew_point_celsius(temp_c: float, relative_humidity_pct: float) -> Optional[float]:
    if relative_humidity_pct <= 0 or relative_humidity_pct > 100:
        return None
    a, b = 17.27, 237.7
    alpha = ((a * temp_c) / (b + temp_c)) + math.log(relative_humidity_pct / 100.0)
    return (b * alpha) / (a - alpha)


def heat_index_celsius_approx(temp_c: float, rh_pct: float) -> Optional[float]:
    if rh_pct < 0 or rh_pct > 100:
        return None
    tf = temp_c * 9.0 / 5.0 + 32.0
    hi_f = (
        -42.379
        + 2.04901523 * tf
        + 10.14333127 * rh_pct
        - 0.22475541 * tf * rh_pct
        - 6.83783e-3 * tf * tf
        - 5.481717e-2 * rh_pct * rh_pct
        + 1.22874e-3 * tf * tf * rh_pct
        + 8.5282e-4 * tf * rh_pct * rh_pct
        - 1.99e-6 * tf * tf * rh_pct * rh_pct
    )
    if hi_f < 80:
        return temp_c
    return (hi_f - 32.0) * 5.0 / 9.0


def rate_of_change(current: float, previous: Optional[float]) -> Optional[float]:
    if previous is None:
        return None
    return current - previous


def exponential_smoothing_step(
    current: float, previous_ema: Optional[float], alpha: float = 0.3
) -> float:
    if previous_ema is None:
        return current
    return alpha * current + (1.0 - alpha) * previous_ema


def simple_zscore(value: float, history: List[float]) -> Optional[float]:
    if len(history) < 2:
        return None
    mean = sum(history) / len(history)
    var = sum((x - mean) ** 2 for x in history) / len(history)
    std = math.sqrt(var) if var > 0 else 0.0
    if std < 1e-9:
        return None
    return (value - mean) / std


def _numeric_keys_in_patch(patch: Mapping[str, Any]) -> List[str]:
    keys: List[str] = []
    for k, v in patch.items():
        if k.startswith("_"):
            continue
        if _to_float(v) is not None:
            keys.append(k)
    return keys


def compute_derived_telemetry(
    new_payload: Dict[str, Any],
    previous_merged_state: Dict[str, Any],
    *,
    ema_alpha: float = 0.35,
    zscore_alert_threshold: float = ZSCORE_ALERT_ABS,
) -> Dict[str, Any]:
    """
    Derive metrics for every numeric key present in the incoming patch.
    Uses previous merged telemetry for deltas, EMA state, and rolling history.
    """
    prev_derived: Dict[str, Any] = {}
    raw_pd = previous_merged_state.get("_iot_derived")
    if isinstance(raw_pd, dict):
        prev_per = raw_pd.get("per_key")
        if isinstance(prev_per, dict):
            prev_derived = prev_per

    clean_new = {k: v for k, v in new_payload.items() if not k.startswith("_")}

    merged = _merged_view(clean_new, previous_merged_state)

    out: Dict[str, Any] = {
        "algorithms_version": 2,
        "per_key": {},
    }

    temp_res = _first_resolved_numeric(merged, _TEMP_ALIASES)
    rh_res = _first_resolved_numeric(merged, _RH_ALIASES)
    if temp_res and rh_res:
        temp_c, rh_pct = temp_res[1], rh_res[1]
        moist: Dict[str, Any] = {
            "temp_field": temp_res[0],
            "rh_field": rh_res[0],
        }
        dp = dew_point_celsius(temp_c, rh_pct)
        if dp is not None:
            moist["dew_point_c"] = round(dp, 2)
        hi = heat_index_celsius_approx(temp_c, rh_pct)
        if hi is not None:
            moist["heat_index_c"] = round(hi, 2)
        out["moist_air"] = moist

    raw_hist = previous_merged_state.get("_iot_history")
    hist: Dict[str, List[float]] = {}
    if isinstance(raw_hist, dict):
        for k, v in raw_hist.items():
            if isinstance(v, list):
                hist[k] = [float(x) for x in v if _to_float(x) is not None]

    per_key: Dict[str, Dict[str, Any]] = {}
    thresholds: Dict[str, Any] = {}

    for metric in _numeric_keys_in_patch(clean_new):
        cur = _to_float(clean_new[metric])
        if cur is None:
            continue

        prev_val = _to_float(previous_merged_state.get(metric))
        roc = rate_of_change(cur, prev_val)

        pk: Dict[str, Any] = {}
        if roc is not None:
            pk["delta"] = round(roc, 4)

        prev_row = prev_derived.get(metric)
        prev_ema = None
        if isinstance(prev_row, dict):
            prev_ema = _to_float(prev_row.get("ema"))
        pk["ema"] = round(
            exponential_smoothing_step(cur, prev_ema, alpha=ema_alpha), 4
        )

        series = list(hist.get(metric) or [])
        series.append(cur)
        series = series[-_HISTORY_MAX_LEN:]
        hist[metric] = series

        z = simple_zscore(cur, series[:-1]) if len(series) > 2 else None
        if z is not None:
            pk["zscore_vs_recent"] = round(z, 3)
            if abs(z) >= zscore_alert_threshold:
                thresholds[f"{metric}_statistical_spike"] = round(z, 3)

        per_key[metric] = pk

    out["per_key"] = per_key
    out["_history_tail"] = hist
    out["thresholds"] = thresholds
    return out
