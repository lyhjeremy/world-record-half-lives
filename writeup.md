# World-record half-lives

*How long does an athletics world record stand, and which of today's records is most likely to go next?*

## Records as survival times

A world record has a lifespan. It starts the day it is set and ends the day someone beats it. The records standing now haven't ended yet, which makes them censored observations: we know they have lasted at least this long, and nothing more. Survival analysis was built for exactly this kind of data.

For nine events I reconstructed the ratified progression, measured every record's reign (years until the next record broke it), and marked the nine current records as censored. That gives 248 reigns, 239 of them ended. Kaplan-Meier estimates the survival curve, a Cox proportional-hazards model tests what shortens a reign, and the curve is then applied to today's records to rank how exposed each one is.

## Getting the data right

The progressions are on Wikipedia, and `pandas.read_html` reads them in seconds. The catch is that each page also carries all-time lists, by-nation tables and navigation boxes, and for several events the modern progression sits in a table a generic scraper won't reliably find. A naive scrape can give a women's 100 m history that stops in 1973, or count a sub-2:00 marathon that was never ratified.

So the fetcher has a completeness gate. It rebuilds each event's running best from the scraped marks and keeps the event only if that best is within 0.5% of the current ratified record, the history reaches back to 1970 or earlier, and there are at least 12 marks. Nine of the 21 candidate events passed: the men's 100 m, 800 m, 1500 m, 5000 m, 10,000 m, marathon and shot put, and the women's 800 m and high jump. The rest were dropped.

The gate has a blind spot. It checks the end point and the start year, not the gaps between them. The women's 800 m passed with only three reigns, one of them running 55 years from 1925 to 1980, which almost certainly means rows are missing.

## What the survival curve says

<p align="center">
  <img src="figures/record_survival.png" alt="Kaplan-Meier survival curves for world records set before 1980 and from 1980 on; both drop below one half within about two years, and the post-1980 curve sits higher for most of the range." width="720">
</p>

The median reign is 1.5 years. Most records don't stand for long, because the athlete who set them and the rivals chasing them are usually still competing. The curve then flattens into a long tail, and every record people think of as untouchable lives in that tail.

Split at 1980, the median is about 1.2 years for the 170 earlier records and about 2.0 years for the 78 later ones. The Cox model gives the same picture: a record set in 1980 or later has a hazard ratio of 0.52 (p < 0.001), about half the yearly risk of being broken.

The model's other clear result is about margins. Each percentage point by which a record beat its predecessor multiplies the hazard by 0.65 (p = 0.0002). A record set by a wide gap, like Beamon's long jump or Kratochvílová's 800 m, leaves the rest of the field further behind. The third covariate, distance event, came out at 0.78 with p = 0.07, which I don't treat as a finding.

## Progressions flatten

<p align="center">
  <img src="figures/progressions.png" alt="Record progression curves for the analysed events, improving over time in smaller and smaller steps, with fitted deceleration curves." width="760">
</p>

Every progression decelerates: each new record takes off less than the one before. That shape is real and can be fitted. The limit itself is another matter. For events still dropping fast, the asymptote runs off to wherever the optimizer lets it, so I don't report a number. Where a progression has plainly levelled off, as with the women's 800 m and the women's high jump near 2.10 m, the plot shows it without a fit.

## Which records fall next?

<p align="center">
  <img src="figures/vulnerability.png" alt="Ranked bar chart of the five-year fall probability for each standing world record, from about 60 percent for the 2023 marathon down to zero for the 1983 women's 800 metres." width="720">
</p>

Because the risk is concentrated early, the simplest forecast comes straight from the survival curve. For a record that has already stood *t* years, the chance it falls in the next five is 1 − S(t+5)/S(t). Recent records sit on the steep part of the curve and score high: the 2023 marathon (60%), the 2024 women's high jump (59%) and the 2023 shot put (57%). Older records have already passed the steep part. The 1983 women's 800 m scores 0%.

That zero is partly an artifact. No reign in the data ends between 43 and 48 years, so the curve is flat across that stretch and the formula returns exactly zero. "Very unlikely" is a fair reading; "impossible" is not.

These numbers use record ages as of the July 2026 run. Rerunning `analyze.py` later will shift them.

## What's missing

The model knows when records fell. It doesn't know the pressure on them now, such as how many athletes are within 1% of a record this season. That would be the next feature, and it would sharpen the five-year numbers. The nine-event scope is a deliberate trade: I'd rather model nine histories that pass a check than twenty that might be truncated, though the women's 800 m shows the check is not airtight.

*Code: [github.com/lyhjeremy/world-record-half-lives](https://github.com/lyhjeremy/world-record-half-lives)*
