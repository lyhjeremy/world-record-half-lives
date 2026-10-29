"""World-record half-lives, October 2026 upgrade.

Pipeline (after fetch_data.py, crosscheck_wikipedia.py and validate.py):

1. Reigns: tables/progressions.csv -> record reigns (reigns.py has the rules).
2. Baselines: Kaplan-Meier overall and per event group; a Cox model with the
   proportional-hazards test.
3. Parametric models: exponential, Weibull, log-normal and log-logistic AFT
   (lifelines), compared by AIC; then the main model, a Weibull proportional-
   hazards model with a shared gamma frailty per event, fitted by maximum
   marginal likelihood (scipy). The frailty lets each event have its own pace
   of record-breaking, estimated from its own history and shrunk toward the
   rest.
4. Backtest: refit on what was known at the end of 2010 (and 2000), predict each
   standing record's chance of falling in the next 15 years, score against what
   happened (Brier, AUC, reliability) next to a pooled constant-rate baseline and
   the old Kaplan-Meier method. A rolling version refits at every year end from
   1985 to 2020 with a five-year horizon.
5. Forecast: for every current record, P(falls before the LA 2028 Olympics close
   on 30 July 2028) and P(falls within five years), with 90% intervals from an
   event-level bootstrap.

Outputs go to tables/ and figures/; web/data.json feeds the site.
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from lifelines import (CoxPHFitter, KaplanMeierFitter, LogLogisticAFTFitter, LogNormalAFTFitter,
                       WeibullAFTFitter, ExponentialFitter)
from lifelines.statistics import proportional_hazard_test
from scipy.optimize import minimize
from scipy.special import gammaln

from reigns import build_reigns

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent
FIG, TAB, WEB = ROOT / "figures", ROOT / "tables", ROOT / "web"
TODAY = pd.Timestamp("2026-10-28")          # data checked against World Athletics on this date
LA28_CLOSE = pd.Timestamp("2028-07-30")
YR = 365.25
IMPROVE_CAP = 5.0
N_BOOT = 200
RNG = np.random.default_rng(20261028)

GROUPS = ["sprint", "middle", "distance", "hurdles", "relay", "road", "walk", "jumps", "throws", "combined"]
GROUP_LABEL = {"sprint": "Sprints", "middle": "Middle distance", "distance": "Long distance and steeple",
               "hurdles": "Hurdles", "relay": "Relays", "road": "Marathon", "walk": "Race walk",
               "jumps": "Jumps", "throws": "Throws", "combined": "Combined events"}
EVENT_LABEL = {"100m": "100 m", "200m": "200 m", "400m": "400 m", "800m": "800 m", "1500m": "1500 m",
               "5000m": "5000 m", "10000m": "10,000 m", "marathon": "Marathon", "3000sc": "3000 m steeple",
               "110h": "110 m hurdles", "100h": "100 m hurdles", "400h": "400 m hurdles",
               "hj": "High jump", "pv": "Pole vault", "lj": "Long jump", "tj": "Triple jump",
               "sp": "Shot put", "dt": "Discus", "ht": "Hammer", "jt": "Javelin", "dec": "Decathlon",
               "hep": "Heptathlon", "20kw": "20 km walk", "4x100": "4x100 m relay", "4x400": "4x400 m relay"}
# Model groups: road and walk have 10 and 11 reigns, too few for their own effects.
MODEL_GROUP = {"road": "distance", "walk": "distance"}
BASE = ["women", "improve_pct", "first_in_chain", "self_break",
        "g_middle", "g_distance", "g_hurdles", "g_relay", "g_jumps", "g_throws", "g_combined"]
SET_ERA = ["era_1950_79", "era_1980_99", "era_2000"]
COVARS = BASE[:1] + SET_ERA + BASE[1:]          # first specification: era in which the record was set
CAL_BREAKS = (1950, 1980, 2000)                 # second: calendar period in which the record is at risk


def cal_names(breaks):
    return [f"cal_{b}" for b in breaks]


def split_calendar(df: pd.DataFrame, breaks=CAL_BREAKS) -> pd.DataFrame:
    """Counting-process split of each reign at calendar boundaries, so the hazard can
    depend on the period in which a record is at risk. Only the last piece carries
    the event; the likelihood of a model without these covariates is unchanged."""
    rows = []
    for _, r in df.iterrows():
        start = pd.Timestamp(r.date_set)
        cuts = [(pd.Timestamp(f"{b}-01-01") - start).days / YR for b in breaks]
        edges = [r.entry_years] + [c for c in cuts if r.entry_years < c < r.years] + [r.years]
        for i in range(len(edges) - 1):
            q = r.copy()
            q["entry_years"], q["years"] = edges[i], edges[i + 1]
            q["broken"] = r.broken if i == len(edges) - 2 else 0
            mid = start + pd.Timedelta(days=(edges[i] + edges[i + 1]) / 2 * YR)
            for b, n in zip(breaks, cal_names(breaks)):
                q[n] = float(mid.year >= b and all(mid.year < b2 for b2 in breaks if b2 > b))
            rows.append(q)
    out = pd.DataFrame(rows)
    out["entry_years"] = out.entry_years.clip(lower=0)
    return out


# --------------------------------------------------------------------------- data
def design(r: pd.DataFrame) -> pd.DataFrame:
    d = r.copy()
    d["women"] = (d.sex == "W").astype(float)
    y = pd.to_datetime(d.date_set).dt.year
    d["era_1950_79"] = ((y >= 1950) & (y < 1980)).astype(float)
    d["era_1980_99"] = ((y >= 1980) & (y < 2000)).astype(float)
    d["era_2000"] = (y >= 2000).astype(float)
    d["improve_pct"] = d.improvement_pct.clip(lower=0, upper=IMPROVE_CAP).fillna(0.0)
    d["first_in_chain"] = d.first_in_chain.astype(float)
    d["self_break"] = d.self_break.astype(float)
    mg = d.group.replace(MODEL_GROUP)
    for g in ["middle", "distance", "hurdles", "relay", "jumps", "throws", "combined"]:
        d[f"g_{g}"] = (mg == g).astype(float)
    d["cluster"] = d.sex + "|" + d.event
    return d


def truncate(r: pd.DataFrame, cutoff: pd.Timestamp) -> pd.DataFrame:
    """What was known at `cutoff`: later reigns dropped, open reigns censored there."""
    d = r[pd.to_datetime(r.date_set) <= cutoff].copy()
    start = pd.to_datetime(d.date_set)
    end = pd.to_datetime(d.date_end)
    over = end > cutoff
    d.loc[over, "broken"] = 0
    d.loc[over, "years"] = np.maximum((cutoff - start[over]).dt.days / YR, 1 / YR)
    d = d[d.years > d.entry_years]
    return d


MOM_YEARS = 3.0   # third specification: records set in the event in the last three years


def record_dates(r: pd.DataFrame, cutoff: pd.Timestamp | None = None) -> dict:
    d = r if cutoff is None else r[pd.to_datetime(r.date_set) <= cutoff]
    return {c: np.sort(pd.to_datetime(g.date_set).to_numpy()) for c, g in d.groupby(d.sex + "|" + d.event)}


def momentum(dates: np.ndarray, at: pd.Timestamp) -> float:
    lo = np.datetime64(at - pd.Timedelta(days=MOM_YEARS * YR))
    hi = np.datetime64(at)
    return float(np.log1p(np.sum((dates > lo) & (dates <= hi))))


def pieces(start: pd.Timestamp, a0: float, a1: float):
    """Split ages (a0, a1] of a reign that began on `start` at each 1 January."""
    out, a = [], a0
    while a < a1 - 1e-9:
        cal = start + pd.Timedelta(days=a * YR)
        nxt = pd.Timestamp(f"{cal.year + 1}-01-01")
        b = min(a1, (nxt - start).days / YR)
        if b <= a:
            b = min(a1, a + 1 / YR)
        out.append((a, b, cal))
        a = b
    return out


def split_yearly(df: pd.DataFrame, rec: dict, breaks=CAL_BREAKS) -> pd.DataFrame:
    """Counting-process split at every 1 January, carrying the calendar period and the
    event's recent record count (log1p of records in the last three years)."""
    recs = []
    names = cal_names(breaks)
    for r in df.itertuples(index=False):
        start = pd.Timestamp(r.date_set)
        ps = pieces(start, max(r.entry_years, 0.0), r.years)
        for i, (a, b, cal) in enumerate(ps):
            q = r._asdict()
            q["entry_years"], q["years"] = a, b
            q["broken"] = r.broken if i == len(ps) - 1 else 0
            for bb, n in zip(breaks, names):
                q[n] = float(cal.year >= bb and all(cal.year < b2 for b2 in breaks if b2 > bb))
            q["momentum"] = momentum(rec.get(r.cluster, np.array([], dtype="datetime64[ns]")), cal)
            recs.append(q)
    return pd.DataFrame(recs)


def p_fall_path(model, row: pd.Series, t0: pd.Timestamp, horizon: float, rec: dict, breaks) -> float:
    """P(record falls between t0 and t0+horizon) with momentum decaying along the
    no-new-record path and the calendar period fixed at the latest one."""
    a, rho, b, theta = model._unpack(model.p)
    start = pd.Timestamp(row.date_set)
    age0 = (t0 - start).days / YR
    fut = future_override(breaks)
    dates = rec.get(row.cluster, np.array([], dtype="datetime64[ns]"))
    dates = dates[dates <= np.datetime64(t0)]
    dH = 0.0
    for a0, a1, cal in pieces(start, max(age0, 1 / YR), age0 + horizon):
        ov = dict(fut); ov["momentum"] = momentum(dates, cal)
        xb = float(model.xvec(row, ov) @ b)
        dH += np.exp(a + xb) * (a1 ** rho - a0 ** rho)
    k, rate = model.post.get(row.cluster, (1 / theta, 1 / theta))
    return float(1 - (1 + dH / rate) ** (-k))


# ------------------------------------------------------------- frailty Weibull model
class FrailtyWeibull:
    """h(t | x, Z) = Z * rho * exp(a) * t^(rho-1) * exp(x b), Z ~ Gamma(1/theta, 1/theta) per event."""

    def __init__(self, covars=COVARS, frailty=True):
        self.covars, self.frailty = list(covars), frailty

    def _unpack(self, p):
        k = len(self.covars)
        a, logrho, b = p[0], p[1], p[2:2 + k]
        logtheta = p[2 + k] if self.frailty else -30.0
        return a, np.exp(logrho), b, np.exp(logtheta)

    def _cum(self, t, a, rho, xb):
        return np.exp(a + xb) * t ** rho

    def _negll(self, p, X, t, e, d, cl_idx, n_cl):
        a, rho, b, theta = self._unpack(p)
        xb = X @ b
        H = self._cum(t, a, rho, xb) - self._cum(e, a, rho, xb)
        logh = a + np.log(rho) + (rho - 1) * np.log(t) + xb
        ll = np.sum(d * logh)
        D = np.bincount(cl_idx, weights=d, minlength=n_cl)
        S = np.bincount(cl_idx, weights=H, minlength=n_cl)
        if self.frailty:
            it = 1 / theta
            ll += np.sum(gammaln(it + D) - gammaln(it) + D * np.log(theta) - (it + D) * np.log1p(theta * S))
        else:
            ll -= np.sum(S)
        return -ll

    def fit(self, df: pd.DataFrame, p0=None):
        X = df[self.covars].to_numpy(float)
        t, e, d = df.years.to_numpy(float), df.entry_years.to_numpy(float), df.broken.to_numpy(float)
        cl = df.cluster.astype("category")
        self.clusters = list(cl.cat.categories)
        idx = cl.cat.codes.to_numpy()
        k = len(self.covars)
        if p0 is None:
            p0 = np.r_[np.log(d.sum() / t.sum()), np.log(0.7), np.zeros(k), [np.log(0.3)] if self.frailty else []]
        res = minimize(self._negll, p0, args=(X, t, e, d, idx, len(self.clusters)), method="L-BFGS-B")
        self.p, self.res = res.x, res
        self.loglik = -res.fun
        self.n_params = len(res.x)
        self.aic = 2 * self.n_params - 2 * self.loglik
        a, rho, b, theta = self._unpack(res.x)
        xb = X @ b
        H = self._cum(t, a, rho, xb) - self._cum(e, a, rho, xb)
        self.post = {}
        for i, c in enumerate(self.clusters):
            m = idx == i
            self.post[c] = (1 / theta + d[m].sum(), 1 / theta + H[m].sum())
        return self

    def se(self, df):
        """Standard errors from a numerical Hessian of the negative log-likelihood."""
        X = df[self.covars].to_numpy(float)
        t, e, d = df.years.to_numpy(float), df.entry_years.to_numpy(float), df.broken.to_numpy(float)
        idx = df.cluster.astype("category").cat.codes.to_numpy()
        f = lambda p: self._negll(p, X, t, e, d, idx, len(self.clusters))
        p, n, h = self.p, len(self.p), 1e-4
        Hm = np.zeros((n, n))
        for i in range(n):
            for j in range(i, n):
                pp = p.copy(); pp[i] += h; pp[j] += h; fpp = f(pp)
                pm = p.copy(); pm[i] += h; pm[j] -= h; fpm = f(pm)
                mp = p.copy(); mp[i] -= h; mp[j] += h; fmp = f(mp)
                mm = p.copy(); mm[i] -= h; mm[j] -= h; fmm = f(mm)
                Hm[i, j] = Hm[j, i] = (fpp - fpm - fmp + fmm) / (4 * h * h)
        return np.sqrt(np.clip(np.diag(np.linalg.pinv(Hm)), 0, None))

    def posterior(self, df: pd.DataFrame) -> dict:
        """Gamma posterior (shape, rate) of each event's frailty given its history."""
        a, rho, b, theta = self._unpack(self.p)
        xb = df[self.covars].to_numpy(float) @ b
        H = self._cum(df.years.to_numpy(float), a, rho, xb) - self._cum(df.entry_years.to_numpy(float), a, rho, xb)
        tmp = pd.DataFrame({"c": df.cluster.to_numpy(), "d": df.broken.to_numpy(float), "H": H})
        g = tmp.groupby("c").sum()
        return {c: (1 / theta + r.d, 1 / theta + r.H) for c, r in g.iterrows()}

    def xvec(self, row: pd.Series, override: dict | None = None) -> np.ndarray:
        v = row.reindex(self.covars).fillna(0.0).astype(float)
        if override:
            for k, val in override.items():
                if k in v.index:
                    v[k] = val
        return v.to_numpy(float)

    def p_fall(self, row: pd.Series, age: float, horizon: float, override: dict | None = None) -> float:
        a, rho, b, theta = self._unpack(self.p)
        xb = float(self.xvec(row, override) @ b)
        dH = np.exp(a + xb) * ((age + horizon) ** rho - max(age, 1 / YR) ** rho)
        if not self.frailty:
            return float(1 - np.exp(-dH))
        k, rate = self.post.get(row.cluster, (1 / theta, 1 / theta))
        return float(1 - (1 + dH / rate) ** (-k))

    def pop_survival(self, row: pd.Series, ts: np.ndarray, override: dict | None = None) -> np.ndarray:
        """Population-average survival from 0 (frailty integrated over its prior)."""
        a, rho, b, theta = self._unpack(self.p)
        xb = float(self.xvec(row, override) @ b)
        H = np.exp(a + xb) * ts ** rho
        return np.exp(-H) if not self.frailty else (1 + theta * H) ** (-1 / theta)


# --------------------------------------------------------------------- baselines
def pooled_rate(df):
    return df.broken.sum() / (df.years - df.entry_years).sum()


def km_cond(km: KaplanMeierFitter, age, horizon):
    s0, s1 = float(km.predict(age)), float(km.predict(age + horizon))
    return 0.0 if s0 <= 0 else max(0.0, 1 - s1 / s0)


def brier(p, y):
    return float(np.mean((np.asarray(p) - np.asarray(y)) ** 2))


def auc(p, y):
    p, y = np.asarray(p, float), np.asarray(y, int)
    pos, neg = p[y == 1], p[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    gt = (pos[:, None] > neg[None, :]).sum() + 0.5 * (pos[:, None] == neg[None, :]).sum()
    return float(gt / (len(pos) * len(neg)))


def climatology(r_all: pd.DataFrame, cutoff: pd.Timestamp, H: float) -> float:
    """Naive base rate: share of standing records that fell within H years, over every
    earlier year end whose outcome was already known at the cutoff."""
    fells, n = 0, 0
    for y in range(1950, cutoff.year + 1):
        c = pd.Timestamp(f"{y}-12-31")
        end = c + pd.Timedelta(days=H * YR)
        if end > cutoff:
            break
        st = standing_at(r_all, c)
        n += len(st)
        fells += int(((st.broken == 1) & (pd.to_datetime(st.date_end) <= end)).sum())
    return fells / n if n else float("nan")


def standing_at(r: pd.DataFrame, cutoff: pd.Timestamp) -> pd.DataFrame:
    s = r[(pd.to_datetime(r.date_set) <= cutoff) & (pd.to_datetime(r.date_end) > cutoff)].copy()
    s = s[~s.censor_reason.isin(["hand timing ended 1977", "implement changed"]) | (pd.to_datetime(s.date_end) > cutoff)]
    s = s[s.chain.isin(["main", "auto", "new implement"])]
    return s


def future_override(breaks) -> dict:
    names = cal_names(breaks)
    return {n: float(i == len(names) - 1) for i, n in enumerate(names)}


def backtest(r_all: pd.DataFrame, cutoff: pd.Timestamp, horizon_end: pd.Timestamp, fit_cache=None):
    known = design(truncate(r_all, cutoff))
    fm = FrailtyWeibull().fit(known)
    # second specification: calendar periods with at least ten years of data before the cutoff
    brk = tuple(b for b in CAL_BREAKS if b <= cutoff.year - 10)
    ks = split_calendar(known, brk)
    f2 = FrailtyWeibull(covars=BASE + cal_names(brk)).fit(ks)
    f2.post = f2.posterior(ks)
    fut = future_override(brk)
    # third specification: add the event's recent record count, time-varying
    rec = record_dates(r_all, cutoff)
    ky = split_yearly(known, rec, brk)
    f3 = FrailtyWeibull(covars=BASE + cal_names(brk) + ["momentum"]).fit(ky)
    f3.post = f3.posterior(ky)
    lam = pooled_rate(known)
    km = KaplanMeierFitter().fit(known.years, known.broken, entry=known.entry_years)
    stand = design(standing_at(r_all, cutoff))
    stand["cluster"] = stand.sex + "|" + stand.event
    H = (horizon_end - cutoff).days / YR
    clim = climatology(r_all, cutoff, H)
    rows = []
    for _, s in stand.iterrows():
        age = (cutoff - pd.Timestamp(s.date_set)).days / YR
        fell = int(s.broken == 1 and pd.Timestamp(s.date_end) <= horizon_end)
        rows.append({"sex": s.sex, "event": s.event, "mark": s.mark, "athlete": s.athlete,
                     "date_set": s.date_set, "age_at_cutoff": round(age, 2),
                     "p_model": p_fall_path(f3, s, cutoff, H, rec, brk),
                     "p_model_v2": f2.p_fall(s, age, H, fut), "p_model_v1": fm.p_fall(s, age, H),
                     "p_baseline": 1 - np.exp(-lam * H), "p_clim": clim,
                     "p_km": km_cond(km, age, H), "fell": fell,
                     "fell_on": s.date_end if fell else ""})
    return pd.DataFrame(rows), fm


def score(bt: pd.DataFrame) -> dict:
    out = {"n": len(bt), "fell": int(bt.fell.sum()), "base_rate": float(bt.fell.mean())}
    for k in ["p_model", "p_model_v2", "p_model_v1", "p_baseline", "p_clim", "p_km"]:
        out[f"brier_{k[2:]}"] = brier(bt[k], bt.fell)
        out[f"auc_{k[2:]}"] = auc(bt[k], bt.fell)
    # 90% interval for the Brier gain over the base rate, resampling events
    rng = np.random.default_rng(7)
    ev = (bt.sex + bt.event).to_numpy()
    uniq = np.unique(ev)
    diffs = []
    for _ in range(1000):
        pick = rng.choice(uniq, len(uniq))
        m = np.concatenate([np.flatnonzero(ev == u) for u in pick])
        sub = bt.iloc[m]
        diffs.append(brier(sub.p_clim, sub.fell) - brier(sub.p_model, sub.fell))
    out["brier_gain_vs_clim"] = out["brier_clim"] - out["brier_model"]
    out["brier_gain_lo90"], out["brier_gain_hi90"] = np.quantile(diffs, [0.05, 0.95])
    return out


def reliability(bt: pd.DataFrame, col="p_model", bins=(0, .2, .4, .6, .8, 1.0001)):
    b = pd.cut(bt[col], bins, right=False)
    t = bt.groupby(b, observed=False).agg(n=("fell", "size"), predicted=(col, "mean"), observed=("fell", "mean"))
    t.index = [f"{int(i.left*100)}-{min(100, int(round(i.right*100)))}%" for i in t.index]
    return t.reset_index(names="bin")


# --------------------------------------------------------------------- main
def main() -> None:
    for p in (FIG, TAB, WEB):
        p.mkdir(exist_ok=True)
    prog = pd.read_csv(TAB / "progressions.csv", parse_dates=["date"])
    r = build_reigns(prog, TODAY)
    r.to_csv(TAB / "record_reigns.csv", index=False)
    d = design(r)
    print(f"reigns: {len(d)} ({int(d.broken.sum())} broken, {int((d.broken == 0).sum())} censored, "
          f"{int((d.censor_reason == 'still standing').sum())} standing); events: {d.cluster.nunique()}; "
          f"late entries: {int((d.entry_years > 0).sum())}")

    # ---- Kaplan-Meier
    km = KaplanMeierFitter().fit(d.years, d.broken, entry=d.entry_years)
    km_curves = {"all": km}
    for g in GROUPS:
        s = d[d.group == g]
        km_curves[g] = KaplanMeierFitter().fit(s.years, s.broken, entry=s.entry_years)
    for e0, e1, lab in [(0, 1980, "pre1980"), (1980, 3000, "post1980")]:
        s = d[(d.year_set >= e0) & (d.year_set < e1)]
        km_curves[lab] = KaplanMeierFitter().fit(s.years, s.broken, entry=s.entry_years)
    km_tab = pd.DataFrame([{"curve": k, "n": int(v.event_observed.size), "median_years": v.median_survival_time_}
                           for k, v in km_curves.items()])
    km_tab.to_csv(TAB / "km_medians.csv", index=False)
    print(km_tab.round(2).to_string(index=False))

    # ---- Cox with PH test
    cox_cols = COVARS
    cph = CoxPHFitter(penalizer=0.0)
    cdf = d[cox_cols + ["years", "broken", "entry_years", "cluster"]]
    cph.fit(cdf, "years", "broken", entry_col="entry_years", cluster_col="cluster")
    cs = cph.summary[["exp(coef)", "exp(coef) lower 95%", "exp(coef) upper 95%", "p"]]
    cs.columns = ["hr", "hr_lo95", "hr_hi95", "p"]
    cs.to_csv(TAB / "cox_summary.csv")
    print("\nCox (robust SE by event):\n", cs.round(3).to_string())
    # lifelines has no Schoenfeld residuals with late entry, so the PH test drops the
    # 11 late-entry reigns and refits without entry times.
    cdf2 = cdf[cdf.entry_years == 0].drop(columns=["cluster", "entry_years"])
    cph2 = CoxPHFitter().fit(cdf2, "years", "broken")
    pht = proportional_hazard_test(cph2, cdf2, time_transform="rank")
    ph = pht.summary[["test_statistic", "p"]]
    ph.to_csv(TAB / "ph_test.csv")
    print("\nPH test (rank transform):\n", ph.round(4).to_string())

    # ---- Parametric comparison (no frailty, lifelines) + frailty Weibull
    pdf = d[COVARS + ["years", "broken", "entry_years"]]
    comp = []
    for name, F in [("Weibull AFT", WeibullAFTFitter), ("Log-normal AFT", LogNormalAFTFitter),
                    ("Log-logistic AFT", LogLogisticAFTFitter)]:
        f = F(penalizer=0.0).fit(pdf, "years", "broken", entry_col="entry_years")
        comp.append({"model": name, "log_lik": f.log_likelihood_, "params": len(f.params_), "aic": f.AIC_})
    ex = FrailtyWeibull(covars=[], frailty=False).fit(d)
    # exponential with no covariates: Weibull with rho fixed at 1
    lam = pooled_rate(d)
    ll_exp = float(np.sum(d.broken * np.log(lam)) - lam * np.sum(d.years - d.entry_years))
    comp.insert(0, {"model": "Exponential, no covariates (pooled rate)", "log_lik": ll_exp, "params": 1, "aic": 2 - 2 * ll_exp})
    wpl = FrailtyWeibull(frailty=False).fit(d)
    comp.append({"model": "Weibull PH, no frailty", "log_lik": wpl.loglik, "params": wpl.n_params, "aic": wpl.aic})
    fm1 = FrailtyWeibull().fit(d)
    comp.append({"model": "Weibull PH + gamma frailty per event (era set)", "log_lik": fm1.loglik, "params": fm1.n_params, "aic": fm1.aic})
    ds = split_calendar(d)
    COV2 = BASE + cal_names(CAL_BREAKS)
    fm2 = FrailtyWeibull(covars=COV2).fit(ds)
    fm2.post = fm2.posterior(ds)
    comp.append({"model": "Weibull PH + gamma frailty per event (calendar period at risk)", "log_lik": fm2.loglik, "params": fm2.n_params, "aic": fm2.aic})
    FUT = future_override(CAL_BREAKS)
    REC = record_dates(r)
    ys = split_yearly(d, REC, CAL_BREAKS)
    COV3 = COV2 + ["momentum"]
    fm = FrailtyWeibull(covars=COV3).fit(ys)
    fm.post = fm.posterior(ys)
    comp.append({"model": "Weibull PH + gamma frailty per event (calendar period + recent records)", "log_lik": fm.loglik, "params": fm.n_params, "aic": fm.aic})
    comp = pd.DataFrame(comp)
    comp["delta_aic"] = comp.aic - comp.aic.min()
    comp.to_csv(TAB / "model_comparison.csv", index=False)
    print("\n", comp.round(1).to_string(index=False))

    se = fm.se(ys)
    a, rho, b, theta = fm._unpack(fm.p)
    names = ["log_scale", "log_shape"] + COV3 + ["log_theta"]
    est = pd.DataFrame({"param": names, "estimate": fm.p, "se": se})
    est["hr"] = np.where(est.param.isin(COV3), np.exp(est.estimate), np.nan)
    est["hr_lo95"] = np.where(est.param.isin(COV3), np.exp(est.estimate - 1.96 * est.se), np.nan)
    est["hr_hi95"] = np.where(est.param.isin(COV3), np.exp(est.estimate + 1.96 * est.se), np.nan)
    est.to_csv(TAB / "frailty_model.csv", index=False)
    print(f"\nfrailty Weibull: shape rho = {rho:.3f}, frailty variance theta = {theta:.3f}")
    print(est.round(3).to_string(index=False))
    frail = pd.DataFrame([{"cluster": c, "posterior_mean_frailty": k / rt, "records": int(k - 1 / theta)}
                          for c, (k, rt) in fm.post.items()]).sort_values("posterior_mean_frailty")
    frail.to_csv(TAB / "event_frailty.csv", index=False)

    # ---- Backtests
    end25 = pd.Timestamp("2025-12-31")
    bt10, _ = backtest(r, pd.Timestamp("2010-12-31"), end25)
    bt00, _ = backtest(r, pd.Timestamp("2000-12-31"), pd.Timestamp("2015-12-31"))
    bt10.to_csv(TAB / "backtest_2010.csv", index=False)
    bt00.to_csv(TAB / "backtest_2000.csv", index=False)
    roll = []
    for y in range(1985, 2021):
        c = pd.Timestamp(f"{y}-12-31")
        bt, _ = backtest(r, c, pd.Timestamp(f"{y + 5}-12-31"))
        bt["cutoff"] = y
        roll.append(bt)
    roll = pd.concat(roll, ignore_index=True)
    roll.to_csv(TAB / "backtest_rolling_5yr.csv", index=False)
    summ = pd.DataFrame([{"test": "fit to 2010, predict 2011-2025", **score(bt10)},
                         {"test": "fit to 2000, predict 2001-2015", **score(bt00)},
                         {"test": "rolling: fit to each year end 1985-2020, predict next 5 years", **score(roll)}])
    summ.to_csv(TAB / "backtest_summary.csv", index=False)
    print("\n", summ.round(3).to_string(index=False))
    rel10 = reliability(bt10, bins=(0, .4, .6, .8, 1.0001))
    relroll = reliability(roll)
    relbase = reliability(roll, "p_km")
    rel10.to_csv(TAB / "reliability_2010.csv", index=False)
    relroll.to_csv(TAB / "reliability_rolling.csv", index=False)
    print(rel10.round(3).to_string(index=False)); print(relroll.round(3).to_string(index=False))

    # ---- Forecast with event-bootstrap intervals
    stand = d[d.censor_reason == "still standing"].copy()
    h28 = (LA28_CLOSE - TODAY).days / YR
    stand["age_years"] = [(TODAY - pd.Timestamp(x)).days / YR for x in stand.date_set]
    stand["p_la28"] = [p_fall_path(fm, s, TODAY, h28, REC, CAL_BREAKS) for _, s in stand.iterrows()]
    stand["p_5yr"] = [p_fall_path(fm, s, TODAY, 5.0, REC, CAL_BREAKS) for _, s in stand.iterrows()]
    stand["p_5yr_v2"] = [fm2.p_fall(s, s.age_years, 5.0, FUT) for _, s in stand.iterrows()]
    stand["p_5yr_v1"] = [fm1.p_fall(s, s.age_years, 5.0) for _, s in stand.iterrows()]
    clusters = sorted(d.cluster.unique())
    boots28, boots5 = [], []
    for i in range(N_BOOT):
        pick = RNG.choice(clusters, size=len(clusters), replace=True)
        parts = []
        for j, c in enumerate(pick):
            sub = ys[ys.cluster == c].copy()
            sub["cluster"] = f"{c}#{j}"
            parts.append(sub)
        bd = pd.concat(parts, ignore_index=True)
        try:
            bm = FrailtyWeibull(covars=COV3).fit(bd, p0=fm.p)
        except Exception:
            continue
        # posterior for each real event uses the bootstrap parameters and that event's own data
        bm.post = bm.posterior(ys)
        boots28.append([p_fall_path(bm, s, TODAY, h28, REC, CAL_BREAKS) for _, s in stand.iterrows()])
        boots5.append([p_fall_path(bm, s, TODAY, 5.0, REC, CAL_BREAKS) for _, s in stand.iterrows()])
    B28, B5 = np.array(boots28), np.array(boots5)
    print(f"\nbootstrap fits used: {len(B28)} of {N_BOOT}")
    stand["p_la28_lo"], stand["p_la28_hi"] = np.quantile(B28, 0.05, axis=0), np.quantile(B28, 0.95, axis=0)
    stand["p_5yr_lo"], stand["p_5yr_hi"] = np.quantile(B5, 0.05, axis=0), np.quantile(B5, 0.95, axis=0)
    stand["p_5yr_km"] = [km_cond(km, s.age_years, 5.0) for _, s in stand.iterrows()]
    cur = pd.read_csv(ROOT / "sources" / "wa_current_records_2026-10-28.csv")
    stand = stand.merge(cur[["sex", "event", "dob", "pending_mark", "pending_athlete", "pending_date"]],
                        on=["sex", "event"], how="left")
    stand["label"] = stand.event.map(EVENT_LABEL)
    stand["holder_age_when_set"] = [
        round((pd.Timestamp(s.date_set) - pd.Timestamp(s.dob)).days / YR, 1) if isinstance(s.dob, str) else np.nan
        for _, s in stand.iterrows()]
    wl = stand[["sex", "event", "label", "group", "mark", "athlete", "country", "date_set", "age_years",
                "holder_age_when_set", "p_la28", "p_la28_lo", "p_la28_hi", "p_5yr", "p_5yr_lo", "p_5yr_hi",
                "p_5yr_km", "p_5yr_v1", "p_5yr_v2", "pending_mark", "pending_athlete", "pending_date"]].sort_values("p_la28", ascending=False)
    wl.to_csv(TAB / "watchlist.csv", index=False)
    print(wl[["sex", "label", "mark", "date_set", "age_years", "p_la28", "p_la28_lo", "p_la28_hi", "p_5yr"]]
          .round(3).to_string(index=False))

    relv1 = reliability(roll, "p_model_v1")
    relv1.to_csv(TAB / "reliability_rolling_v1.csv", index=False)
    print(relv1.round(3).to_string(index=False))
    _figures(d, km_curves, fm, bt10, relroll, wl)
    _web(d, km_curves, fm, est, comp, cs, ph, summ, bt10, bt00, rel10, relroll, relbase, wl, frail, FUT, relv1)


def _figures(d, kmc, fm, bt10, relroll, wl):
    ink, c1, c2, mute = "#1C1E1B", "#A0362B", "#2F5E3E", "#767A70"
    fig, ax = plt.subplots(figsize=(8.5, 5))
    for g, col in [("sprint", c1), ("jumps", c2), ("throws", mute), ("middle", ink)]:
        k = kmc[g]
        ax.step(k.survival_function_.index, k.survival_function_.iloc[:, 0], where="post", color=col, lw=2,
                label=f"{GROUP_LABEL[g]} (median {k.median_survival_time_:.1f} yr)")
    ax.axhline(0.5, color=mute, ls=":", lw=1)
    ax.set_xscale("symlog", linthresh=1); ax.set_xlim(0, 45)
    ax.set_xticks([0, 0.5, 1, 2, 5, 10, 20, 40]); ax.set_xticklabels(["0", "0.5", "1", "2", "5", "10", "20", "40"])
    ax.set_xlabel("years since the record was set (log scale past 1 year)")
    ax.set_ylabel("share of records still standing")
    ax.set_title("Kaplan-Meier survival of world records, four event groups")
    ax.legend(frameon=False, fontsize=9); ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout(); fig.savefig(FIG / "survival_by_group.png", dpi=150); plt.close(fig)

    fig, ax = plt.subplots(figsize=(5.5, 5.2))
    ax.plot([0, 1], [0, 1], color=mute, ls=":", lw=1)
    ax.plot(relroll.predicted, relroll.observed, "o-", color=c1, label="frailty Weibull, rolling 5-yr")
    for _, rr in relroll.iterrows():
        ax.annotate(f"n={rr.n}", (rr.predicted, rr.observed), textcoords="offset points", xytext=(6, -10), fontsize=8)
    ax.set_xlabel("predicted chance of falling within 5 years"); ax.set_ylabel("share that fell")
    ax.set_title("Backtest reliability, 1985-2020 cutoffs")
    ax.spines[["top", "right"]].set_visible(False); ax.legend(frameon=False, fontsize=9)
    fig.tight_layout(); fig.savefig(FIG / "backtest_reliability.png", dpi=150); plt.close(fig)

    w = wl.sort_values("p_la28")
    fig, ax = plt.subplots(figsize=(8.5, 11))
    lab = [f"{x.label} ({x.sex}) {x.mark}" for _, x in w.iterrows()]
    ax.barh(lab, w.p_la28 * 100, color=[c1 if s == "M" else c2 for s in w.sex])
    ax.errorbar(w.p_la28 * 100, lab, xerr=[(w.p_la28 - w.p_la28_lo) * 100, (w.p_la28_hi - w.p_la28) * 100],
                fmt="none", ecolor=ink, lw=0.8)
    ax.set_xlabel("chance the record falls before the LA 2028 Olympics close (%), 90% interval")
    ax.tick_params(axis="y", labelsize=8); ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout(); fig.savefig(FIG / "watchlist.png", dpi=150); plt.close(fig)


def pop_curve(model, row, ts, fut):
    """Population-average survival for a record set in the latest calendar period in an
    event with no other record in the previous three years (momentum log 2 while the
    record itself is inside the window, then 0)."""
    a, rho, b, theta = model._unpack(model.p)
    ov1 = dict(fut); ov1["momentum"] = float(np.log(2))
    ov0 = dict(fut); ov0["momentum"] = 0.0
    x1 = np.exp(a + float(model.xvec(row, ov1) @ b)); x0 = np.exp(a + float(model.xvec(row, ov0) @ b))
    H = np.where(ts <= MOM_YEARS, x1 * ts ** rho, x1 * MOM_YEARS ** rho + x0 * (ts ** rho - MOM_YEARS ** rho))
    return (1 + theta * H) ** (-1 / theta)


def _web(d, kmc, fm, est, comp, cs, ph, summ, bt10, bt00, rel10, relroll, relbase, wl, frail, FUT, relv1):
    def steps(k, tmax=60):
        sf = k.survival_function_
        pts = [[round(float(t), 4), round(float(s), 4)] for t, s in zip(sf.index, sf.iloc[:, 0]) if t <= tmax]
        return pts
    ts = np.r_[np.linspace(0.01, 2, 40), np.linspace(2.1, 60, 120)]
    model_curves = {}
    for g in GROUPS:
        s = d[d.group == g]
        # population-average model curve for a typical record of the group set in 2000 or later
        rows = s[s.year_set >= 1980] if (s.year_set >= 1980).any() else s
        avg = np.mean([pop_curve(fm, rw, ts, FUT) for _, rw in rows.iterrows()], axis=0)
        model_curves[g] = [[round(float(t), 3), round(float(v), 4)] for t, v in zip(ts, avg)]
    curves = {g: {"label": GROUP_LABEL[g], "n": int((d.group == g).sum()),
                  "broken": int(d[d.group == g].broken.sum()),
                  "median": None if np.isinf(kmc[g].median_survival_time_) else round(float(kmc[g].median_survival_time_), 2),
                  "km": steps(kmc[g]), "model": model_curves[g]} for g in GROUPS}
    curves["all"] = {"label": "All events", "n": len(d), "broken": int(d.broken.sum()),
                     "median": round(float(kmc["all"].median_survival_time_), 2), "km": steps(kmc["all"])}
    reigns = d.copy()
    reigns["yset"] = pd.to_datetime(reigns.date_set).dt.year + pd.to_datetime(reigns.date_set).dt.dayofyear / 366
    standing = {g: [[r.label if False else EVENT_LABEL[r.event] + (" (W)" if r.sex == "W" else ""), r.mark,
                     round((TODAY - pd.Timestamp(r.date_set)).days / YR, 2)]
                    for _, r in d[(d.group == g) & (d.censor_reason == "still standing")].iterrows()] for g in GROUPS}
    js = lambda df: json.loads(df.to_json(orient="records", date_format="iso"))
    data = {
        "generated": str(TODAY.date()),
        "counts": {"reigns": len(d), "broken": int(d.broken.sum()), "events": int(d.cluster.nunique()),
                   "standing": int((d.censor_reason == "still standing").sum())},
        "curves": curves, "standing": standing,
        "model": {"shape": float(fm._unpack(fm.p)[1]), "theta": float(fm._unpack(fm.p)[3]), "params": js(est.round(4))},
        "comparison": js(comp.round(2)), "cox": js(cs.round(4).reset_index(names="covariate")),
        "ph": js(ph.round(4).reset_index(names="covariate")),
        "backtest": {"summary": js(summ.round(4)), "y2010": js(bt10.round(4)), "y2000": js(bt00.round(4)),
                     "rel2010": js(rel10.round(4)), "rel_rolling": js(relroll.round(4)), "rel_rolling_km": js(relbase.round(4)),
                     "rel_rolling_v1": js(relv1.round(4))},
        "watchlist": js(wl.round(4)), "frailty": js(frail.round(4)),
    }
    (WEB / "data.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    print(f"wrote web/data.json ({(WEB / 'data.json').stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
