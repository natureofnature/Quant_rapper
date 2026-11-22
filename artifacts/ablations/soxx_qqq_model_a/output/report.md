# SOXX and QQQ: Model A comparisons

Produced by `run.py` from `registration.json` (sha256 `742ae75f072a…`), code commit `03c6c82`. Every definition is in the registration. This file only formats `report.json`.

## Comparison 1: Model A's gate against the frozen rule

### SOXX

| | Frozen rule | Model A |
| --- | ---: | ---: |
| First decision | 2013-11-01 | 2013-11-01 |
| Last session | 2026-11-20 | 2026-11-20 |
| Decisions (held) | 3283 (0) | 3283 (0) |
| Decisions at 100% / 50% / 0% | 2126 / 886 / 271 | 2098 / 429 / 756 |
| Max drawdown | -38.0% | -46.2% |
| Final equity | 10.05 | 4.50 |
| Buy and hold: max drawdown / final equity | -46.2% / 16.15 | -46.2% / 16.15 |
| Episodes (unfinished) | 34 (1) | 14 (0) |
| Whipsaw rate | 63.6% | 64.3% |
| Re-entry cost, all finished episodes | +2.4% | +21.4% |
| Turnover per year | 15.4 | 7.0 |
| Recall on the worst 15% of forward drawdowns | 54.6% | 28.2% |
| Share of decisions under an alert | 35.0% | 36.3% |
| Forward drawdown, stressed minus ordinary | -2.5% | +0.8% |
| Its 90% interval | -3.5% to -1.2% | -0.8% to +2.0% |
| Worst windows labelled as the left-tail class | 56.4% | 56.4% |

Registered limits: drawdown smaller by points: not met; final equity share of rule: not met; whipsaw within points: met. **Decision: the rule is kept.**

Sensitivity runs, which decide nothing (model max drawdown / final equity, then the rule's over the same period):

- cost x2: -46.2% / 4.30; rule -38.6% / 9.09
- cost x3: -46.2% / 4.11; rule -39.2% / 8.22
- rolling 5y: -43.3% / 2.29; rule -38.0% / 10.05
- embargo 5: -46.0% / 4.58; rule -38.0% / 10.05
- tail weight 1.5: -46.2% / 3.81; rule -38.0% / 10.05

### QQQ

| | Frozen rule | Model A |
| --- | ---: | ---: |
| First decision | 2013-11-01 | 2013-11-01 |
| Last session | 2026-11-20 | 2026-11-20 |
| Decisions (held) | 3283 (0) | 3283 (0) |
| Decisions at 100% / 50% / 0% | 2222 / 827 / 234 | 2001 / 443 / 839 |
| Max drawdown | -17.9% | -35.6% |
| Final equity | 5.62 | 4.25 |
| Buy and hold: max drawdown / final equity | -35.6% / 7.33 | -35.6% / 7.33 |
| Episodes (unfinished) | 38 (1) | 17 (0) |
| Whipsaw rate | 62.2% | 64.7% |
| Re-entry cost, all finished episodes | +0.7% | +9.3% |
| Turnover per year | 14.4 | 8.4 |
| Recall on the worst 15% of forward drawdowns | 52.6% | 28.2% |
| Share of decisions under an alert | 32.2% | 39.3% |
| Forward drawdown, stressed minus ordinary | -1.9% | +0.5% |
| Its 90% interval | -2.8% to -0.7% | -0.7% to +1.4% |
| Worst windows labelled as the left-tail class | 55.2% | 55.2% |

Registered limits: drawdown smaller by points: not met; final equity share of rule: not met; whipsaw within points: met. **Decision: the rule is kept.**

Sensitivity runs, which decide nothing (model max drawdown / final equity, then the rule's over the same period):

- cost x2: -35.6% / 4.02; rule -18.2% / 5.12
- cost x3: -35.6% / 3.81; rule -18.7% / 4.66
- rolling 5y: -31.0% / 3.82; rule -17.9% / 5.62
- embargo 5: -35.6% / 3.98; rule -17.9% / 5.62
- tail weight 1.5: -35.6% / 4.28; rule -17.9% / 5.62

## Comparison 2: Model A with and without the revenue factor

### SOXX

| | Without the factor | With the factor |
| --- | ---: | ---: |
| First decision | 2013-11-01 | 2013-11-01 |
| Last session | 2026-11-20 | 2026-11-20 |
| Decisions (held) | 3283 (2) | 3283 (2) |
| Decisions at 100% / 50% / 0% | 2100 / 428 / 755 | 2054 / 425 / 804 |
| Max drawdown | -46.2% | -46.2% |
| Final equity | 4.67 | 4.24 |
| Buy and hold: max drawdown / final equity | -46.2% / 16.15 | -46.2% / 16.15 |
| Episodes (unfinished) | 14 (0) | 14 (0) |
| Whipsaw rate | 64.3% | 71.4% |
| Re-entry cost, all finished episodes | +21.2% | +21.2% |
| Turnover per year | 7.1 | 7.8 |
| Recall on the worst 15% of forward drawdowns | 28.2% | 28.2% |
| Share of decisions under an alert | 36.3% | 37.7% |
| Forward drawdown, stressed minus ordinary | +0.8% | +0.9% |
| Its 90% interval | -0.8% to +2.0% | -0.6% to +2.0% |
| Worst windows labelled as the left-tail class | 56.4% | 56.4% |

Paired on the same 489 worst outcomes in 29 realised episodes: recall 28.2% without, 28.2% with, difference +0.0%, 90% interval -1.5% to +1.7%. Share of eligible decisions under an alert: 36.3% without, 37.7% with.

Coverage: 3263 eligible decisions, 2 held, 29 episodes; sufficient.

Evidence rule: recall higher by points: not met; alert share rise within points: met; interval above zero: not met; max drawdown not larger: met. **Status: completed, evidence threshold not met (a null result).**

## Limits

- About 45 independent stress episodes per fund over the whole sample, fewer in the comparison period. That is the sample size for any difference between two gates.
- The gate thresholds, class bounds and feature definitions come from RFC-002 and RFC-004, which were written knowing this period.
- The rule's full-period results were known when this was drafted, so the promotion limits were set with that knowledge.
- Cboe, Yahoo and Treasury publication times are assumptions. Prices exclude dividends.
- The revenue factor changes once a month while outcomes span 20 sessions; a real effect would have to be large to show.
- The first-release revenue values were read through a page reader that returns quoted fragments; the numbers were cross-checked, the wording was not.
- Model A's penalty of 10 and the 750-decision minimum are judgements made without tuning; RFC-004 gives neither.
