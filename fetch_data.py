"""Build ratified world-record progressions for every standard Olympic event.

Source of record: the "Progression of Official World Records" section of World
Athletics' Oregon22 Statistics Handbook (edited for World Athletics Heritage by
Richard Hymans, records as at 31 May 2022). That section lists every ratified
world record, including equalling marks, for men and women. Records ratified
after 31 May 2022 come from `sources/updates_2022_2026.csv`, which was checked
against World Athletics' own records page on 2026-10-28. Wikipedia is used only
as a cross-check (`crosscheck_wikipedia.py`).

Steps
1. Download the handbook PDF to ~/data-cache/world-record-half-lives/ (not in git)
   and convert it with `pdftotext -layout` (poppler).
2. Parse each event block in the men's and women's progression sections.
3. Hand-timed and automatically timed series are kept apart (column `series`).
4. Road events (marathon, 20 km walk) use the ratified list in
   `sources/road_ratified.csv`: the handbook prints ratified road marks in bold,
   which does not survive text extraction.
5. Append the 2022-2026 updates and write tables/progressions.csv.

Usage:  python fetch_data.py
"""

from __future__ import annotations

import re
import subprocess
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
CACHE = Path.home() / "data-cache" / "world-record-half-lives"
PDF_URL = "https://iaafmedia.s3.amazonaws.com/misc/Oregon22/Oregon%20stats%20book.pdf"
PDF = CACHE / "pdf" / "oregon22.pdf"
TXT = CACHE / "pdf" / "oregon22.txt"

# Handbook line ranges of the two progression sections (stable for this PDF).
MEN_START, WOMEN_START, WOMEN_END = "PROGRESSION OF OFFICIAL WORLD RECORDS", None, None

# event key -> (handbook heading, sex, group, unit)
TRACK = {
    "100m": ("100 Metres", "sprint"), "200m": ("200 Metres (Bend)", "sprint"),
    "400m": ("400 Metres", "sprint"), "800m": ("800 Metres", "middle"),
    "1500m": ("1500 Metres", "middle"), "5000m": ("5000 Metres", "distance"),
    "10000m": ("10,000 Metres", "distance"), "3000sc": ("3000 Metres Steeplechase", "distance"),
    "110h": ("110 Metres Hurdles", "hurdles"), "100h": ("100 Metres Hurdles", "hurdles"),
    "400h": ("400 Metres Hurdles", "hurdles"),
    "hj": ("High Jump", "jumps"), "pv": ("Pole Vault", "jumps"), "lj": ("Long Jump", "jumps"),
    "tj": ("Triple Jump", "jumps"), "sp": ("Shot Put", "throws"), "dt": ("Discus Throw", "throws"),
    "ht": ("Hammer Throw", "throws"), "jt": ("Javelin Throw", "throws"),
    "dec": ("Decathlon", "combined"), "hep": ("Heptathlon", "combined"),
    "4x100": ("4 x 100 Metres Relay", "relay"), "4x400": ("4 x 400 Metres Relay", "relay"),
}
MEN_EVENTS = ["100m", "200m", "400m", "800m", "1500m", "5000m", "10000m", "3000sc", "110h", "400h",
              "hj", "pv", "lj", "tj", "sp", "dt", "ht", "jt", "dec", "4x100", "4x400"]
WOMEN_EVENTS = ["100m", "200m", "400m", "800m", "1500m", "5000m", "10000m", "3000sc", "100h", "400h",
                "hj", "pv", "lj", "tj", "sp", "dt", "ht", "jt", "hep", "4x100", "4x400"]
LOWER_IS_BETTER = {"sprint", "middle", "distance", "hurdles", "relay", "walk", "road"}

MON = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}
DATE_RE = re.compile(r"(\d{1,2})\s+([A-Z][a-z]{2})[a-z]?\s*(\d{2})\s*$")
LEADER = re.compile(r"\s*\.(?:\s*\.)+\s*")
COUNTRY = re.compile(r"^[A-Z]{3}(?:/[A-Z]{3})?$")
HEAD_RE = re.compile(r"^(\S.*?)\s{4,}(\d+|No) ratified world records?")


def ensure_text() -> list[str]:
    PDF.parent.mkdir(parents=True, exist_ok=True)
    if not PDF.exists():
        req = urllib.request.Request(PDF_URL, headers={"User-Agent": "Mozilla/5.0"})
        PDF.write_bytes(urllib.request.urlopen(req, timeout=120).read())
    if not TXT.exists():
        subprocess.run(["pdftotext", "-layout", str(PDF), str(TXT)], check=False)
    return TXT.read_text(encoding="utf-8").splitlines()


def parse_value(mark: str, group: str) -> float | None:
    s = mark.lstrip("0") if not mark.startswith("0:") else mark
    s = s.rstrip("y+hA*")
    if s.startswith(".") or s.startswith(":"):
        s = "0" + s
    if group in ("jumps", "throws", "combined"):
        try:
            return float(s)
        except ValueError:
            return None
    parts = s.split(":")
    try:
        total = 0.0
        for p in parts:
            total = total * 60 + float(p)
        return total
    except ValueError:
        return None


def sections(lines: list[str]) -> tuple[list[str], list[str]]:
    idx = [i for i, l in enumerate(lines) if "PROGRESSION OF OFFICIAL WORLD RECORDS" in l]
    men = lines[idx[0]:idx[1]]
    women = lines[idx[1]:idx[1] + 1400]
    return men, women


def event_blocks(sec: list[str]) -> dict[str, list[str]]:
    blocks, cur = {}, None
    for l in sec:
        m = HEAD_RE.match(l)
        if m:
            cur = m.group(1).strip()
            blocks.setdefault(cur, [])
            continue
        if cur:
            blocks[cur].append(l)
    return blocks


def parse_block(block: list[str], group: str) -> list[dict]:
    """Return rows in listed order. Each row is tagged with its timing series."""
    rows, series, first_auto = [], "main", None
    for l in block:
        if "Progression of automatically-timed records" in l:
            series = "auto"
            m = re.search(r"marks?,?\s+([\d.:]+)\s+(\S+)", l)
            first_auto = m.group(1) if m else None
            if first_auto:  # the first auto record is printed in the hand list's AUTO column
                who = m.group(2)
                src = next((r for r in reversed(rows) if r.get("auto") == first_auto
                            and (who in r["athlete"] or who in r["country"])), None)
                if src:
                    rows.append({**src, "mark": first_auto, "series": "auto",
                                 "value": parse_value(first_auto, group), "from_auto_col": True})
            continue
        if "New specification introduced" in l:
            series = "newspec"
            continue
        if "Javelins with serrated tails outlawed" in l:
            rows.append({"marker": "serrated_reversion"})
            continue
        l = re.sub(r"\d+\.\d+/adjusted from (\d+\.\d+)", r". . . \1 . . .", l)
        dm = DATE_RE.search(l)
        if not dm or not re.match(r"^\s*\d", l):
            continue
        body = l[:dm.start()]
        parts = [p.strip() for p in LEADER.split(body) if p.strip()]
        if len(parts) < 3:
            continue
        ci = next((i for i in range(len(parts) - 1, 0, -1)
                   if COUNTRY.match(parts[i].split()[0].rstrip("."))), None)
        if ci is None or ci < 2:
            continue
        ctoks = parts[ci].split()
        country = ctoks[0].rstrip(".")
        mark, extras, name = parts[0].split()[0], parts[1:ci - 1], parts[ci - 1]
        venue = " ".join([t for t in ctoks[1:] if t != "."] + parts[ci + 1:])
        flags = ""
        mm = re.match(r"^(\d*:?\d*[.:]?\d+)([a-zA-Z+*]*)(\[\d+\])?$", mark)
        if mm:
            mark, flags = mm.group(1) + ("y" if "y" in mm.group(2) else "") + ("+" if "+" in mm.group(2) else ""), mm.group(2)
        wind, auto, note = None, None, []
        for x in extras:
            x0 = x.split()[0]
            if re.fullmatch(r"[+-]?\d\.\d", x0) and group in ("sprint", "hurdles", "jumps"):
                wind = float(x0)
            elif re.fullmatch(r"\d*:?\d+\.\d{2,3}", x0) or re.fullmatch(r"\d+", x0):
                auto = x0.lstrip("0") if not x0.startswith("0.") else x0
            else:
                note.append(x)
        d, mon, yy = int(dm.group(1)), MON.get(dm.group(2)), int(dm.group(3))
        if mon is None:
            continue
        rows.append({"mark": mark.lstrip("0") if re.match(r"0\d", mark) else mark,
                     "value": parse_value(mark, group), "wind": wind, "auto": auto,
                     "athlete": re.sub(r"\s+", " ", name), "country": country, "venue": venue,
                     "indoor": "i" in flags, "wind_aided_flag": "w" in flags,
                     "d": d, "m": mon, "yy": yy, "series": series, "note": " ".join(note)})
    return rows


def assign_years(rows: list[dict], floor_year: int) -> None:
    """Two-digit years: choose the century that keeps the list chronological."""
    floor = pd.Timestamp(f"{floor_year}-01-01")
    prevs: dict[str, pd.Timestamp] = {}
    for r in rows:
        if "marker" in r or r.get("from_auto_col"):
            continue
        best, prev = None, prevs.get(r["series"], floor if r["series"] == "main"
                                         else max(floor, pd.Timestamp("1960-01-01")))
        for c in (1800, 1900, 2000):
            try:
                t = pd.Timestamp(year=c + r["yy"], month=r["m"], day=r["d"])
            except ValueError:
                continue
            if prev - pd.Timedelta(days=1) <= t <= pd.Timestamp("2022-06-01"):
                best = t
                break
        r["date"] = best
        if best is not None:
            prevs[r["series"]] = best
    for r in rows:  # auto copies inherit the date of their source row
        if r.get("from_auto_col"):
            src = next(x for x in rows if "marker" not in x and not x.get("from_auto_col")
                       and x["auto"] == r["mark"] and x["athlete"] == r["athlete"])
            r["date"] = src["date"]


# earliest plausible year of each women's event list (guards the century guess)
FLOOR = {("W", "3000sc"): 1990, ("W", "ht"): 1990, ("W", "pv"): 1990, ("W", "tj"): 1990,
         ("W", "5000m"): 1980, ("W", "10000m"): 1980, ("W", "hep"): 1980, ("W", "100h"): 1969,
         ("W", "400h"): 1974}


def build() -> pd.DataFrame:
    lines = ensure_text()
    men, women = sections(lines)
    out = []
    for sex, sec, evs in (("M", men, MEN_EVENTS), ("W", women, WOMEN_EVENTS)):
        blocks = event_blocks(sec)
        for ev in evs:
            head, group = TRACK[ev]
            key = next((k for k in (head, head.replace(" (Bend)", ""), head + " (Bend)") if k in blocks), None)
            if key not in blocks:
                raise SystemExit(f"missing block {sex} {head}")
            rows = parse_block(blocks[key], group)
            assign_years(rows, FLOOR.get((sex, ev), 1890))
            order = 0
            for r in rows:
                if "marker" in r:
                    out.append({"sex": sex, "event": ev, "group": group, "order": order,
                                "series": "marker", "note": r["marker"]})
                else:
                    out.append({"sex": sex, "event": ev, "group": group, "order": order,
                                **{k: v for k, v in r.items() if k not in ("d", "m", "yy")}})
                order += 1
    df = pd.DataFrame(out)
    df["source"] = "WA Oregon22 Statistics Handbook, progression of official world records (to 31 May 2022)"

    road = pd.read_csv(ROOT / "sources" / "road_ratified.csv", parse_dates=["date"])
    upd = pd.read_csv(ROOT / "sources" / "updates_2022_2026.csv", parse_dates=["date"])
    auto_events = set(map(tuple, df.loc[df.series == "auto", ["sex", "event"]].values))
    for extra in (road, upd):
        extra["series"] = ["auto" if (s, e) in auto_events else "main"
                           for s, e in zip(extra.sex, extra.event)]
        df = pd.concat([df, extra], ignore_index=True)
    df["order"] = df.groupby(["sex", "event"]).cumcount()
    return df


def main() -> None:
    df = build()
    cols = ["sex", "event", "group", "order", "series", "mark", "value", "wind", "athlete",
            "country", "venue", "date", "indoor", "note", "status", "source"]
    for c in cols:
        if c not in df:
            df[c] = None
    df["status"] = df["status"].fillna("ratified")
    df["indoor"] = df["indoor"].map(lambda v: str(v).lower() == "true")
    df = df[cols]
    (ROOT / "tables").mkdir(exist_ok=True)
    df.to_csv(ROOT / "tables" / "progressions.csv", index=False)
    rec = df[df.series != "marker"]
    print(rec.groupby(["sex", "event"]).size().to_string())
    print(f"\nwrote tables/progressions.csv: {len(rec)} records, {rec.groupby(['sex','event']).ngroups} events")


if __name__ == "__main__":
    main()
