"""Turn the ratified progression list into record reigns (survival times).

The rules are written out in docs/data_decisions.md. In short:

* A reign starts when a record is set and ends when a strictly better record is
  ratified. Equalling marks do not end a reign; they are counted on the reign.
* Hand-timed and automatically timed records (events up to 400 m, hurdles and
  sprint relays) are two chains. The hand chain is censored on 1977-01-01, when
  World Athletics stopped accepting hand times for these events. Auto records set
  before 1975-01-01 (the first year separate auto records were accepted) enter
  the risk set late, on that date (left truncation).
* Javelin: the old-implement chain is censored on the day the new specification
  took effect (men 1986-04-01, women 1999-04-01). The four men's marks made with
  serrated-tail javelins in 1990-91 were deleted in 1991 and are dropped, so
  Backley's 89.58 reign runs to his 91.46 in January 1992.
* Shinnick's 8.33 long jump (1963), added to the list in 2021 with no deletion
  of later marks, was not a record at the time and is dropped.
* Yards marks (220y, 440y, 880y, 4x110y) are compared after scaling by the
  distance ratio. A listed mark that does not beat the standing record on that
  basis is kept in the table but does not end a reign.
* Combined events: every listed non-equal mark ends the reign (the scoring tables
  changed, so marks are not comparable across tables). Improvement is measured on
  the 1985 tables where the handbook gives them.
* Durations under one day are set to one day (two records on the same day).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

HAND_END = pd.Timestamp("1977-01-01")
AUTO_START = pd.Timestamp("1975-01-01")
JT_SWITCH = {"M": pd.Timestamp("1986-04-01"), "W": pd.Timestamp("1999-04-01")}
LOWER = {"sprint", "middle", "distance", "hurdles", "relay", "walk", "road"}
YARD_RATIO = {"200m": 200 / 201.168, "400m": 400 / 402.336, "800m": 800 / 804.672,
              "4x100": 400 / 402.336, "4x400": 1600 / 1609.344}
MIN_YEARS = 1 / 365.25


def comparable(row) -> float:
    v = float(row["value"])
    if isinstance(row["mark"], str) and row["mark"].endswith("y") and row["event"] in YARD_RATIO:
        v *= YARD_RATIO[row["event"]]
    return v


def chains(g: pd.DataFrame) -> list[tuple[str, pd.DataFrame]]:
    """Split one event's listed rows into the chains described above."""
    sex, ev = g["sex"].iloc[0], g["event"].iloc[0]
    g = g.sort_values("order")
    out = []
    if ev == "jt":
        old = g[g.series == "main"]
        new = g[g.series == "newspec"].copy()
        if sex == "M":
            marker_order = g.loc[g.note == "serrated_reversion", "order"]
            if len(marker_order):
                mo = marker_order.iloc[0]
                backley = new[(new.value == 89.58)].order.iloc[0]
                new = new[~((new.order > backley) & (new.order < mo))]
        out += [("old implement", old), ("new implement", new)]
        return out
    if (g.series == "auto").any():
        out += [("hand", g[g.series == "main"]), ("auto", g[g.series == "auto"])]
    else:
        out.append(("main", g[g.series.isin(["main", "newspec"])]))
    return out


def chain_reigns(rows: pd.DataFrame, kind: str, today: pd.Timestamp) -> list[dict]:
    rows = rows[rows.series != "marker"].copy()
    rows = rows[~((rows.event == "lj") & (rows.sex == "M") & (rows.athlete.str.contains("Shinnick", na=False)))]
    if rows.empty:
        return []
    group = rows["group"].iloc[0]
    lower = group in LOWER
    combined = group == "combined"
    sex, ev = rows["sex"].iloc[0], rows["event"].iloc[0]
    end_cap = {"hand": HAND_END, "old implement": JT_SWITCH.get(sex)}.get(kind, today)

    recs = []          # reign starters
    for _, r in rows.iterrows():
        if r.date is pd.NaT or pd.isna(r.date) or r.date > end_cap:
            continue
        cv = comparable(r)
        if not recs:
            recs.append({"row": r, "cv": cv, "equals": 0})
            continue
        cur = recs[-1]
        if r.value == cur["row"].value and r.mark.rstrip("y+") == cur["row"].mark.rstrip("y+"):
            cur["equals"] += 1
            continue
        better = (cv < cur["cv"]) if lower else (cv > cur["cv"])
        if combined or better:
            recs.append({"row": r, "cv": cv, "equals": 0})
        else:
            cur.setdefault("not_better", []).append(f"{r.mark} {r.athlete} {r.date.date()}")

    out = []
    for i, rec in enumerate(recs):
        r = rec["row"]
        nxt = recs[i + 1]["row"] if i + 1 < len(recs) else None
        end = nxt.date if nxt is not None else end_cap
        broken = int(nxt is not None)
        start = r.date
        entry = 0.0
        if kind == "auto" and start < AUTO_START:
            entry = (AUTO_START - start).days / 365.25
        prev = recs[i - 1] if i > 0 else None
        if prev is not None:
            if combined:
                a = pd.to_numeric(prev["row"].get("auto"), errors="coerce")
                b = pd.to_numeric(r.get("auto"), errors="coerce")
                pv = a if pd.notna(a) else prev["row"].value
                nv = b if pd.notna(b) else r.value
            else:
                pv, nv = prev["cv"], rec["cv"]
            improvement = 100 * abs(pv - nv) / pv
            if combined and ((nv - pv) < 0):
                improvement = -improvement
        else:
            improvement = np.nan
        dur = max((end - start).days / 365.25, MIN_YEARS)
        out.append({
            "sex": sex, "event": ev, "group": group, "chain": kind, "mark": r.mark,
            "value": r.value, "athlete": r.athlete, "country": r.country, "date_set": start.date(),
            "date_end": end.date(), "years": round(dur, 4), "broken": broken,
            "entry_years": round(entry, 4), "equalled": rec["equals"],
            "improvement_pct": improvement,
            "self_break": int(prev is not None and prev["row"].athlete == r.athlete),
            "first_in_chain": int(prev is None),
            "indoor": bool(r.get("indoor")) if pd.notna(r.get("indoor")) else False,
            "broken_by": None if nxt is None else f"{nxt.mark} {nxt.athlete}",
            "not_better_listed": "; ".join(rec.get("not_better", [])),
            "censor_reason": ("" if broken else
                              "hand timing ended 1977" if kind == "hand" else
                              "implement changed" if kind == "old implement" else "still standing"),
        })
    return out


def build_reigns(prog: pd.DataFrame, today: pd.Timestamp) -> pd.DataFrame:
    prog = prog.copy()
    prog["date"] = pd.to_datetime(prog["date"])
    rows = []
    for (_, _), g in prog.groupby(["sex", "event"], sort=False):
        for kind, ch in chains(g):
            rows += chain_reigns(ch, kind, today)
    df = pd.DataFrame(rows)
    df["year_set"] = pd.to_datetime(df.date_set).dt.year
    return df
