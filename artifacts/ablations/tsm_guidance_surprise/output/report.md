# TSM guidance surprise (exploratory)

Produced by `run.py` from `registration.json` (sha256 `f3dbcd4bf3b0…`), code commit `7cdb7e8`. Every definition is in the registration. This file only formats the outputs.

Exploratory. It closes the directional-surprise part of RFC-002 section 3.3, which the TSM diagnostics recorded as a data gap. The returns used are the ones already frozen in tsm_diagnostics, whose results were known when this was written: no state split there reached two standard errors. Revenue growth is never used as a surprise here; the only benchmark is the company's own guidance.

## All quarters

Above the range: 52. Inside: 29. Below: 3.

| Window | Mean, above | Mean, below | Mean, inside | Above minus below | In standard errors |
| --- | ---: | ---: | ---: | ---: | ---: |
| Reaction | +0.41% | -0.58% | +0.27% | +0.98% | +1.42 |
| Entry session | -0.42% | +0.70% | -0.29% | -1.12% | -0.98 |
| 5 sessions | -0.25% | +0.20% | +0.62% | -0.45% | -0.33 |

## Without the quarters whose guidance was revised mid-quarter

Above the range: 50. Inside: 28. Below: 2.

| Window | Mean, above | Mean, below | Mean, inside | Above minus below | In standard errors |
| --- | ---: | ---: | ---: | ---: | ---: |
| Reaction | +0.40% | -0.97% | +0.26% | +1.37% | +1.41 |
| Entry session | -0.42% | +1.24% | -0.28% | -1.66% | -0.96 |
| 5 sessions | -0.26% | +1.08% | +0.73% | -1.34% | -0.80 |

Returns are TSM's beta-adjusted abnormal returns around the release of the quarter's third month.

Currency check: of the 37 quarters guided in US dollars, 20 fall on the same side of the range when converted at the single rate the guidance assumed instead of month by month.

## Every quarter

| Quarter | Currency | Range | Actual | Against midpoint | Surprise | At assumed rate | Revised | Reaction | Entry session | 5 sessions |
| --- | --- | ---: | ---: | ---: | --- | --- | --- | ---: | ---: | ---: |
| 2005Q4 | NTD | 77 to 79 | 81.16 | +4.05% | above |  |  | -0.84% | -2.22% | -5.17% |
| 2006Q1 | NTD | 73 to 76 | 77.29 | +3.75% | above |  |  | +0.92% | +0.45% | +3.44% |
| 2006Q2 | NTD | 80 to 82 | 82.16 | +1.43% | above |  |  | +0.48% | -1.71% | -5.82% |
| 2006Q3 | NTD | 79 to 82 | 82.44 | +2.41% | above |  |  | +1.38% | -0.29% | +1.14% |
| 2006Q4 | NTD | 74 to 76 | 74.96 | -0.05% | inside |  |  | -0.77% | -0.23% | +8.40% |
| 2007Q1 | NTD | 62 to 64 | 64.90 | +3.01% | above |  |  | -0.78% | -0.58% | -1.44% |
| 2007Q2 | NTD | 73 to 75 | 74.92 | +1.24% | inside |  |  | -0.43% | +0.07% | -1.10% |
| 2007Q3 | NTD | 85 to 87 | 88.95 | +3.44% | above |  |  | -0.81% | +2.38% | -2.99% |
| 2007Q4 | NTD | 92 to 94 | 93.86 | +0.92% | inside |  |  | +0.14% | -0.34% | -1.69% |
| 2008Q1 | NTD | 87 to 89 | 87.48 | -0.59% | inside |  |  | +0.93% | -0.12% | +5.56% |
| 2008Q2 | NTD | 87 to 89 | 88.14 | +0.16% | inside |  |  | +1.54% | -0.87% | -6.32% |
| 2008Q3 | NTD | 90 to 92 | 92.98 | +2.17% | above |  |  | +0.69% | -6.84% | -1.19% |
| 2008Q4 | NTD | 69 to 71 | 64.56 | -7.77% | below |  |  | -1.93% | +2.96% | +2.67% |
| 2009Q1 | NTD | 32 to 35 | 39.51 | +17.94% | above |  |  | +0.79% | +1.86% | -4.44% |
| 2009Q2 | NTD | 71 to 74 | 74.22 | +2.37% | above |  |  | +0.16% | +1.63% | -6.63% |
| 2009Q3 | NTD | 88 to 90 | 89.94 | +1.06% | inside |  |  | +0.38% | -2.26% | -3.02% |
| 2009Q4 | NTD | 90 to 92 | 92.09 | +1.20% | above |  |  | +0.44% | -1.76% | -1.45% |
| 2010Q1 | NTD | 89 to 91 | 92.19 | +2.43% | above |  |  | +0.77% | -1.57% | -1.52% |
| 2010Q2 | NTD | 100 to 102 | 104.96 | +3.92% | above |  |  | +0.22% | -0.53% | -2.15% |
| 2010Q3 | NTD | 109 to 111 | 112.25 | +2.05% | above |  |  | +0.19% | -0.03% | -0.62% |
| 2010Q4 | NTD | 107 to 109 | 110.15 | +1.99% | above |  |  | +0.50% | -0.06% | +0.14% |
| 2011Q1 | NTD | 105 to 107 | 105.38 | -0.58% | inside |  |  | -0.25% | +0.26% | -0.69% |
| 2011Q2 | NTD | 109 to 111 | 110.51 | +0.46% | inside |  |  | -0.15% | +0.08% | +1.05% |
| 2011Q3 | NTD | 102 to 104 | 106.48 | +3.38% | above |  |  | -0.35% | -0.27% | -1.64% |
| 2011Q4 | NTD | 103 to 105 | 104.71 | +0.68% | inside |  |  | +0.09% | +2.01% | +0.01% |
| 2012Q1 | NTD | 103 to 105 | 105.51 | +1.45% | above |  |  | +0.20% | -0.28% | +1.57% |
| 2012Q2 | NTD | 126 to 128 | 128.07 | +0.84% | above |  |  | -0.49% | +0.53% | -1.74% |
| 2012Q3 | NTD | 136 to 138 | 141.38 | +3.20% | above |  |  | -0.61% | +0.72% | +0.52% |
| 2012Q4 | NTD | 129 to 131 | 131.30 | +1.00% | above |  |  | +0.66% | +1.84% | -1.03% |
| 2013Q1 | NTD | 127 to 129 | 132.75 | +3.71% | above |  |  | +0.39% | -0.72% | +0.60% |
| 2013Q2 | NTD | 154 to 156 | 155.89 | +0.57% | inside |  |  | +0.77% | -1.52% | -0.55% |
| 2013Q3 | NTD | 161 to 164 | 162.57 | +0.04% | inside |  |  | +0.97% | -1.82% | +1.47% |
| 2013Q4 | NTD | 144 to 147 | 145.81 | +0.21% | inside |  |  | +0.43% | +0.07% | +6.47% |
| 2014Q1 | NTD | 136 to 138 | 148.22 | +8.19% | above |  | yes | +0.19% | +0.38% | +2.22% |
| 2014Q2 | NTD | 180 to 183 | 183.02 | +0.84% | above |  |  | +0.03% | +2.66% | -7.35% |
| 2014Q3 | NTD | 206 to 209 | 209.06 | +0.75% | above |  |  | +0.95% | +0.11% | +2.28% |
| 2014Q4 | NTD | 217 to 220 | 222.53 | +1.84% | above |  |  | -1.14% | -1.71% | +8.88% |
| 2015Q1 | NTD | 221 to 224 | 222.04 | -0.21% | inside |  |  | +1.19% | -1.19% | -2.02% |
| 2015Q2 | NTD | 204 to 207 | 205.45 | -0.02% | inside |  |  | -0.24% | +0.60% | +0.33% |
| 2015Q3 | NTD | 207 to 210 | 212.50 | +1.92% | above |  |  | -0.64% | +1.65% | +0.83% |
| 2015Q4 | NTD | 201 to 204 | 203.52 | +0.50% | inside |  |  | +0.32% | -0.68% | +4.09% |
| 2016Q1 | NTD | 198 to 201 | 203.50 | +2.00% | above |  | yes | +0.69% | -1.35% | -2.19% |
| 2016Q2 | NTD | 215 to 218 | 221.81 | +2.45% | above |  |  | +1.04% | -0.13% | -2.43% |
| 2016Q3 | NTD | 254 to 257 | 260.40 | +1.92% | above |  |  | +0.24% | +1.20% | +2.05% |
| 2016Q4 | NTD | 255 to 258 | 262.23 | +2.23% | above |  |  | -0.06% | -0.22% | -0.59% |
| 2017Q1 | NTD | 236 to 239 | 233.92 | -1.51% | below |  |  | -0.01% | -0.48% | -0.51% |
| 2017Q2 | NTD | 213 to 216 | 213.86 | -0.30% | inside |  |  | -0.30% | +0.64% | +2.03% |
| 2017Q3 | USD | 8.12 to 8.22 | 8.34 | +2.05% | above | above |  | +0.08% | -0.31% | +3.84% |
| 2017Q4 | USD | 9.1 to 9.2 | 9.22 | +0.75% | above | inside |  | -1.32% | -0.08% | +1.84% |
| 2018Q1 | USD | 8.4 to 8.5 | 8.47 | +0.29% | inside | below |  | +0.29% | -1.26% | -3.81% |
| 2018Q2 | USD | 7.8 to 7.9 | 7.85 | -0.04% | inside | above |  | -1.09% | +0.57% | +2.03% |
| 2018Q3 | USD | 8.45 to 8.55 | 8.50 | -0.01% | inside | inside | yes | +0.52% | -0.58% | -2.41% |
| 2018Q4 | USD | 9.35 to 9.45 | 9.41 | +0.10% | inside | inside |  | +0.31% | +1.31% | +0.01% |
| 2019Q1 | USD | 7.3 to 7.4 | 7.10 | -3.41% | below | below | yes | +0.20% | -0.37% | -1.55% |
| 2019Q2 | USD | 7.55 to 7.65 | 7.75 | +1.98% | above | above |  | +2.14% | -0.09% | +1.15% |
| 2019Q3 | USD | 9.1 to 9.2 | 9.41 | +2.81% | above | above |  | -0.22% | +0.75% | +0.21% |
| 2019Q4 | USD | 10.2 to 10.3 | 10.43 | +1.73% | above | above |  | +0.33% | -0.52% | -2.08% |
| 2020Q1 | USD | 10.2 to 10.3 | 10.34 | +0.84% | above | above |  | +1.14% | -1.13% | +5.05% |
| 2020Q2 | USD | 10.1 to 10.4 | 10.43 | +1.73% | above | inside |  | -0.68% | -0.51% | +4.16% |
| 2020Q3 | USD | 11.2 to 11.5 | 12.17 | +7.21% | above | above |  | +1.53% | -1.44% | -2.60% |
| 2020Q4 | USD | 12.4 to 12.7 | 12.70 | +1.21% | above | inside |  | +2.57% | -4.68% | -1.54% |
| 2021Q1 | USD | 12.7 to 13 | 12.93 | +0.62% | inside | inside |  | +0.17% | -0.40% | -3.42% |
| 2021Q2 | USD | 12.9 to 13.2 | 13.33 | +2.18% | above | inside |  | -0.20% | +0.96% | +0.11% |
| 2021Q3 | USD | 14.6 to 14.9 | 14.91 | +1.07% | above | inside |  | -0.20% | +0.44% | +1.13% |
| 2021Q4 | USD | 15.4 to 15.7 | 15.77 | +1.41% | above | inside |  | +2.42% | -1.25% | +8.85% |
| 2022Q1 | USD | 16.6 to 17.2 | 17.57 | +3.98% | above | above |  | +0.71% | -0.10% | +1.15% |
| 2022Q2 | USD | 17.6 to 18.2 | 18.19 | +1.60% | inside | above |  | +0.16% | +0.56% | +2.81% |
| 2022Q3 | USD | 19.8 to 20.6 | 20.19 | -0.06% | inside | above |  | -1.27% | -0.62% | -1.94% |
| 2022Q4 | USD | 19.9 to 20.7 | 19.96 | -1.69% | inside | below |  | +0.50% | -0.27% | +7.02% |
| 2023Q1 | USD | 16.7 to 17.5 | 16.76 | -2.02% | inside | below |  | -1.35% | -1.37% | -2.09% |
| 2023Q2 | USD | 15.2 to 16 | 15.68 | +0.48% | inside | inside |  | -0.23% | -1.90% | +1.26% |
| 2023Q3 | USD | 16.7 to 17.5 | 17.27 | +1.01% | inside | above |  | +1.64% | -0.83% | +1.00% |
| 2023Q4 | USD | 18.8 to 19.6 | 19.65 | +2.32% | above | inside |  | +0.44% | -1.23% | +0.54% |
| 2024Q1 | USD | 18 to 18.8 | 18.88 | +2.63% | above | above |  | +2.29% | -0.14% | -3.13% |
| 2024Q2 | USD | 19.6 to 20.4 | 20.84 | +4.22% | above | above |  | +1.22% | +0.25% | -1.16% |
| 2024Q3 | USD | 22.4 to 23.2 | 23.57 | +3.38% | above | above |  | +1.03% | -1.38% | +1.63% |
| 2024Q4 | USD | 26.1 to 26.9 | 26.90 | +1.50% | inside | above |  | +1.38% | +1.43% | +2.30% |
| 2025Q1 | USD | 25 to 25.8 | 25.55 | +0.57% | inside | inside |  | +2.27% | +0.17% | +1.13% |
| 2025Q2 | USD | 28.4 to 29.2 | 30.23 | +4.95% | above | inside |  | -0.40% | -1.34% | +2.34% |
| 2025Q3 | USD | 31.8 to 33 | 33.05 | +2.02% | above | above |  | -0.59% | -0.55% | +1.79% |
| 2025Q4 | USD | 32.2 to 33.4 | 33.74 | +2.86% | above | above |  | -0.27% | -0.36% | +3.09% |
| 2026Q1 | USD | 34.6 to 35.8 | 35.87 | +1.91% | above | above |  | +1.85% | -2.21% | -8.46% |
| 2026Q2 | USD | 39 to 40.2 | 40.21 | +1.54% | above | inside |  | +2.12% | -1.56% | -2.87% |
| 2026Q3 | USD | 44.6 to 45.8 | 46.74 | +3.41% | above | above |  | -0.11% | -0.58% | -1.28% |

## Limits

- About 84 quarters, and far fewer above or below the range: this can describe, not establish.
- The quarter's revenue being above guidance is often visible from the first two months, so the third month's release is not the first moment the surprise could be inferred.
- USD/TWD closes are Yahoo's, not an official fixing, and start in March 2004.
- Guidance ranges and monthly figures were collected through page readers and PDF text extraction and cross-checked as their files describe.
- Revisions to guidance before 2012 were not searched for.
