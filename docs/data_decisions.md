# Data decisions

October 2026 rebuild. Every number in the write-up comes from the data described here. The code that applies these rules is `fetch_data.py` (parsing), `reigns.py` (turning records into reigns) and `validate.py` (checks).

## Sources

The source of record is the "Progression of Official World Records" section of World Athletics' Oregon22 Statistics Handbook (pages 724 to 877, edited for World Athletics Heritage by Richard Hymans, records as at 31 May 2022). It lists every ratified world record, equalling marks included, for men and women. `fetch_data.py` downloads the PDF to `~/data-cache/world-record-half-lives/` (not in git) and reads it with `pdftotext -layout`.

Records ratified after 31 May 2022 were read from World Athletics' own progression pages (worldathletics.org/records/by-progression/...) on 28 October 2026 and are in `sources/updates_2022_2026.csv`, one row per record with the page it came from. There are 30 of them, 11 of them Duplantis pole vaults.

The current record in every event was checked against World Athletics' records page (worldathletics.org/records/by-category/world-records) on 28 October 2026 and saved in `sources/wa_current_records_2026-10-28.csv`. `validate.py` fails unless the last ratified mark of all 46 events matches it, mark and date. All 46 match.

Wikipedia is a cross-check only (`crosscheck_wikipedia.py`). For every record, it looks for the same mark in the same year in a table row of the matching Wikipedia progression page. 1,456 of 1,464 records (99.5%) match. The eight that do not are formatting differences (2:56.1 against 2:56.16 for the 1968 US 4x400, one decathlon score printed on a different table, and similar), listed in `tables/wikipedia_crosscheck.csv`.

## What is in

46 events: every individual and relay event on the Olympic programme for men and women, with the 20 km race walk and the marathon. That is 1,464 listed records, which become 1,251 reigns (1,192 ended by a new record, 46 still standing, 13 closed by a rule change).

Indoor marks count where World Athletics counts them. Since 1998 a record can be set "with or without a roof", so 18 marks set indoors are in the lists (pole vault for both sexes and Rojas's 15.74 triple jump). Earlier indoor-only records are not in World Athletics' list and are not here.

## What is out, and why

- Mixed 4x400 m relay: first record 2019, too short a history to estimate anything.
- 35 km race walk and the marathon race walk relay: no longer on the Olympic programme, and the first ratified 35 km mark is from 2023.
- Women-only marathon records: World Athletics keeps two women's marathon lists. I use the outright (mixed race) list, which is the one a record falls from when a woman runs faster in any race.
- Pending marks. Two marks were listed as pending ratification on 28 October 2026: Masai Russell's 12.09 in the 100 m hurdles and Alison dos Santos's 45.80 in the 400 m hurdles, both in Zürich on 27 August 2026. Neither is in the model. The watchlist shows them next to the record they would replace.
- Shinnick's 8.33 long jump (1963). It was added to the list in 2021 "with no deletion of later marks", so it was not a record at the time and nobody was chasing it. Dropped.
- The four men's javelin marks made with serrated-tail implements in 1990 and 1991 (89.66 and 90.98 in 1990, Räty's 91.98 and 96.96 in 1991). World Athletics deleted them and the record reverted to Backley's 89.58, so Backley's reign runs to his own 91.46 in January 1992.
- Road events before official recognition. World Athletics only ratified road records from 2004 (with the 2003 marks of Tergat and Pérez as the first). The pre-2004 "world bests" in the handbook are not ratified records and are left out, which leaves the men's marathon with 10 records, the women's with 4 and the walks with 5 and 6.

## Hand timing, automatic timing and the 1977 switch

From 1975 World Athletics accepted separate automatic-timing records for events up to 400 m (sprints, sprint hurdles, 4x100 m), and from 1 January 1977 it accepted only automatic times. The handbook lists the hand-timed progression and then the automatic one. I keep them as two chains:

- The hand-timed chain is censored on 1 January 1977. The 11 hand records standing that day were not broken; the rule changed. They count as survival to that date and no further.
- The automatic chain starts with the first ratified automatic mark, which is often older than 1975 (Hines's 9.95 is from October 1968). A record cannot be at risk of falling before the list it belongs to exists, so those 11 reigns enter the risk set on 1 January 1975 (left truncation). Hines's 9.95 is counted from age 6.2 years to its end in 1983.

Automatic times to hundredths were accepted up to 10,000 m from 1981. Those events have one list and need no split: a 1:41.73 simply beats a 1:42.4. The 4x400 m list is also a single list in the handbook; its 1988 2:56.16 equals the 1968 2:56.1 rather than beating it.

Yards marks (220y, 440y, 880y, 4x110y) were ratified for the metric events until 1976. They stay in the list and are compared after scaling by the distance ratio. A listed mark that does not beat the standing record on that basis does not end a reign; this affects five reigns, all before 1932 (for example Carr's 46.2 400 m in 1932 against Eastman's 46.4 for 440 yards earlier that year).

## Other rules

- Equalling marks do not end a reign. There are 200 of them, kept as a count on each reign.
- Javelin: the old-implement chain is censored when the new specification took effect (men 1 April 1986, women 1 April 1999). The new chain starts with the first record on the new javelin.
- Combined events: scoring tables changed (1964 and 1985 tables for the decathlon), so every listed non-equal mark ends the reign. Improvement is measured on the 1985 tables where the handbook gives them.
- Two records on the same day (a heat and a final, or two rounds of a field event) give a zero-length reign. Those 43 reigns are set to one day.
- Improvement over the previous record is capped at 5% in the models. The largest raw value is 38% (Hargus's 37.575 javelin in 1927 after 27.24 in 1924), and the cap keeps a handful of 1920s women's throws from steering the margin effect.

## Validation flags

`validate.py` lists every reign longer than 20 years and every improvement more than four times the event's median step and above 1% (3% for combined events). It found 32 long reigns and 63 large jumps, all in `tables/validation_flags.csv`. Each was looked up in the Wikipedia cross-check: 94 match on mark and year there. Four have hand notes in `sources/flag_notes.csv`:

- the 1968 US 4x400 m, the one flag Wikipedia does not match, because it prints 2:56.16 for the same race;
- three early women's throws with steps above 10%, which need a note even though Wikipedia has them (Reuter's 38.34 discus in 1926, Dumbadze's 53.25 discus in 1948 after a 12-year gap that includes the war, Hargus's javelin above). All three are in both sources. They stay in, with the improvement cap.

Of the 32 long reigns, 16 are records standing today. The other 16 ended, from Ahearn's 15.52 triple jump (1911 to 1931) to Kostadinova's 2.09 high jump (1987 to 2024) and Schult's 74.08 discus (1986 to 2024).

## The old women's 800 m problem

The July 2026 version scraped Wikipedia and passed a completeness gate that checked only the end point and start year. Its women's 800 m had three reigns, one of them a 1:54.0 from 1925 that stood 55 years. The handbook lists 29 women's 800 m records from 1922, the fastest 1920s mark being 2:16.8 (Radke, 1928). The 1925 "1:54.0" was a misread row. The rebuilt event has 27 reigns, the longest of them Kratochvílová's 1:53.28, standing since 1983.

## Handbook count check

The handbook prints its own count of records per event. My parse matches it in 37 of 42 track and field lists. In the other five the parse finds one or two more or fewer rows than the printed count (men's 10,000 m 38 against 36, women's 200 m 27 against 26, women's 400 m 26 against 27, women's 400 m hurdles 20 against 19, women's 4x100 m 46 against 47). For those five I counted the record lines in the PDF text directly: the parse picks up every one of them (38, 27, 26, 20 and 46), so the differences are in the printed counts.
