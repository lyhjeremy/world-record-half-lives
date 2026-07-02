# World-Record Half-Lives

*How long does a world record live — and can a survival model tell us which of today's records is next?*

## Records as survival times

A world record has a lifespan. It is "born" the day it is set and "dies" the day
someone beats it — and the records standing right now simply haven't died *yet*.
That is exactly the structure survival analysis was built for: durations, some of
which are **censored** because the event (here, being broken) hasn't happened.

So I reframed a century of athletics as a survival dataset. For nine events I
reconstructed the ratified progression, measured every record's **reign** (years
until the next record broke it), and marked the nine current records as censored.
That gives ~250 reigns to model — with Kaplan-Meier for the overall survival
curve, Cox proportional hazards for *what* shortens a reign, and the fitted model
turned back on today's records to rank their vulnerability.

## Getting the data right was the hard part

The progressions live on Wikipedia, and `pandas.read_html` reads them in seconds —
but that is a trap. Each page also carries all-time top-lists, by-nation tables and
navigation boxes, and for several events the *modern* progression sits in a table a
generic scraper won't reliably pick up. Scrape naïvely and you get a women's 100 m
history that mysteriously stops in 1973, or a "1:59 marathon" that never counted.

The fix is a **completeness gate**: reconstruct each event's running best from the
scraped marks, and keep the event only if that best equals the current *ratified*
world record (and the history reaches back far enough). It's a blunt check, but a
decisive one — it turns "I scraped some tables" into "every event I analyse is
provably complete." Nine events cleared it; the rest were dropped rather than
analysed half-true.

## What the survival curve says

<p align="center">
  <img src="figures/record_survival.png" alt="Kaplan-Meier survival of records by era" width="720">
</p>

The headline number is smaller than you'd guess: the **median record half-life is
about 1.5 years**. Most records don't stand for long — someone is usually right
behind. But that median is dominated by the churn of early athletics, and it hides
a very heavy tail. Split the curve by era and the story sharpens: a record set
since 1980 survives far longer than one set before it. The Cox model puts a number
on it — being a *modern* record roughly **halves** the hazard of being broken.

The other clean signal from Cox: the **size of the leap that set a record predicts
its durability**. Records set by a big margin (think Beamon's long jump, or
Kratochvílová's 800 m) last longer — a large jump both signals a special
performance and leaves less room for the next athlete to find.

## Progressions bend toward a wall

<p align="center">
  <img src="figures/progressions.png" alt="Record progressions with fitted deceleration" width="760">
</p>

Every progression decelerates — each record shaves less than the one before, the
curve flattening toward some human limit. That deceleration is real and fittable.
Estimating the *limit itself*, though, is not something the data will honestly
support for events still dropping fast (the asymptote just runs off to wherever you
let it), so I don't headline a number for it. Where a progression has genuinely
plateaued — the women's 800 m, the women's high jump near 2.10 m — the flattening is
plain to see.

## Which records fall next?

<p align="center">
  <img src="figures/vulnerability.png" alt="Five-year fall probability per current record" width="720">
</p>

Because the hazard is front-loaded, the cleanest vulnerability estimate comes
straight from the survival curve: given that a record has *already* survived *t*
years, what's the chance it falls in the next five? Recent records sit in the steep
early part of the curve and score high — the **2023 marathon record (~60%)**, the
**2024 women's high jump (~59%)**, the **2023 shot put (~57%)**. Old records have
proven their durability by surviving the dangerous years, so they score low — and
the **1983 women's 800 m comes out at essentially 0%**, the statistical embodiment
of a record everyone has stopped expecting to fall.

## What's missing, honestly

The model sees *when* records fell, not the live pressure on them — how many
athletes are within 1% of a record right this season. Adding that "pool pressure"
signal is the obvious next step and would sharpen the five-year probabilities. And
the nine-event scope is a deliberate trade: I'd rather model nine provably complete
histories than twenty half-scraped ones.

*Code: [github.com/lyhjeremy/world-record-half-lives](https://github.com/lyhjeremy/world-record-half-lives)*
