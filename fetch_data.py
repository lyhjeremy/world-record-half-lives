"""Download world-record progression histories from Wikipedia.

For each event we fetch its gender-specific "… world record progression" page and
pull every (mark, date) pair from its tables. Wikipedia pages also carry auxiliary
tables (season top-lists, by-nation, indoor) and — on shared pages — the other
gender, so we keep only marks inside a generous *physical plausibility band* for
that event and gender, and drop impossible dates. `analyze.py` then reconstructs
the ratified progression as the running best of the surviving marks.

The band (lo, hi) is deliberately wide — it just fences out parsing garbage (a
"3:40 marathon", a 2026 top-list row) and the faster gender on a shared page; it
never trims the real record sequence.

Output: data/raw/progressions.csv  (event, gender, category, unit, mark, value, date)

Usage:  python fetch_data.py
"""

from __future__ import annotations

import io
import re
import time
from pathlib import Path

import pandas as pd
import urllib.request

RAW = Path(__file__).resolve().parent / "data" / "raw"
UA = {"User-Agent": "Mozilla/5.0 (world-record-half-lives research; github.com/lyhjeremy)"}
TODAY = pd.Timestamp.today()

# key -> (wiki title, gender, category, unit, lo, hi)   unit: time→sec, dist→m
EVENTS = {
    "100m":        ("Men's 100 metres world record progression", "M", "sprint", "time", 9.5, 11.0),
    "200m":        ("Men's 200 metres world record progression", "M", "sprint", "time", 19.0, 22.5),
    "400m":        ("Men's 400 metres world record progression", "M", "sprint", "time", 43.0, 49.0),
    "800m":        ("Men's 800 metres world record progression", "M", "middle", "time", 100.0, 112.0),
    "1500m":       ("Men's 1500 metres world record progression", "M", "middle", "time", 205.0, 230.0),
    "mile":        ("Mile run world record progression", "M", "middle", "time", 223.0, 265.0),
    "5000m":       ("Men's 5000 metres world record progression", "M", "distance", "time", 755.0, 900.0),
    "10000m":      ("Men's 10,000 metres world record progression", "M", "distance", "time", 1570.0, 1900.0),
    # marathon lo=7230 excludes Kipchoge's non-ratified sub-2:00 exhibitions.
    "marathon":    ("Marathon world record progression", "M", "distance", "time", 7230.0, 11000.0),
    "long_jump":   ("Men's long jump world record progression", "M", "jump", "dist", 7.6, 9.0),
    "high_jump":   ("Men's high jump world record progression", "M", "jump", "dist", 2.0, 2.5),
    "pole_vault":  ("Men's pole vault world record progression", "M", "jump", "dist", 4.5, 6.3),
    "shot_put":    ("Men's shot put world record progression", "M", "throw", "dist", 15.0, 24.5),
    "discus":      ("Men's discus throw world record progression", "M", "throw", "dist", 44.0, 77.0),
    "100m_w":      ("Women's 100 metres world record progression", "W", "sprint", "time", 10.4, 12.5),
    "200m_w":      ("Women's 200 metres world record progression", "W", "sprint", "time", 21.3, 24.5),
    "400m_w":      ("Women's 400 metres world record progression", "W", "sprint", "time", 47.0, 56.0),
    "800m_w":      ("Women's 800 metres world record progression", "W", "middle", "time", 112.5, 135.0),
    "1500m_w":     ("Women's 1500 metres world record progression", "W", "middle", "time", 225.0, 262.0),
    "long_jump_w": ("Women's long jump world record progression", "W", "jump", "dist", 6.5, 7.6),
    "high_jump_w": ("Women's high jump world record progression", "W", "jump", "dist", 1.8, 2.15),
}


# Current ratified world records (seconds / metres). Used ONLY to verify each scraped
# progression is complete — its running best must equal the real WR. Events that fail
# are dropped: their modern records sit in page tables our generic scraper can't
# reliably isolate, and a truncated progression would yield wrong record lifespans.
REFERENCE_WR = {
    "100m": 9.58, "800m": 100.91, "1500m": 206.00, "mile": 223.13, "5000m": 755.36,
    "10000m": 1571.00, "marathon": 7235.0, "200m": 19.19, "400m": 43.03,
    "long_jump": 8.95, "high_jump": 2.45, "shot_put": 23.56, "discus": 74.35,
    "100m_w": 10.49, "200m_w": 21.34, "400m_w": 47.60, "800m_w": 113.28,
    "1500m_w": 229.11, "long_jump_w": 7.52, "high_jump_w": 2.10,
}
GATE_TOL = 0.005           # best must be within 0.5% of the real WR
GATE_FIRST_YEAR = 1970     # and the history must reach back at least this far
GATE_MIN_MARKS = 12


def _fetch_html(title: str) -> str:
    url = "https://en.wikipedia.org/wiki/" + urllib.request.quote(title.replace(" ", "_"))
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30).read().decode("utf-8", "replace")


def _parse_time(s: str) -> float | None:
    m = re.match(r"^(?:(\d+):)?(?:(\d+):)?(\d+(?:\.\d+)?)$", str(s).strip())
    if not m:
        return None
    total = 0.0
    for p in (p for p in m.groups() if p is not None):
        total = total * 60 + float(p)
    return total


def _parse_dist(s: str) -> float | None:
    m = re.search(r"(\d+\.\d+)", str(s))
    return float(m.group(1)) if m else None


def _clean_mark(raw) -> str:
    s = str(raw).replace("\xa0", " ")
    s = re.sub(r"\[[^\]]*\]", "", s)
    s = re.sub(r"\(.*?\)", "", s)
    return s.strip().split(" ")[0].strip()


_MARK_COLS = ("time", "mark", "performance", "distance", "height", "result")


def _extract(html: str, unit: str, lo: float, hi: float) -> pd.DataFrame:
    rows = []
    for t in pd.read_html(io.StringIO(html)):
        cols = {str(c).lower(): c for c in t.columns}
        mcol = next((cols[k] for k in cols if any(mc in k for mc in _MARK_COLS)), None)
        dcol = next((cols[k] for k in cols if "date" in k), None)
        if mcol is None or dcol is None or len(t) < 3:
            continue
        for _, r in t.iterrows():
            val = (_parse_time if unit == "time" else _parse_dist)(_clean_mark(r[mcol]))
            date = pd.to_datetime(str(r[dcol]), errors="coerce")
            if val is not None and lo <= val <= hi and pd.notna(date) and pd.Timestamp("1896-01-01") <= date <= TODAY:
                rows.append({"mark": _clean_mark(r[mcol]), "value": val, "date": date})
    return pd.DataFrame(rows).drop_duplicates(subset=["value", "date"])


def main() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    frames = []
    for key, (title, gender, category, unit, lo, hi) in EVENTS.items():
        try:
            df = _extract(_fetch_html(title), unit, lo, hi)
        except Exception as e:
            print(f"  ! {key:14s} {type(e).__name__}: {e}")
            continue
        if len(df) < 4:
            print(f"  ! {key:14s} only {len(df)} marks — skipped")
            continue
        df = df.sort_values("date")
        best = df["value"].min() if unit == "time" else df["value"].max()
        ref = REFERENCE_WR.get(key)
        first_year, n = int(df["date"].dt.year.min()), len(df)
        ok = (ref is not None and abs(best - ref) / ref <= GATE_TOL
              and first_year <= GATE_FIRST_YEAR and n >= GATE_MIN_MARKS)
        tag = "PASS" if ok else "drop"
        note = "" if ref is None else f" (WR {ref:g})"
        print(f"  [{tag}] {key:14s} {n:3d} marks  {first_year}–{df['date'].dt.year.max()}  best={best:g}{note}")
        if ok:
            for col, v in [("event", key), ("gender", gender), ("category", category), ("unit", unit)]:
                df.insert(0, col, v)
            frames.append(df)
        time.sleep(1.0)

    out = pd.concat(frames, ignore_index=True)[
        ["event", "gender", "category", "unit", "mark", "value", "date"]]
    out.to_csv(RAW / "progressions.csv", index=False)
    print(f"\nwrote data/raw/progressions.csv  ({len(out)} rows, {out['event'].nunique()} verified-complete events)")


if __name__ == "__main__":
    main()
