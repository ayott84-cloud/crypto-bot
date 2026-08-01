"""Module 2 Phase S2 — multiple-testing haircuts.

The crypto module learned the expensive way that a backtest window can
be a favorable slice: the scalp sleeve validated at PF 1.54 on one year
and was parked at PF 0.95 once the full 2.85 years were measured. These
two statistics are the cheap, quantitative version of that lesson.

DEFLATED SHARPE RATIO — Bailey & Lopez de Prado (2014), "The Deflated
Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting and
Non-Normality". Run N specifications and the best one looks brilliant
by construction. DSR asks: given N trials, and given this series'
skewness, kurtosis and length, what is the probability the TRUE Sharpe
exceeds zero? Module 2's dual-momentum sleeve is an ensemble over many
lookbacks, so an undeflated Sharpe there is not evidence of anything.

PROBABILITY OF BACKTEST OVERFITTING — Bailey, Borwein, Lopez de Prado &
Zhu, via Combinatorially Symmetric Cross-Validation. Split the trial
matrix into in-sample/out-of-sample halves every possible way; count how
often the in-sample winner lands below median out-of-sample. PBO near
0.5 means the selection procedure carries no information at all.

Pure functions, numpy only. No I/O.
"""

from __future__ import annotations

import math
from itertools import combinations
from typing import Optional, Sequence

import numpy as np

# Sharpe is capped rather than allowed to run to infinity on a
# zero-variance series (the same convention the crypto metrics use for
# profit factor).
SHARPE_CAP = 999.0

_EULER_MASCHERONI = 0.5772156649015329

# DSR is a probability that the true Sharpe > 0. 0.95 is the
# conventional bar and matches the project's habit of pre-registering
# thresholds rather than choosing them after seeing the number.
DSR_PASS = 0.95


def _phi(x: float) -> float:
    """Standard normal CDF."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _phi_inv(p: float) -> float:
    """Standard normal quantile (Acklam's rational approximation).

    Accurate to ~1e-9 in the relevant range, which is far tighter than
    the uncertainty in any Sharpe we feed it.
    """
    if not 0.0 < p < 1.0:
        raise ValueError(f"p must be in (0,1), got {p}")
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
          1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
          6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
          -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
          3.754408661907416e+00]
    p_low, p_high = 0.02425, 1 - 0.02425
    if p < p_low:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > p_high:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
           (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


def sharpe(returns: Sequence[float]) -> float:
    """Per-observation Sharpe (NOT annualized).

    Annualization belongs at the reporting layer where the periods-per-
    year convention is explicit — see metrics.annualized_sharpe.
    """
    r = np.asarray(list(returns), dtype=float)
    if r.size < 2:
        return 0.0
    sd = r.std(ddof=1)
    if sd == 0:
        return SHARPE_CAP if r.mean() > 0 else (-SHARPE_CAP if r.mean() < 0
                                                  else 0.0)
    return float(r.mean() / sd)


def expected_max_sharpe(n_trials: int, trial_sharpe_var: float = 1.0) -> float:
    """E[max Sharpe] across N independent trials under a zero-true-Sharpe
    null. This is the bar an observed Sharpe must clear to mean anything
    after a search."""
    n = max(1, int(n_trials))
    if n == 1 or trial_sharpe_var <= 0:
        # One trial means no selection happened, so there is nothing to
        # deflate. Zero dispersion across trials means the same.
        return 0.0
    v = math.sqrt(max(trial_sharpe_var, 1e-12))
    g = _EULER_MASCHERONI
    return v * ((1 - g) * _phi_inv(1 - 1.0 / n)
                 + g * _phi_inv(1 - 1.0 / (n * math.e)))


def deflated_sharpe(returns: Sequence[float], n_trials: int = 1,
                      trial_sharpe_var: float = 1.0,
                      benchmark_sharpe: Optional[float] = None,
                      trial_sharpes: Optional[Sequence[float]] = None) -> dict:
    """P(true Sharpe > benchmark), corrected for selection bias and
    non-normality.

    PREFER `trial_sharpes`: the actual Sharpes the sweep produced. Both
    the trial COUNT and their DISPERSION then come from the data, which
    is what Bailey & Lopez de Prado specify.

    THE SCALE TRAP (found on Module 2's first real run, Aug 1 2026):
    `trial_sharpe_var` defaults to 1.0, which is only sane if Sharpes
    are expressed in annualized units. These are PER-TRADE Sharpes of
    order 0.08, so a variance of 1.0 puts the expected-max bar at 1.05
    and EVERY strategy scores exactly 0.000 — ten different sleeves all
    "failing" identically, which looks like rigor and is actually a
    broken instrument. Passing the observed trial Sharpes makes the bar
    commensurate with the thing it is judging.

    Non-normality matters and is why a plain t-test will not do:
    negative skew and fat tails both inflate an observed Sharpe, and
    equity strategies whose whole job is drawdown compression have
    exactly that shape.
    """
    if trial_sharpes is not None:
        ts = [float(s) for s in trial_sharpes
               if s is not None and abs(float(s)) < SHARPE_CAP]
        n_trials = max(1, len(ts))
        trial_sharpe_var = (float(np.var(ts, ddof=1)) if len(ts) > 1 else 0.0)
    r = np.asarray(list(returns), dtype=float)
    blank = {"dsr": 0.0, "sharpe": 0.0, "sr0": 0.0, "skew": 0.0,
              "kurtosis": 0.0, "n": int(r.size), "n_trials": int(n_trials),
              "passes": False}
    if r.size < 3:
        return blank

    sr = sharpe(r)
    if abs(sr) >= SHARPE_CAP:
        return {**blank, "sharpe": sr, "dsr": 1.0 if sr > 0 else 0.0,
                 "passes": sr > 0}

    t = r.size
    sd = r.std(ddof=1)
    z = (r - r.mean()) / sd
    skew = float((z ** 3).mean())
    kurt = float((z ** 4).mean())            # non-excess

    sr0 = (benchmark_sharpe if benchmark_sharpe is not None
            else expected_max_sharpe(n_trials, trial_sharpe_var))

    denom_sq = 1.0 - skew * sr + ((kurt - 1.0) / 4.0) * (sr ** 2)
    if denom_sq <= 0:
        # The variance estimate has broken down (extreme tails); refuse
        # to emit a confident number rather than returning a wrong one.
        return {**blank, "sharpe": sr, "sr0": sr0, "skew": skew,
                 "kurtosis": kurt,
                 "note": "non-normality term non-positive; DSR undefined"}

    stat = (sr - sr0) * math.sqrt(t - 1) / math.sqrt(denom_sq)
    dsr = _phi(stat)
    return {"dsr": round(dsr, 6), "sharpe": round(sr, 6),
             "sr0": round(sr0, 6), "skew": round(skew, 4),
             "kurtosis": round(kurt, 4), "n": int(t),
             "n_trials": int(n_trials), "passes": bool(dsr >= DSR_PASS)}


def pbo_cscv(returns_matrix, n_splits: int = 8,
               metric=None) -> dict:
    """Probability of Backtest Overfitting via CSCV.

    returns_matrix: (T observations, N strategies).

    Splits T into n_splits contiguous blocks, then for EVERY way of
    choosing half the blocks as in-sample, picks the IS winner and finds
    its rank among all strategies out-of-sample. PBO is the share of
    those combinations where the IS winner lands in the bottom half OOS.

    Interpretation: ~0 means selection generalizes; ~0.5 means the
    procedure is choosing noise and the "best" strategy is an artifact
    of the split you happened to look at.
    """
    m = np.asarray(returns_matrix, dtype=float)
    if m.ndim != 2:
        raise ValueError("returns_matrix must be 2-D (T observations, N strategies)")
    if n_splits % 2 != 0:
        raise ValueError(f"n_splits must be even, got {n_splits}")
    t, n = m.shape
    if n < 2:
        return {"pbo": None, "n_combinations": 0,
                 "note": "PBO needs at least 2 strategies to rank"}
    if t < n_splits * 2:
        return {"pbo": None, "n_combinations": 0,
                 "note": f"need >= {n_splits * 2} observations, got {t}"}

    score = metric or (lambda col: sharpe(col))
    blocks = np.array_split(np.arange(t), n_splits)
    half = n_splits // 2

    logits, oos_ranks, below = [], [], 0
    combos = list(combinations(range(n_splits), half))
    for pick in combos:
        is_idx = np.concatenate([blocks[b] for b in pick])
        oos_idx = np.concatenate([blocks[b] for b in range(n_splits)
                                    if b not in pick])
        is_scores = np.array([score(m[is_idx, j]) for j in range(n)])
        oos_scores = np.array([score(m[oos_idx, j]) for j in range(n)])
        best = int(np.argmax(is_scores))
        # Relative rank of the IS winner among OOS scores, in (0,1).
        rank = float((oos_scores < oos_scores[best]).sum()) / n
        # 1-based rank / (N+1) — the standard CSCV mapping, and the one
        # the random_baseline below is computed against.
        w = (int((oos_scores < oos_scores[best]).sum()) + 1) / (n + 1.0)
        logits.append(math.log(w / (1.0 - w)))
        oos_ranks.append(rank)
        if w <= 0.5:
            below += 1

    # Random-selection baseline. With few specs the rank grid is coarse
    # and random picking does NOT give 0.5: ranks are 1..N, w =
    # rank/(N+1), and PBO counts w <= 0.5, so N=5 gives 0.6. Comparing a
    # raw PBO against a remembered "0.5 means noise" would misread the
    # result, so the baseline ships alongside it.
    baseline = sum(1 for k in range(1, n + 1)
                    if k / (n + 1) <= 0.5) / float(n)
    pbo = below / len(combos)
    return {
        "pbo": round(pbo, 4),
        "random_baseline": round(baseline, 4),
        "excess_over_baseline": round(pbo - baseline, 4),
        "n_combinations": len(combos),
        "n_strategies": int(n),
        "n_observations": int(t),
        "median_oos_rank": round(float(np.median(oos_ranks)), 4),
        "median_logit": round(float(np.median(logits)), 4),
    }
