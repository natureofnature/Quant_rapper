# SOXX and QQQ: a wider input set for the risk gate (RFC-005)

Produced by `run.py` from `registration.json` (sha256 `dafbf4772974…`), code commit `a86e9a8`. Every definition is in the registration. This file only formats `report.json`.

## Decision

- **SOXX**: the rule is kept.
- **QQQ**: the rule is kept.

Stopping rule: met: no candidate is promoted for either fund; no variant of this list, these targets or this model is tried on the test period.

Only the two wide arms can replace the rule, and only on the test period. The 3-input arms are controls, scored on the same decisions.

## SOXX: Test period

2020-01-02 to 2026-11-20, 1732 decisions. Buy and hold: maximum drawdown -46.2%, final equity 4.40.

| Arm | Max drawdown | Final equity | Alert rate | Tail recall | Episodes | Whipsaw | Turnover a year | Held decisions |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Frozen rule | -38.0% | 4.06 | 38.0% | 56.6% | 16 | 60.0% | 13.9 | 0 |
| 3 inputs, forward average | -41.9% | 1.52 | 47.0% | 32.4% | 13 | 76.9% | 14.4 | 5 |
| 33 inputs, forward average | -37.2% | 3.91 | 38.3% | 42.2% | 15 | 60.0% | 11.0 | 5 |
| 3 inputs, drawdown | -42.2% | 1.88 | 49.1% | 46.5% | 13 | 69.2% | 18.6 | 5 |
| 33 inputs, drawdown | -40.4% | 4.54 | 40.7% | 52.3% | 11 | 60.0% | 11.0 | 5 |

Promotion limits against the rule:

| Candidate | Drawdown smaller by 2 points | Equity at least 95% of the rule's | Whipsaw within 5 points | All |
| --- | --- | --- | --- | --- |
| 33 inputs, forward average | no | yes | yes | no |
| 33 inputs, drawdown | no | yes | yes | no |

Recall on the same realised drawdown episodes, second arm minus first (descriptive, promotes nothing):

| Contrast | Episodes | Recall, first | Recall, second | Difference | 90% interval |
| --- | ---: | ---: | ---: | ---: | --- |
| avg: W minus N | 13 | 32.4% | 42.2% | +9.8 points | -19.5 to +32.8 points |
| dd: W minus N | 13 | 46.5% | 52.3% | +5.9 points | -26.8 to +31.1 points |
| N: dd minus avg | 13 | 32.4% | 46.5% | +14.1 points | -3.6 to +34.6 points |
| W: dd minus avg | 13 | 42.2% | 52.3% | +10.2 points | -11.1 to +31.2 points |

At higher costs (deciding nothing): 2× cost: rule -38.6% / 3.87, N-avg -42.3% / 1.44, W-avg -37.3% / 3.77, N-dd -42.4% / 1.76, W-dd -40.6% / 4.37; 3× cost: rule -39.2% / 3.69, N-avg -42.7% / 1.37, W-avg -37.5% / 3.63, N-dd -42.7% / 1.65, W-dd -40.7% / 4.20.

## SOXX: Development period (already seen when the comparison was fixed)

2013-11-07 to 2019-12-31, 1547 decisions. Buy and hold: maximum drawdown -26.1%, final equity 3.73.

| Arm | Max drawdown | Final equity | Alert rate | Tail recall | Episodes | Whipsaw | Turnover a year | Held decisions |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Frozen rule | -25.1% | 2.51 | 32.2% | 47.6% | 18 | 66.7% | 17.1 | 0 |
| 3 inputs, forward average | -28.5% | 3.35 | 38.0% | 39.3% | 14 | 61.5% | 14.2 | 0 |
| 33 inputs, forward average | -24.3% | 3.34 | 35.5% | 50.7% | 14 | 53.8% | 11.4 | 0 |
| 3 inputs, drawdown | -24.8% | 2.54 | 45.4% | 61.6% | 11 | 60.0% | 17.7 | 0 |
| 33 inputs, drawdown | -16.5% | 3.08 | 44.2% | 65.9% | 11 | 60.0% | 11.2 | 0 |

Promotion limits against the rule:

| Candidate | Drawdown smaller by 2 points | Equity at least 95% of the rule's | Whipsaw within 5 points | All |
| --- | --- | --- | --- | --- |
| 33 inputs, forward average | no | yes | yes | no |
| 33 inputs, drawdown | yes | yes | yes | yes |

Recall on the same realised drawdown episodes, second arm minus first (descriptive, promotes nothing):

| Contrast | Episodes | Recall, first | Recall, second | Difference | 90% interval |
| --- | ---: | ---: | ---: | ---: | --- |
| avg: W minus N | 15 | 39.3% | 50.7% | +11.4 points | +0.0 to +20.7 points |
| dd: W minus N | 15 | 61.6% | 65.9% | +4.4 points | -10.6 to +21.0 points |
| N: dd minus avg | 15 | 39.3% | 61.6% | +22.3 points | +1.5 to +40.2 points |
| W: dd minus avg | 15 | 50.7% | 65.9% | +15.3 points | +1.4 to +30.5 points |

At higher costs (deciding nothing): 2× cost: rule -26.0% / 2.38, N-avg -29.3% / 3.21, W-avg -24.6% / 3.22, N-dd -26.0% / 2.41, W-dd -16.7% / 2.98; 3× cost: rule -26.9% / 2.26, N-avg -30.1% / 3.07, W-avg -24.9% / 3.11, N-dd -27.1% / 2.28, W-dd -16.9% / 2.88.

## QQQ: Test period

2020-01-02 to 2026-11-20, 1732 decisions. Buy and hold: maximum drawdown -35.6%, final equity 2.85.

| Arm | Max drawdown | Final equity | Alert rate | Tail recall | Episodes | Whipsaw | Turnover a year | Held decisions |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Frozen rule | -17.9% | 2.82 | 34.7% | 62.9% | 19 | 66.7% | 12.2 | 0 |
| 3 inputs, forward average | -28.8% | 2.25 | 45.6% | 50.8% | 14 | 61.5% | 11.7 | 5 |
| 33 inputs, forward average | -26.3% | 2.38 | 42.9% | 48.8% | 13 | 66.7% | 8.7 | 5 |
| 3 inputs, drawdown | -24.1% | 2.10 | 34.5% | 48.4% | 13 | 50.0% | 12.2 | 5 |
| 33 inputs, drawdown | -22.3% | 2.78 | 39.7% | 69.5% | 10 | 55.6% | 9.9 | 5 |

Promotion limits against the rule:

| Candidate | Drawdown smaller by 2 points | Equity at least 95% of the rule's | Whipsaw within 5 points | All |
| --- | --- | --- | --- | --- |
| 33 inputs, forward average | no | no | yes | no |
| 33 inputs, drawdown | no | yes | yes | no |

Recall on the same realised drawdown episodes, second arm minus first (descriptive, promotes nothing):

| Contrast | Episodes | Recall, first | Recall, second | Difference | 90% interval |
| --- | ---: | ---: | ---: | ---: | --- |
| avg: W minus N | 16 | 50.8% | 48.8% | -2.0 points | -25.0 to +14.6 points |
| dd: W minus N | 16 | 48.4% | 69.5% | +21.1 points | +5.2 to +31.6 points |
| N: dd minus avg | 16 | 50.8% | 48.4% | -2.3 points | -22.5 to +11.5 points |
| W: dd minus avg | 16 | 48.8% | 69.5% | +20.7 points | -5.1 to +35.2 points |

At higher costs (deciding nothing): 2× cost: rule -18.2% / 2.71, N-avg -28.8% / 2.16, W-avg -26.4% / 2.31, N-dd -24.3% / 2.01, W-dd -23.0% / 2.69; 3× cost: rule -18.6% / 2.60, N-avg -28.8% / 2.07, W-avg -26.5% / 2.24, N-dd -24.5% / 1.93, W-dd -23.7% / 2.60.

## QQQ: Development period (already seen when the comparison was fixed)

2013-11-07 to 2019-12-31, 1547 decisions. Buy and hold: maximum drawdown -23.2%, final equity 2.60.

| Arm | Max drawdown | Final equity | Alert rate | Tail recall | Episodes | Whipsaw | Turnover a year | Held decisions |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Frozen rule | -17.7% | 2.02 | 29.9% | 43.2% | 19 | 57.9% | 16.9 | 0 |
| 3 inputs, forward average | -23.9% | 1.42 | 32.5% | 35.4% | 14 | 76.9% | 12.2 | 0 |
| 33 inputs, forward average | -17.0% | 1.74 | 39.5% | 34.9% | 11 | 60.0% | 13.1 | 0 |
| 3 inputs, drawdown | -16.7% | 1.38 | 41.9% | 59.0% | 16 | 81.2% | 16.9 | 0 |
| 33 inputs, drawdown | -12.1% | 1.66 | 48.5% | 73.4% | 10 | 77.8% | 13.4 | 0 |

Promotion limits against the rule:

| Candidate | Drawdown smaller by 2 points | Equity at least 95% of the rule's | Whipsaw within 5 points | All |
| --- | --- | --- | --- | --- |
| 33 inputs, forward average | no | no | yes | no |
| 33 inputs, drawdown | yes | no | no | no |

Recall on the same realised drawdown episodes, second arm minus first (descriptive, promotes nothing):

| Contrast | Episodes | Recall, first | Recall, second | Difference | 90% interval |
| --- | ---: | ---: | ---: | ---: | --- |
| avg: W minus N | 10 | 35.4% | 34.9% | -0.4 points | -17.7 to +13.3 points |
| dd: W minus N | 10 | 59.0% | 73.4% | +14.4 points | -4.9 to +32.8 points |
| N: dd minus avg | 10 | 35.4% | 59.0% | +23.6 points | -2.6 to +40.2 points |
| W: dd minus avg | 10 | 34.9% | 73.4% | +38.4 points | +22.2 to +49.4 points |

At higher costs (deciding nothing): 2× cost: rule -18.2% / 1.92, N-avg -24.0% / 1.36, W-avg -17.2% / 1.67, N-dd -18.7% / 1.31, W-dd -12.5% / 1.59; 3× cost: rule -18.7% / 1.82, N-avg -24.2% / 1.31, W-avg -17.3% / 1.61, N-dd -20.6% / 1.24, W-dd -12.9% / 1.53.

## Inputs

`vix_term_structure`, `real_yield_10y_change_20_rank_250`, `trend_200`, `vix_log`, `vix_change_5`, `vvix_over_vix`, `skew`, `vxn_over_vix`, `move_log`, `move_change_20`, `slope_10y_3m`, `slope_30y_5y`, `nominal_10y_change_20`, `bill_3m_change_60`, `hyg_ief_roc_20`, `hyg_ief_roc_60`, `lqd_ief_roc_20`, `hyg_lqd_trend_200`, `dollar_roc_20`, `copper_gold_roc_60`, `oil_roc_20`, `equal_weight_roc_60`, `small_cap_roc_60`, `utilities_roc_60`, `spy_implied_minus_realised`, `sessions_to_fomc`, `soxx_over_qqq_roc_60`, `roc_20`, `roc_60`, `roc_120`, `below_250_high`, `realised_vol_20`, `realised_vol_ratio_20_120`.

Dropped before registration: none.

## Limits

- 2020-2026 is a later-period test of the added inputs. RFC-004 already showed how the rule and two models behaved on those outcomes, and the promotion limits were set knowing the rule's results.
- The model, the input list and the 15% share were fixed after the development-period results were seen.
- Seven years hold few large drawdowns (2020, 2022, 2025). A result rests mostly on those.
- Every publication time of the added series is an assumption; none is sourced. Yahoo is an aggregator, and its histories of indices and futures can be restated.
- The inputs were chosen by hand and several share a series (five use VIX, four use HYG or LQD). This tests broader inputs, not graph-based factor selection.
- VVIX, SKEW and VXN histories include values back-calculated by the exchange before each index was launched.
- The futures series are continuous front-month prices and jump at contract rolls.
- The FOMC schedule is treated as known in advance at every decision; the publication date of each year's schedule was not checked.
- Prices exclude dividends.
