# SOXX and QQQ: Model C against the frozen rule, with Model A as frozen

Produced by `run.py` from `registration.json` (sha256 `7c101ff6f156…`), code commit `53545e2`. Every definition is in the registration. This file only formats `report.json`.

## The frozen rule, Model A and Model C over one period

### SOXX

| | Frozen rule | Model A, as frozen | Model C |
| --- | ---: | ---: | ---: |
| First decision | 2013-11-01 | 2013-11-01 | 2013-11-01 |
| Last session | 2026-11-20 | 2026-11-20 | 2026-11-20 |
| Decisions (held) | 3283 (0) | 3283 (0) | 3283 (0) |
| Decisions at 100% / 50% / 0% | 2126 / 886 / 271 | 2098 / 429 / 756 | 2048 / 563 / 672 |
| Max drawdown | -38.0% | -46.2% | -45.2% |
| Final equity | 10.05 | 4.50 | 7.20 |
| Buy and hold: max drawdown / final equity | -46.2% / 16.15 | -46.2% / 16.15 | -46.2% / 16.15 |
| Episodes (unfinished) | 34 (1) | 14 (0) | 15 (0) |
| Whipsaw rate | 63.6% | 64.3% | 73.3% |
| Re-entry cost, all finished episodes | +2.4% | +21.4% | +19.5% |
| Turnover per year | 15.4 | 7.0 | 9.1 |
| Recall on the worst 15% of forward drawdowns | 54.6% | 28.2% | 30.5% |
| Share of decisions under an alert | 35.0% | 36.3% | 37.8% |
| Forward drawdown, stressed minus ordinary | -2.5% | +0.8% | +0.5% |
| Its 90% interval | -3.5% to -1.2% | -0.8% to +2.0% | -1.0% to +1.9% |
| Worst windows labelled as the left-tail class | 56.4% | 56.4% | 56.4% |

Registered limits: drawdown smaller by points: not met; final equity share of rule: not met; whipsaw within points: not met. **Decision: the rule is kept.**

Sensitivity runs for Model C, which decide nothing (max drawdown / final equity, then the rule's over the same period):

- cost x2: -45.2% / 6.78; rule -38.6% / 9.09
- cost x3: -45.2% / 6.39; rule -39.2% / 8.22
- rolling 5y: -41.1% / 4.86; rule -38.0% / 10.05
- embargo 5: -45.2% / 8.50; rule -38.0% / 10.05

Max drawdown / final equity at each cost level:

| Cost | Frozen rule | Model A, as frozen | Model C |
| --- | ---: | ---: | ---: |
| 1x | -38.0% / 10.05 | -46.2% / 4.50 | -45.2% / 7.20 |
| 2x | -38.6% / 9.09 | -46.2% / 4.30 | -45.2% / 6.78 |
| 3x | -39.2% / 8.22 | -46.2% / 4.11 | -45.2% / 6.39 |

### QQQ

| | Frozen rule | Model A, as frozen | Model C |
| --- | ---: | ---: | ---: |
| First decision | 2013-11-01 | 2013-11-01 | 2013-11-01 |
| Last session | 2026-11-20 | 2026-11-20 | 2026-11-20 |
| Decisions (held) | 3283 (0) | 3283 (0) | 3283 (0) |
| Decisions at 100% / 50% / 0% | 2222 / 827 / 234 | 2001 / 443 / 839 | 2196 / 379 / 708 |
| Max drawdown | -17.9% | -35.6% | -28.8% |
| Final equity | 5.62 | 4.25 | 4.00 |
| Buy and hold: max drawdown / final equity | -35.6% / 7.33 | -35.6% / 7.33 | -35.6% / 7.33 |
| Episodes (unfinished) | 38 (1) | 17 (0) | 18 (0) |
| Whipsaw rate | 62.2% | 64.7% | 77.8% |
| Re-entry cost, all finished episodes | +0.7% | +9.3% | +7.2% |
| Turnover per year | 14.4 | 8.4 | 6.4 |
| Recall on the worst 15% of forward drawdowns | 52.6% | 28.2% | 26.4% |
| Share of decisions under an alert | 32.2% | 39.3% | 33.3% |
| Forward drawdown, stressed minus ordinary | -1.9% | +0.5% | +0.3% |
| Its 90% interval | -2.8% to -0.7% | -0.7% to +1.4% | -0.4% to +1.6% |
| Worst windows labelled as the left-tail class | 55.2% | 55.2% | 55.2% |

Registered limits: drawdown smaller by points: not met; final equity share of rule: not met; whipsaw within points: not met. **Decision: the rule is kept.**

Sensitivity runs for Model C, which decide nothing (max drawdown / final equity, then the rule's over the same period):

- cost x2: -28.8% / 3.84; rule -18.2% / 5.12
- cost x3: -28.8% / 3.68; rule -18.7% / 4.66
- rolling 5y: -22.9% / 4.53; rule -17.9% / 5.62
- embargo 5: -29.7% / 4.22; rule -17.9% / 5.62

Max drawdown / final equity at each cost level:

| Cost | Frozen rule | Model A, as frozen | Model C |
| --- | ---: | ---: | ---: |
| 1x | -17.9% / 5.62 | -35.6% / 4.25 | -28.8% / 4.00 |
| 2x | -18.2% / 5.12 | -35.6% / 4.02 | -28.8% / 3.84 |
| 3x | -18.7% / 4.66 | -35.6% / 3.81 | -28.8% / 3.68 |

## Limits

- About 45 independent stress episodes per fund over the whole sample, fewer in the comparison period. That is the sample size for any difference between two gates.
- The gate thresholds, class bounds and feature definitions come from RFC-002 and RFC-004, which were written knowing this period.
- The rule's full-period results were known when this was drafted, so the promotion limits were set with that knowledge.
- Cboe, Yahoo and Treasury publication times are assumptions. Prices exclude dividends.
- The revenue factor changes once a month while outcomes span 20 sessions; a real effect would have to be large to show.
- The first-release revenue values were read through a page reader that returns quoted fragments; the numbers were cross-checked, the wording was not.
- Model A's penalty of 10 and the 750-decision minimum are judgements made without tuning; RFC-004 gives neither.
- The same held-out months were already used for Model A, and the expected direction of this result was known in advance, so it is weak evidence either way.
