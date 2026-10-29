"""Cross-check the World Athletics progressions against Wikipedia.

For every record in tables/progressions.csv, look for the same mark in the same
year anywhere in a table row of the matching Wikipedia progression page. This is
a second source, not a correction: nothing in the main data is changed by it.
Pages are cached in ~/data-cache/world-record-half-lives/wiki/.

Output: tables/wikipedia_crosscheck.csv (one row per record, matched or not) and
a per-event summary on stdout.
"""

from __future__ import annotations

import io
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
CACHE = Path.home() / "data-cache" / "world-record-half-lives" / "wiki"
UA = {"User-Agent": "world-record-half-lives research (github.com/lyhjeremy)"}

NAMES = {"100m": "100 metres", "200m": "200 metres", "400m": "400 metres", "800m": "800 metres",
         "1500m": "1500 metres", "5000m": "5000 metres", "10000m": "10,000 metres",
         "3000sc": "3000 metres steeplechase", "110h": "110 metres hurdles",
         "100h": "100 metres hurdles", "400h": "400 metres hurdles", "hj": "high jump",
         "pv": "pole vault", "lj": "long jump", "tj": "triple jump", "sp": "shot put",
         "dt": "discus throw", "ht": "hammer throw", "jt": "javelin throw", "dec": "decathlon",
         "hep": "heptathlon", "4x100": "4 × 100 metres relay", "4x400": "4 × 400 metres relay",
         "20kw": "20 kilometres race walk"}


def titles(sex: str, ev: str) -> list[str]:
    if ev == "marathon":
        return ["Marathon world record progression"]
    who = "Men's" if sex == "M" else "Women's"
    name = NAMES[ev]
    out = [f"{who} {name} world record progression", f"{name.capitalize()} world record progression"]
    if ev == "20kw":
        out += [f"{who} 20 kilometres walk world record progression", "20 kilometres race walk world record progression"]
    return out


def fetch(t: str) -> str | None:
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / (re.sub(r"[^A-Za-z0-9]+", "_", t) + ".html")
    if f.exists():
        return f.read_text()
    url = "https://en.wikipedia.org/wiki/" + urllib.parse.quote(t.replace(" ", "_"))
    try:
        html = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30).read().decode()
    except Exception:
        return None
    f.write_text(html)
    time.sleep(0.5)
    return html


def norm(m: str) -> str:
    m = str(m).strip().rstrip("y+*hA")
    m = re.sub(r"^0(?=\d)", "", m)
    return m


def row_pairs(html: str) -> set[tuple[str, int]]:
    pairs = set()
    try:
        tables = pd.read_html(io.StringIO(html))
    except ValueError:
        return pairs
    for t in tables:
        for _, r in t.iterrows():
            txt = " ".join(str(x) for x in r.values)
            txt = re.sub(r"\[[^\]]*\]", " ", txt).replace("\xa0", " ")
            years = {int(y) for y in re.findall(r"(?<!\d)(18[6-9]\d|19\d\d|20[0-2]\d)(?!\d)", txt)}
            marks = set(re.findall(r"\d+(?::\d{2})*(?:\.\d+)?", txt))
            marks |= {m.rstrip("0").rstrip(".") for m in marks if "." in m}
            for m in marks:
                for y in years:
                    pairs.add((m, y))
    return pairs


def main() -> None:
    prog = pd.read_csv(ROOT / "tables" / "progressions.csv", parse_dates=["date"])
    prog = prog[prog.series != "marker"]
    out = []
    for (sx, ev), g in prog.groupby(["sex", "event"]):
        html = next((h for h in map(fetch, titles(sx, ev)) if h), None)
        pairs = row_pairs(html) if html else set()
        sub = g if not (ev == "marathon" and sx == "W") else g
        for _, r in sub.iterrows():
            m = norm(r.mark)
            alt = {m, m.rstrip("0").rstrip(".") if "." in m else m}
            if "." in m and m.count(":") == 0 and len(m.split(".")[1]) == 3:
                alt.add(m[:-1])  # 30.225 -> 30.22
            ok = any((a, r.date.year) in pairs for a in alt) if html else None
            out.append({"sex": sx, "event": ev, "mark": r.mark, "athlete": r.athlete,
                        "date": r.date.date(), "page_found": html is not None, "wikipedia_match": ok})
    df = pd.DataFrame(out)
    df.to_csv(ROOT / "tables" / "wikipedia_crosscheck.csv", index=False)
    s = df.groupby(["sex", "event"]).agg(n=("mark", "size"), page=("page_found", "first"),
                                         matched=("wikipedia_match", "mean"))
    print(s.to_string())
    found = df[df.page_found]
    print(f"\n{found.wikipedia_match.mean():.1%} of {len(found)} records on found pages matched "
          f"by mark and year; {df[~df.page_found].groupby(['sex','event']).ngroups} pages not found")


if __name__ == "__main__":
    main()
