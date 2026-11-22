# SOXX/QQQ rule baseline

Produced by `run.py` from `registration.json` (sha256 `f965d9d2f5db…`), code commit `816deb4`. Every definition is in the registration. This file only formats `report.json`.

| | SOXX | QQQ |
| --- | ---: | ---: |
| First decision | 2009-09-18 | 2009-09-18 |
| Last decision, positions | 2026-11-20 | 2026-11-20 |
| Last decision, forward measure | 2026-10-23 | 2026-10-23 |
| Decisions | 4321 | 4321 |
| Decisions at 100% / 50% / 0% | 2636 / 1348 / 337 | 2918 / 1123 / 280 |
| Sessions holding the previous target | 0 (longest run 0) | 0 (longest run 0) |
| Max drawdown, gate | -38.0% | -17.9% |
| Max drawdown, buy and hold | -46.2% | -35.6% |
| Final equity, gate | 11.33 | 8.02 |
| Final equity, buy and hold | 24.90 | 14.42 |
| Forward drawdown, stressed: mean / median / 5th pct | -5.1% / -3.7% / -15.5% | -3.5% / -2.4% / -12.2% |
| Forward drawdown, ordinary: mean / median / 5th pct | -3.3% / -2.2% / -10.6% | -2.2% / -1.4% / -7.3% |
| Sessions (runs), stressed | 1669 (43) | 1393 (50) |
| Sessions (runs), ordinary | 1898 (43) | 2147 (50) |
| Stressed minus ordinary, mean | -1.8% | -1.4% |
| 90% interval | -2.7% to -0.7% | -2.3% to -0.3% |
| Decisions at 100% inside an episode (in neither group) | 738 | 771 |
| Episodes (unfinished) | 43 (1) | 50 (1) |
| Episodes crossing the end of the forward period | 1 | 1 |
| Whipsaw rate | 64% | 65% |
| Re-entry cost, whipsaw episodes | +6.9% | +3.4% |
| Re-entry cost, all finished episodes | +2.8% | +1.2% |

## Reading, by the registered rule

- **SOXX**: separated drawdown periods: yes (the interval lies wholly below zero). Reduced maximum drawdown: yes.
- **QQQ**: separated drawdown periods: yes (the interval lies wholly below zero). Reduced maximum drawdown: yes.

The reading rule says nothing about return. Final equity is in the table: the gate ended well below buy and hold for both funds, on prices that exclude dividends.

## Limits

- VIX/VIX3M at or above 1 was already examined against TSM minus SOXX around 289 TSMC revenue releases. It had not been examined against SOXX or QQQ drawdowns.
- The thresholds come from RFC-002 and common practice, so they are not free of knowledge of this period, which includes 2020 and 2022.
- Sessions inside one episode are not independent; the number of episodes, not of sessions, is the sample size.
- Cboe, Yahoo and Treasury publication times are assumptions, so no decision is timing-verified.
- Prices are Yahoo's on the snapshot's split basis, exclude dividends, and were not checked against an exchange. Treasury values are those visible on the download date.
- High-yield credit spread widening, named in RFC-002, is not in this version: the public history of the spread series is limited to a rolling three years. A later credit proxy takes its own factor id and its own evaluation; it does not join this rule.
