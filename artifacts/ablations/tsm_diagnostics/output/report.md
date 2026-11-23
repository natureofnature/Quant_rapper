# TSM diagnostics (exploratory)

Produced by `run.py` from `registration.json` (sha256 `b397634f0a30…`), code commit `e38529d`. Every definition is in the registration. This file only formats `report.json`.

Exploratory throughout. The 289 revenue releases were already examined against VIX state on unadjusted returns, and every decision predates the frozen prior. These diagnostics describe where TSM's variance sits and whether a beta adjustment changes the earlier picture. They are not a test, and no result here licenses a trading rule.

## Part 1: where TSM's daily variance sits

6012 sessions from 2002-12-31. Betas are fitted on the 250 sessions before each day.

| Adjusted for | Share of TSM's return variance left in the residual |
| --- | ---: |
| SPY | 60.7% |
| SOXX | 44.6% |
| SPY and SOXX | 43.6% |

Residual after SOXX, by year: 2003 45.5%, 2004 42.9%, 2005 66.9%, 2006 48.6%, 2007 55.5%, 2008 40.2%, 2009 49.5%, 2010 52.3%, 2011 38.9%, 2012 56.2%, 2013 69.4%, 2014 69.4%, 2015 63.1%, 2016 48.0%, 2017 71.8%, 2018 43.5%, 2019 41.7%, 2020 39.6%, 2021 35.8%, 2022 35.2%, 2023 44.3%, 2024 41.0%, 2025 34.3%, 2026 32.3%.

### Announcement sessions in the SOXX-adjusted residual

| Type | Sessions | Share of sessions | Share of squared residual | Ratio |
| --- | ---: | ---: | ---: | ---: |
| cpi | 286 | 4.8% | 4.3% | 0.90 |
| ppi | 286 | 4.8% | 5.4% | 1.13 |
| fomc | 200 | 3.3% | 3.9% | 1.18 |
| tsmc earnings | 96 | 1.6% | 9.8% | 6.12 |
| tsmc monthly revenue | 287 | 4.8% | 4.5% | 0.94 |
| opex | 287 | 4.8% | 4.3% | 0.89 |
| any catalyst | 1314 | 21.9% | 28.4% | 1.30 |

Shock audit: 76 of 4501 non-announcement sessions have a residual beyond 2.5 trailing standard deviations. They are listed in `shocks.csv` for review against the research map; none is explained here.

## Part 2: the monthly revenue event study

289 releases, 287 with a pre-event beta, 40 whose entry session is also a CPI, PPI, FOMC or TSMC earnings session.

### Announcement-window returns by state (not drift)

| State split | Window | Measure | n high / low | Mean high | Mean low | Difference | In standard errors | Episodes high / low |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| vix_term_structure | reaction | raw | 16 / 190 | +0.34% | +0.22% | +0.12% | +0.27 | 11 / 1 |
| vix_term_structure | reaction | relative | 16 / 190 | +0.00% | +0.18% | -0.18% | -0.61 | 11 / 1 |
| vix_term_structure | reaction | abnormal | 16 / 190 | +0.21% | +0.18% | +0.03% | +0.13 | 11 / 1 |
| vix_term_structure | entry day | raw | 16 / 190 | -0.36% | -0.10% | -0.25% | -0.69 | 11 / 1 |
| vix_term_structure | entry day | relative | 16 / 190 | -0.38% | -0.11% | -0.27% | -0.86 | 11 / 1 |
| vix_term_structure | entry day | abnormal | 16 / 190 | -0.32% | -0.11% | -0.21% | -0.70 | 11 / 1 |
| vix_term_structure | window | raw | 16 / 190 | +0.02% | +0.58% | -0.56% | -0.39 | 11 / 1 |
| vix_term_structure | window | relative | 16 / 190 | -0.49% | -0.17% | -0.32% | -0.40 | 11 / 1 |
| vix_term_structure | window | abnormal | 16 / 190 | -0.36% | -0.02% | -0.34% | -0.45 | 11 / 1 |
| vix_level | reaction | raw | 93 / 196 | +0.32% | +0.18% | +0.14% | +0.69 | 18 / 10 |
| vix_level | reaction | relative | 93 / 196 | +0.17% | +0.14% | +0.03% | +0.17 | 18 / 10 |
| vix_level | reaction | abnormal | 91 / 196 | +0.21% | +0.14% | +0.08% | +0.52 | 18 / 10 |
| vix_level | entry day | raw | 93 / 196 | -0.13% | -0.14% | +0.01% | +0.03 | 18 / 10 |
| vix_level | entry day | relative | 93 / 196 | +0.06% | -0.13% | +0.19% | +0.99 | 18 / 10 |
| vix_level | entry day | abnormal | 91 / 196 | +0.04% | -0.13% | +0.16% | +0.89 | 18 / 10 |
| vix_level | window | raw | 93 / 196 | +0.68% | -0.06% | +0.74% | +1.08 | 18 / 10 |
| vix_level | window | relative | 93 / 196 | +0.06% | -0.44% | +0.50% | +1.09 | 18 / 10 |
| vix_level | window | abnormal | 91 / 196 | +0.40% | -0.35% | +0.75% | +1.69 | 18 / 10 |

### Announcement-day volatility against other sessions in the same state

Mean absolute abnormal return, open to close.

| State split | Sessions | High: announcement / other | Low: announcement / other | Difference in differences | Standard error |
| --- | --- | ---: | ---: | ---: | ---: |
| vix_term_structure | all sessions | +0.99% / +0.93% | +0.81% / +0.71% | -0.04% | +0.17% |
| vix_term_structure | without collision sessions | +1.03% / +0.90% | +0.86% / +0.70% | -0.03% | +0.22% |
| vix_level | all sessions | +1.11% / +1.06% | +0.85% / +0.75% | -0.04% | +0.13% |
| vix_level | without collision sessions | +1.18% / +1.04% | +0.89% / +0.74% | -0.01% | +0.15% |

The standard errors treat sessions as independent, which they are not, so they understate the uncertainty.

### Listed event by event: vix_term_structure at_or_above 1.0 (16 events)

| Event | Entry | State | Beta | Reaction, abnormal | Entry day, abnormal | 5 sessions, abnormal | Collisions |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| 2010-04 | 2010-05-10 | 1.118 | 0.69 | -0.52% | +1.05% | -0.91% | COLLISION_FOMC |
| 2011-07 | 2011-08-10 | 1.128 | 0.79 | +0.24% | -2.50% | -2.20% |  |
| 2011-08 | 2011-09-09 | 1.018 | 0.83 | -0.05% | -0.07% | -5.84% |  |
| 2011-10 | 2011-11-10 | 1.019 | 0.82 | -0.13% | +1.10% | -1.05% |  |
| 2013-09 | 2013-10-09 | 1.025 | 0.77 | +0.97% | -1.82% | +1.47% |  |
| 2015-06 | 2015-07-10 | 1.010 | 0.66 | -0.24% | +0.60% | +0.33% |  |
| 2015-12 | 2016-01-08 | 1.015 | 0.70 | +0.32% | -0.68% | +4.09% |  |
| 2018-01 | 2018-02-09 | 1.224 | 0.63 | -0.48% | +1.42% | +3.04% |  |
| 2018-03 | 2018-04-10 | 1.025 | 0.68 | +0.29% | -1.26% | -3.81% | COLLISION_PPI |
| 2018-11 | 2018-12-10 | 1.035 | 0.76 | -0.31% | -0.91% | -0.95% |  |
| 2019-04 | 2019-05-10 | 1.036 | 0.70 | -0.26% | -0.82% | -1.53% | COLLISION_CPI |
| 2020-02 | 2020-03-10 | 1.265 | 0.76 | +0.43% | -0.26% | -1.64% |  |
| 2020-03 | 2020-04-13 | 1.026 | 0.76 | +1.14% | -1.13% | +5.05% | COLLISION_CPI |
| 2025-02 | 2025-03-10 | 1.035 | 0.95 | -0.28% | +1.04% | -0.15% |  |
| 2025-03 | 2025-04-10 | 1.124 | 0.87 | +2.27% | +0.17% | +1.13% | COLLISION_CPI |
| 2026-02 | 2026-03-10 | 1.006 | 0.79 | -0.05% | -0.98% | -2.72% |  |

## Not done

- Key customer earnings dates: not collected, so they are not a catalyst type.
- The guidance-surprise table of RFC-002 section 3.3: it needs TSMC's quarterly revenue guidance history and exchange rates as of each date, neither of which is held. It is recorded as not done, and revenue growth is not used in its place.

## Limits

- Prices exclude dividends and are Yahoo's.
- Announcement times of day were not collected: a 08:30 release and a 14:00 statement are both assigned to their session.
- TSMC earnings dates from 2013Q4 rest on one reading of the company's calendar.
- Session-level volatility comparisons use sessions that are not independent.

## Amendments

- 2026-11-22: The code pin moves to a runner that leaves out a year with a single evaluated session and writes strict JSON. No definition changed. The first run wrote NaN for 2002's variance share, which is not valid JSON. Every other figure of the first run is reproduced exactly by the corrected runner.
- 2026-11-22: Two reported counts are corrected; no definition, return or listed session changes. (1) Stress episodes are counted from entry dates: an event starts a new episode unless it enters fewer than 3 calendar months after the one before, and each outcome's count uses only the events with a value for that outcome. (2) The shock audit's denominator counts the sessions the screen could flag: no catalyst day, a residual, and 250 residuals before it. An outside review found that episodes were counted from revenue-month numbers, which drops the day, and over every event of a group whether or not it had the outcome; and that the denominator required only the day's own residual. Effect: the below-1 term-structure group spans 1 episode, not 4; the VIX-below-20 group 10, not 11; the stressed groups are unchanged at 11 and 18. Eligible sessions are 4,501, not 4,698; the 76 listed sessions, events.csv and shocks.csv are identical.
