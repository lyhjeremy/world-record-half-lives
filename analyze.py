"""World-record half-lives: how long records stand, and which are vulnerable next.

Pipeline
--------
1. Reconstruct each event's ratified progression as the running best of the
   verified marks, then measure every record's *reign* — the years until the next
   record broke it. The current record's reign is right-censored (still standing).
2. Survival analysis of reigns (lifelines): Kaplan-Meier record half-life + a Cox
   proportional-hazards model for what makes a record fall sooner.
3. Fit an asymptotic progression curve per event (scipy) to estimate each event's
   theoretical limit and how much room is left under the current record.
4. Combine into a vulnerability score for each of the current world records.

Outputs: tables/*.csv and figures/*.png
"""

from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from lifelines import CoxPHFitter, KaplanMeierFitter
from scipy.optimize import curve_fit

ROOT = Path(__file__).resolve().parent
FIG, TAB = ROOT / "figures", ROOT / "tables"
TODAY = pd.Timestamp.today()

LABELS = {
    "100m": "100 m", "800m": "800 m", "1500m": "1500 m", "5000m": "5000 m",
    "10000m": "10,000 m", "marathon": "Marathon", "shot_put": "Shot put",
    "800m_w": "800 m (W)", "high_jump_w": "High jump (W)",
}
INK, GOLD, WINE = "#1a1a2e", "#D4A537", "#7a1f2b"


def fmt_mark(v: float, unit: str) -> str:
    if unit == "dist":
        return f"{v:.2f} m"
    if v < 100:
        return f"{v:.2f} s"
    m, s = divmod(v, 60)
    if m < 60:
        return f"{int(m)}:{s:05.2f}"
    h, m = divmod(int(m), 60)
    return f"{h}:{m:02d}:{int(s):02d}"


# --- 1. reconstruct progressions and record reigns --------------------------
def build_reigns(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    reigns, progressions = [], {}
    for ev, g in df.groupby("event"):
        unit = g["unit"].iloc[0]
        g = g.sort_values("date")
        best, recs = None, []
        for _, r in g.iterrows():
            better = best is None or (r.value < best if unit == "time" else r.value > best)
            if better:
                best = r.value
                recs.append((r.date, r.value))
        recs = pd.DataFrame(recs, columns=["date", "value"]).drop_duplicates("value")
        progressions[ev] = recs
        for i, row in recs.iterrows():
            nxt = recs.date.iloc[recs.index.get_loc(i) + 1] if i != recs.index[-1] else None
            end = nxt if nxt is not None else TODAY
            improv = abs(recs.value.iloc[recs.index.get_loc(i) - 1] - row.value) if recs.index.get_loc(i) > 0 else np.nan
            prev = recs.value.iloc[recs.index.get_loc(i) - 1] if recs.index.get_loc(i) > 0 else row.value
            reigns.append({
                "event": ev, "category": g["category"].iloc[0], "unit": unit,
                "value": row.value, "date_set": row.date,
                "reign_years": (end - row.date).days / 365.25,
                "broken": 0 if nxt is None else 1,           # 0 = still standing (censored)
                "improvement_pct": 100 * improv / prev if not np.isnan(improv) else np.nan,
                "year_set": row.date.year,
            })
    return pd.DataFrame(reigns), progressions


# --- 2. survival analysis ----------------------------------------------------
def survival(reigns: pd.DataFrame):
    km = KaplanMeierFitter().fit(reigns.reign_years, reigns.broken, label="all records")
    median = km.median_survival_time_
    # Era split — the headline shift: modern records stand far longer.
    pre, post = reigns[reigns.year_set < 1980], reigns[reigns.year_set >= 1980]
    km_pre = KaplanMeierFitter().fit(pre.reign_years, pre.broken, label=f"set before 1980 (n={len(pre)})")
    km_post = KaplanMeierFitter().fit(post.reign_years, post.broken, label=f"set 1980+ (n={len(post)})")
    # Cox: what shortens a reign? drop the first record of each event (no improvement_pct).
    cox_df = reigns.dropna(subset=["improvement_pct"]).copy()
    cox_df["modern"] = (cox_df.year_set >= 1980).astype(int)
    cox_df["is_distance"] = cox_df.category.isin(["distance", "middle"]).astype(int)
    cph = CoxPHFitter()
    try:
        cph.fit(cox_df[["reign_years", "broken", "improvement_pct", "modern", "is_distance"]],
                "reign_years", "broken")
    except Exception as e:
        print("  (Cox did not converge:", e, ")")
        cph = None
    return km, median, cph, km_pre, km_post


# --- 3. asymptotic progression fit ------------------------------------------
def fit_asymptote(recs: pd.DataFrame, unit: str):
    """Fit value(t) = A ± B·exp(-C·t). A is the estimated limit under current
    conditions. A is bounded to a physically sane window around the current record
    (a limit can't be slower than today's record, nor implausibly far beyond it)."""
    t = (recs.date - recs.date.min()).dt.days.values / 365.25
    y = recs.value.values.astype(float)
    cur = y.min() if unit == "time" else y.max()
    span = max(y.max() - y.min(), 1e-6)
    if unit == "time":                       # decays DOWN toward the limit A ≤ current
        def f(t, A, B, C): return A + B * np.exp(-C * t)
        bounds = ([0.75 * cur, 0, 1e-3], [cur * 1.0005, 6 * span, 0.5])
        p0 = [0.97 * cur, span, 0.04]
    else:                                    # rises UP toward the limit A ≥ current
        def f(t, A, B, C): return A - B * np.exp(-C * t)
        bounds = ([cur * 0.9995, 0, 1e-3], [1.25 * cur, 6 * span, 0.5])
        p0 = [1.03 * cur, span, 0.04]
    try:
        popt, _ = curve_fit(f, t, y, p0=p0, bounds=bounds, maxfev=30000)
        A = popt[0]
        gap_pct = 100 * abs(cur - A) / cur
        return A, gap_pct, (f, popt, t, y)
    except Exception:
        return np.nan, np.nan, None


def main() -> None:
    FIG.mkdir(exist_ok=True); TAB.mkdir(exist_ok=True)
    df = pd.read_csv(ROOT / "data" / "raw" / "progressions.csv", parse_dates=["date"])
    reigns, progs = build_reigns(df)
    reigns.to_csv(TAB / "record_reigns.csv", index=False)

    km, median, cph, km_pre, km_post = survival(reigns)
    broken = reigns[reigns.broken == 1]
    print(f"records analysed: {len(reigns)}  ({broken.shape[0]} broken, "
          f"{(reigns.broken == 0).sum()} still standing)")
    print(f"median record half-life (Kaplan-Meier, all eras): {median:.1f} years")
    print(f"  pre-1980 half-life:  {km_pre.median_survival_time_:.1f} years")
    print(f"  1980+  half-life:    {km_post.median_survival_time_:.1f} years")
    print(f"mean reign of broken records: {broken.reign_years.mean():.1f} years")

    # Vulnerability, straight from the survival model: the record hazard is
    # front-loaded (half-life ~1.5 yr), so a record that has *already* survived t
    # years has a conditional probability of falling in the next 5 years of
    # 1 − S(t+5)/S(t). No shaky limit extrapolation needed.
    def cond_p5(t: float) -> float:
        St, St5 = float(km.predict(t)), float(km.predict(t + 5))
        return max(0.0, 1 - St5 / St) if St > 0 else 0.0

    rows = []
    for ev, recs in progs.items():
        unit = df[df.event == ev].unit.iloc[0]
        cur = recs.iloc[-1]
        age = (TODAY - cur.date).days / 365.25
        rows.append({"event": ev, "label": LABELS[ev], "unit": unit,
                     "record": fmt_mark(cur.value, unit), "value": cur.value,
                     "set": cur.date.date(), "age_years": age,
                     "p_fall_5yr": cond_p5(age)})
    cur_df = pd.DataFrame(rows).sort_values("p_fall_5yr", ascending=False)
    cur_df.to_csv(TAB / "current_record_vulnerability.csv", index=False)
    print("\nModel-estimated P(record falls within 5 years), given how long it has stood:")
    for _, r in cur_df.iterrows():
        print(f"  {r.label:14s} {r.record:>10s}  set {str(r.set)[:4]}  age {r.age_years:4.1f}y"
              f"  P(fall ≤5yr) = {r.p_fall_5yr:.0%}")

    _figures(km, median, km_pre, km_post, reigns, progs, df, cur_df)
    if cph is not None:
        cph.summary[["coef", "exp(coef)", "p"]].to_csv(TAB / "cox_summary.csv")
        print("\nCox hazard ratios (exp coef > 1 → record falls sooner):")
        print(cph.summary[["exp(coef)", "p"]].round(3).to_string())


def _figures(km, median, km_pre, km_post, reigns, progs, df, cur_df):
    # Fig 1: Kaplan-Meier record survival, split by era
    fig, ax = plt.subplots(figsize=(8, 5))
    km_pre.plot_survival_function(ax=ax, color="#9aa0a6", linewidth=2.2, ci_alpha=0.10)
    km_post.plot_survival_function(ax=ax, color=WINE, linewidth=2.4, ci_alpha=0.12)
    ax.axhline(0.5, color=INK, ls=":", lw=1)
    ax.set_xlabel("years since the record was set")
    ax.set_ylabel("probability the record still stands")
    ax.set_title("A world record survives far longer than it used to", fontweight="bold")
    ax.set_xlim(0, 45)
    ax.legend(frameon=False, title=f"Kaplan-Meier  (overall half-life {median:.1f} yr)")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout(); fig.savefig(FIG / "record_survival.png", dpi=150); plt.close(fig)

    # Fig 2: record progressions with the fitted deceleration curve (data range only)
    marquee = ["100m", "1500m", "marathon", "high_jump_w"]
    fig, axes = plt.subplots(2, 2, figsize=(11, 7.5))
    for ax, ev in zip(axes.ravel(), marquee):
        recs = progs[ev]; unit = df[df.event == ev].unit.iloc[0]
        _, _, fit = fit_asymptote(recs, unit)
        yrs = recs.date.dt.year + recs.date.dt.dayofyear / 365.25
        ax.step(yrs, recs.value, where="post", color=WINE, lw=1.8, label="record progression")
        if fit:
            f, popt, t, _ = fit
            tt = np.linspace(t.min(), t.max(), 200)      # fit over the data range only
            ax.plot(recs.date.dt.year.min() + tt, f(tt, *popt), color=GOLD, ls="--", lw=1.6,
                    label="fitted deceleration")
        n_reigns = (reigns.event == ev).sum()
        ax.set_title(f"{LABELS[ev]}  ({n_reigns} records since {int(yrs.min())})", fontsize=10)
        ax.legend(frameon=False, fontsize=7.5)
        ax.spines[["top", "right"]].set_visible(False)
    fig.suptitle("Record progressions decelerate — each jump smaller than the last", fontweight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.97]); fig.savefig(FIG / "progressions.png", dpi=150); plt.close(fig)

    # Fig 3: survival-model probability each current record falls within 5 years
    fig, ax = plt.subplots(figsize=(8.5, 5))
    d = cur_df.sort_values("p_fall_5yr")
    ax.barh(d.label, d.p_fall_5yr * 100, color=WINE)
    ax.set_xlabel("model P(record falls within 5 years), given years already survived — %")
    ax.set_title("Which world records is the survival model betting against?", fontweight="bold")
    for _, r in d.iterrows():
        ax.text(r.p_fall_5yr * 100 + 0.6, r.label, f"set {str(r.set)[:4]} · age {r.age_years:.0f}y",
                va="center", fontsize=8, color=INK)
    ax.margins(x=0.20)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout(); fig.savefig(FIG / "vulnerability.png", dpi=150); plt.close(fig)
    print(f"\nwrote {len(list(FIG.glob('*.png')))} figures to figures/ and tables to tables/")


if __name__ == "__main__":
    main()
