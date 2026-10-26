# World-record half-lives

A survival analysis of athletics world records: how long they stand, what makes them last, and which of today's records is most likely to fall next.

[Live site](https://lyhjeremy.github.io/world-record-half-lives/) · [Write-up](https://lyhjeremy.github.io/world-record-half-lives/overview/) · [Long-form notes](writeup.md)

Design and copy follow [these standards](https://github.com/lyhjeremy/lyhjeremy/blob/main/DESIGN_STANDARDS.md).

## Results

| Result | Value |
|---|---|
| Kaplan-Meier median reign, all 248 records | 1.5 years |
| Median reign, set before 1980 (n=170) vs 1980 or later (n=78) | 1.2 vs 2.0 years |
| Cox hazard ratio, record set in 1980 or later | 0.52 (p < 0.001) |
| Cox hazard ratio per percentage point of improvement over the previous record | 0.65 (p = 0.0002) |
| Five-year fall probability, 2023 marathon record (highest) | 60% |
| Five-year fall probability, 1983 women's 800 m (lowest) | 0% |

<p align="center">
  <img src="figures/record_survival.png" alt="Kaplan-Meier survival curves for world records set before 1980 and from 1980 on; both drop below one half within about two years, and the post-1980 curve sits higher for most of the range." width="760">
</p>

## Method

Each world record is a duration: it starts when it is set and ends when it is broken. The nine records still standing are right-censored, so a plain average of past reigns would be biased short. Survival analysis keeps them in.

1. `fetch_data.py` reads Wikipedia's record-progression pages with `pandas.read_html` and runs a completeness gate. An event is kept only if its reconstructed running best is within 0.5% of the current ratified record, its history reaches back to 1970 or earlier, and it has at least 12 marks. Nine of 21 candidate events pass: the men's 100 m, 800 m, 1500 m, 5000 m, 10,000 m, marathon and shot put, and the women's 800 m and high jump.
2. `analyze.py` rebuilds each progression as a running best and measures every reign (248 in total, 239 broken and 9 standing).
3. A Kaplan-Meier estimator (lifelines) gives the survival curve and the median reign, overall and split at 1980.
4. A Cox proportional-hazards model on the 239 reigns that have a predecessor tests three covariates: set in 1980 or later, percentage improvement over the previous record, and distance event (HR 0.78, p = 0.07, not significant).
5. For each standing record of age *t*, the five-year fall probability is 1 − S(t+5)/S(t) from the Kaplan-Meier curve.
6. An asymptotic curve is fitted to each progression with SciPy for the progression figure. Event limits from those fits are not reported (see limitations).

<p align="center">
  <img src="figures/vulnerability.png" alt="Ranked bar chart of the five-year fall probability for each standing world record, from about 60 percent for the 2023 marathon down to zero for the 1983 women's 800 metres." width="720">
</p>

## Stack

Python, pandas, lxml, NumPy, SciPy, lifelines, matplotlib. The site is static HTML on GitHub Pages.

## How to run

```bash
pip install -r requirements.txt
python fetch_data.py      # scrape and verify progressions -> data/raw/ (git-ignored)
python analyze.py         # survival models and curve fits -> figures/ and tables/
```

Reign lengths for standing records, and so the five-year probabilities, depend on the date you run it. The committed tables are from July 2026.

## Repo layout

| Path | What it is |
|---|---|
| `fetch_data.py` | Scrape Wikipedia progressions; keep only events that pass the completeness gate |
| `analyze.py` | Reigns, Kaplan-Meier, Cox, progression fits, figures and tables |
| `figures/` | Survival curves, progression fits, vulnerability ranking |
| `tables/` | `record_reigns.csv`, `cox_summary.csv`, `current_record_vulnerability.csv` |
| `index.html`, `overview/` | The two pages of the site |
| `writeup.md` | Long-form notes |

## Limitations

- The model sees when records fell, not the current pressure on them (for example, how many athletes are within 1% this season).
- The 0% for the women's 800 m is partly mechanical. No reign in the data ends between 43 and 48 years, so the curve is flat over that stretch.
- The women's 800 m contributes only three reigns, one of them 55 years long (1925 to 1980). That points to missing rows; the gate checks the final mark and start year, not gaps in between.
- Asymptotic event limits are left out because for events still improving the asymptote is not identifiable from the data.

## License

[MIT](LICENSE) © 2026 Jeremy Lee. Record data from Wikipedia.
