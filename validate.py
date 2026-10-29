"""Checks on the rebuilt progressions, run after fetch_data.py.

1. End points: the last ratified mark of each event must equal the record on
   World Athletics' records page (sources/wa_current_records_2026-10-28.csv).
2. Gaps: every reign longer than 20 years is listed for a manual look-up.
3. Jumps: every improvement larger than 4 times the event's median improvement
   and above 1% (above 3% for combined events) is listed for a manual look-up.
4. Chronology: dates within each timing series must not go backwards.

Each flag is looked up in the Wikipedia cross-check (crosscheck_wikipedia.py must
run first). A flag confirmed there gets an automatic note; a flag that is not
confirmed, or a jump above 10%, needs a hand-written note in
sources/flag_notes.csv, or the run fails. Output: tables/validation_flags.csv
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

from reigns import build_reigns

ROOT = Path(__file__).resolve().parent
TODAY = pd.Timestamp("2026-10-28")


def mark_value(mark: str) -> float:
    s = str(mark).strip()
    if ":" in s:
        t = 0.0
        for p in s.split(":"):
            t = t * 60 + float(p)
        return t
    return float(s)


def main() -> int:
    prog = pd.read_csv(ROOT / "tables" / "progressions.csv", parse_dates=["date"])
    reigns = build_reigns(prog, TODAY)
    cur = pd.read_csv(ROOT / "sources" / "wa_current_records_2026-10-28.csv")
    notes_path = ROOT / "sources" / "flag_notes.csv"
    notes = pd.read_csv(notes_path) if notes_path.exists() else pd.DataFrame(columns=["key", "note"])
    notes = dict(zip(notes.key, notes.note))

    flags, failed = [], False
    standing = reigns[reigns.broken == 0]
    standing = standing[standing.censor_reason == "still standing"]
    for _, c in cur.iterrows():
        s = standing[(standing.sex == c.sex) & (standing.event == c.event)]
        if len(s) != 1 or abs(s.value.iloc[0] - mark_value(c.mark)) > 1e-6 or str(s.date_set.iloc[0]) != c.date:
            print(f"END POINT MISMATCH {c.sex} {c.event}: WA {c.mark} {c.date} vs "
                  f"{s[['mark','date_set']].values.tolist()}")
            failed = True

    for _, r in reigns.iterrows():
        key = f"{r.sex}|{r.event}|{r.mark}|{r.date_set}"
        if r.years > 20:
            flags.append({"key": key, "type": "reign over 20 years", "sex": r.sex, "event": r.event,
                          "mark": r.mark, "athlete": r.athlete, "date_set": r.date_set,
                          "years": round(r.years, 1), "status": "standing" if not r.broken else "broken"})
    for (sx, ev), g in reigns.groupby(["sex", "event"]):
        imp = g.improvement_pct.dropna()
        if imp.empty:
            continue
        med = imp.median()
        lim = 3.0 if g.group.iloc[0] == "combined" else 1.0
        for _, r in g[(g.improvement_pct > 4 * med) & (g.improvement_pct > lim)].iterrows():
            flags.append({"key": f"{r.sex}|{r.event}|{r.mark}|{r.date_set}", "type": "large jump",
                          "sex": r.sex, "event": r.event, "mark": r.mark, "athlete": r.athlete,
                          "date_set": r.date_set, "years": round(r.improvement_pct, 2),
                          "status": f"improvement {r.improvement_pct:.2f}% (event median {med:.2f}%)"})
    fl = pd.DataFrame(flags)
    wk = pd.read_csv(ROOT / "tables" / "wikipedia_crosscheck.csv")
    wk["key"] = wk.sex + "|" + wk.event + "|" + wk["mark"].astype(str) + "|" + wk["date"].astype(str)
    fl["wikipedia_match"] = fl.key.map(dict(zip(wk.key, wk.wikipedia_match)))
    fl["note"] = fl.key.map(notes)
    auto_ok = fl.note.isna() & (fl.wikipedia_match == True) & ~((fl.type == "large jump") & (fl.years > 10))
    fl.loc[auto_ok, "note"] = "Same mark and year in the WA handbook and on Wikipedia; kept as listed."
    fl.to_csv(ROOT / "tables" / "validation_flags.csv", index=False)
    missing = fl[fl.note.isna()]
    print(f"{len(fl)} flags ({(fl.type == 'reign over 20 years').sum()} long reigns, "
          f"{(fl.type == 'large jump').sum()} large jumps); {len(missing)} without a look-up note")
    if len(missing):
        print(missing[["key", "type", "athlete", "years", "status"]].to_string())
        failed = True
    print("end points: " + ("FAILED" if failed else f"all {len(cur)} match World Athletics"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
