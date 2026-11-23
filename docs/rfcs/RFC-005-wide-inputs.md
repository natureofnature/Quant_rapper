# RFC-005: A Wider Input Set for the SOXX/QQQ Risk Gate

* **Status**: Draft, revision 2. Nothing registered. One fenced local exploration has been run (section 2).
* **Date**: 2026-11-22
* **Repository**: `Quant_rapper`
* **Depends on**: the frozen rule baseline (`artifacts/ablations/sector_risk_baseline`) and the frozen Model A and Model C comparisons of RFC-004.
* **Scope**: one preregistered four-arm comparison: 3 inputs against 33, under two training targets, with one fixed model. No single-stock work and no graph-based factor selection.
* **Revision history**: revision 1 proposed only a change of target on the same three inputs. A local exploration showed the inputs matter more than the target, so the inputs are now the main contrast and the target is the second factor.

---

## 1. Why this RFC exists

RFC-004 asked whether a fitted model beats the frozen three-signal rule as a risk gate. It did not, for either fund or either model. Two explanations were open:

* **The target.** The forward 20-session average rewards the rebound after stress, so the models learned that stress means low risk. RFC-004 section 2.1 named this risk.
* **The inputs.** Both models saw only the rule's own three signals, so the most they could do was re-weight the rule. A model cannot learn a driver it is never shown.

This RFC tests both at once, in a design that keeps them apart.

## 2. What the local exploration found

Run on 2026-11-22 in `run/wide_factors_2026-11-22` (local only, not registered). **Every series was cut at 2019-12-31**, so no price, input or outcome from 2020 onward was read with the new inputs.

Twelve gates per fund: {3 inputs, 33 inputs} × {forward-average target, drawdown target} × {linear with penalty 10, linear with penalty 100, depth-2 trees}, over 1,530 decisions from 2013-12-03 to 2019-12-31, each compared with the frozen rule under RFC-004's promotion limits.

| Fund | Rule: drawdown / equity | 33 inputs against 3, same target and model | Gates meeting the promotion limits |
| --- | --- | --- | --- |
| SOXX | −25.1% / 2.44 | Equity higher in 6 of 6 pairs; drawdown equal or smaller in 6 of 6 | 3 inputs: 0 of 6. 33 inputs: 4 of 6 |
| QQQ | −17.7% / 1.94 | Equity higher in 6 of 6 pairs; drawdown smaller in 2, equal in 1, larger in 3 | 0 of 6 either way |

The depth-2 tree, the model this RFC fixes:

| Fund | Target | Inputs | Max drawdown | Final equity | Alert rate | Tail recall | Whipsaw |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| SOXX | forward average | 3 | −29.0% | 3.19 | 38% | 39% | 54% |
| SOXX | forward average | 33 | −17.9% | 3.69 | 36% | 56% | 50% |
| SOXX | drawdown | 3 | −26.4% | 2.45 | 46% | 62% | 60% |
| SOXX | drawdown | 33 | −19.7% | 2.90 | 43% | 61% | 70% |
| QQQ | forward average | 3 | −24.0% | 1.45 | 33% | 31% | 80% |
| QQQ | forward average | 33 | −16.9% | 1.75 | 36% | 33% | 62% |
| QQQ | drawdown | 3 | −14.4% | 1.44 | 41% | 58% | 87% |
| QQQ | drawdown | 33 | −12.8% | 1.69 | 45% | 65% | 89% |

What this does and does not show:

* **The wider set helped on SOXX and raised equity on QQQ.** On QQQ no gate met the limits; the ones with small drawdowns gave up too much equity.
* **The target is not settled.** The drawdown target raised recall and the alert rate together. On SOXX the tree's recall was the same with 3 and 33 inputs; the wider set issued fewer alerts with better equity and drawdown. Recall must be read beside alert frequency.
* **It is weak evidence.** The window has no bear market (worst buy-and-hold drawdown −26%), 4 to 18 episodes per gate, and twelve gates per fund.
* **It took shortcuts** the registered run may not: closes came straight from Yahoo files with no publication-time checks, and gaps of up to five sessions were filled.

## 3. The registered comparison

### 3.1 Arms

One model, four arms per fund, plus the frozen rule and buy and hold. Same decisions, costs, gate and promotion limits for all.

| Arm | Inputs | Training target | Role |
| --- | --- | --- | --- |
| N-avg | the rule's 3 | forward average, left-tail class | control |
| N-dd | the rule's 3 | forward drawdown, worst 15% | control |
| W-avg | 33 | forward average, left-tail class | candidate |
| W-dd | 33 | forward drawdown, worst 15% | candidate |

### 3.2 Model, fixed

Boosted trees on a binary left-tail label: depth 2, 60 trees, learning rate 0.05, L2 penalty 10, column sampling 0.75, at least 60 decisions per leaf, fixed seed. These are RFC-004's Model C settings without the threshold expansion, which a binary label does not need.

The tree is chosen over the linear model because it needs no penalty to be picked and the linear results in section 2 moved with the penalty. This choice was made after seeing section 2, and the registration will say so.

### 3.3 Targets

* **Forward average, left tail**: RFC-004's class 0, unchanged: the forward 20-session average return below the clipped volatility threshold, with the same bounds per fund.
* **Forward drawdown, worst 15%**: the lowest close of sessions *t*+1 to *t*+20 over the open of *t*+1, minus one (`quant_evaluator.beta.risk_gate.forward_drawdown`), labelled left tail when below the 15th percentile of the matured values in that fold's training set. The 15% is the share already used in evaluation.

Both need the same sessions, so the purge is unchanged: train on *t* ≤ *T* − 21, frozen at each month's start.

### 3.4 Inputs

The 3 are the rule's. The 30 added, by mechanism:

| Mechanism | Inputs |
| --- | --- |
| Volatility | log VIX, its 5-session change, VVIX/VIX, SKEW, VXN/VIX, log MOVE and its 20-session change, SPY implied minus realised volatility |
| Rates | 10-year minus 3-month, 30-year minus 5-year, 20-session change in the 10-year, 60-session change in the 3-month |
| Credit | HYG/IEF over 20 and 60 sessions, LQD/IEF over 20, HYG/LQD against its 200-session average |
| Dollar and commodities | dollar index over 20 sessions, copper/gold over 60, oil over 20 |
| Breadth and rotation | equal-weight, small-cap and utilities against SPY over 60 sessions, SOXX/QQQ over 60 |
| The fund itself | return over 20, 60 and 120 sessions, distance below its 250-session high, 20-session realised volatility and its ratio to 120 |
| Calendar | sessions to the next scheduled FOMC meeting |

* **The list is frozen as it stands.** No input is added, dropped or re-specified after section 2, for either fund.
* **It was chosen by hand**, and several inputs share a series (five use VIX, four use HYG or LQD). This run tests broader inputs. Whether the RFC-003 causal-map generator selects factors well is a separate question for a later RFC.
* **Each added series must pass an availability check before registration** (section 5). A series that cannot be given a defensible publication time is dropped before registration, and the drop is recorded; the count of inputs then changes for both funds alike.

### 3.5 Periods

* **Test period: 2020-01-02 to 2026-11-20.** Models keep refitting monthly on everything matured before each month, as in RFC-004.
* **Development period: 2013-12-03 to 2019-12-31.** Reported beside it and labelled as already seen.

## 4. Reading rule

Fixed before the test period is run with the new inputs.

* **Promotion.** Only W-avg and W-dd can be promoted, each against the frozen rule over the test period at base cost: maximum drawdown smaller by at least 2 points, final equity at least 95% of the rule's, whipsaw rate no more than 5 points higher. Any failed limit keeps the rule.
* **If both candidates pass for a fund**, the one with the smaller maximum drawdown is taken. Funds are decided separately.
* **What the inputs add**: W against N under each target, on drawdown, equity, and tail recall read together with alert rate. Reported with the paired recall difference on realised drawdown episodes. Descriptive; it promotes nothing.
* **What the target adds**: the same, dd against avg at each input set. Descriptive.
* **Stopping rule.** If no candidate is promoted for either fund, the recorded conclusion is that this hand-picked set does not beat the rule out of its development period. No variant of this list, these targets or this model is then tried on 2020–2026. Further work goes to factor selection through the causal-map generator, judged on live tickets.

## 5. What limits the evidence

To be stated in the registration.

* **2020–2026 is a later-period test of the new inputs, not untouched data.** RFC-004 already showed how the rule and two models behaved on those market outcomes, and the rule's results there are known.
* **The model, the list and the 15% were fixed after the development-period results were seen.**
* **Few independent episodes.** Seven years hold a handful of large drawdowns (2020, 2022, 2025). A result will rest mostly on those.
* **Availability times are assumptions** for most series, as for the existing ones.

## 6. Deliverables

1. Bindings for each added series with timezone, value time and publication delay, and a recorded basis for each. Least certain and to be checked first: futures settlement times, the dollar index, the Treasury yield indices, and SKEW, VVIX and MOVE.
2. The inputs as factor definitions read through the existing point-in-time panel: no filling, missing stays missing. Where the existing measures cannot express an input (a ratio of two series over a lookback, a distance from a rolling high, a calendar countdown), the measure is added to the contracts with tests.
3. The FOMC countdown computed on the full published calendar. The exploration first counted on a calendar cut at the fence, which shortened the count for the last decisions of 2019; that is corrected in the figures above.
4. A binary-label path through the tree model and the per-fold training-only percentile, with invariant tests for both.
5. A new snapshot holding the added raw files, pinned by hash.
6. A registration `artifacts/ablations/soxx_qqq_wide_inputs/`, committed and pushed before the run, pinning code, inputs, the frozen rule and both RFC-004 arms.
7. A synthetic rehearsal, then one real run from code exported at the pinned commit, a rendered report and a frozen record.

## 7. Acceptance criteria

* Every added series has a binding and passes the existing point-in-time invariants, or is dropped before registration with the reason recorded.
* The registration predates the run and states sections 2 and 5.
* The report shows, per fund and per period: buy and hold, the rule, and the four arms.
* The decision per fund follows from section 4 and nothing else, and the stopping rule's outcome is recorded either way.

## 8. Open choices for the owner

| Choice | Default in this draft | Alternative |
| --- | --- | --- |
| Model | Depth-2 trees only | Add the linear model as a second, non-deciding arm |
| Tie-break when both candidates pass | Smaller maximum drawdown | Higher final equity |
| On a pass | Live tickets from the promoted gate beside the rule's | Replace the rule's tickets |
| A series failing its availability check | Dropped for both funds | Kept with a one-session lag |

## 9. Out of scope

* Graph-based or automated factor selection.
* TSM or any single stock.
* Any change to the frozen rule, its thresholds or its costs.
* Tuning any setting on 2020–2026.
