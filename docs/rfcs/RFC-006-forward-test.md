# RFC-006: A Forward Test of the Fitted Gates, and Graph-Selected Inputs

* **Status**: Draft, revision 5. Nothing registered, no ticket issued.
* **Date**: 2026-11-23
* **Repository**: `Quant_rapper`
* **Depends on**: the frozen rule baseline, the frozen RFC-005 comparison (`artifacts/ablations/soxx_qqq_wide_inputs`), the ticket loop of RFC-004 (`quant_loop.tickets`) and the causal-map generator of RFC-003.
* **Scope**: daily shadow tickets from fitted gates beside the rule's for SOXX, QQQ and TSM, a review point fixed now, and one new gate whose inputs are chosen by the causal-map generator. No backtest decides anything.
* **Revision 3** adds TSM as a subject and a seed-averaged gate, after a local exploration on TSM (section 1.1).
* **Revision 5** corrects two defects in the RFC-005 inputs before anything is registered (section 3.5).

---

## 1. Why this RFC exists

Three registered comparisons have been run on SOXX and QQQ history to 2026-11-20, and the frozen three-signal rule was kept each time.

| Comparison | What changed | Result |
| --- | --- | --- |
| RFC-004, Model A | A linear model on the rule's 3 inputs | Rule kept |
| RFC-004, Model C | Boosted trees on the same 3 inputs | Rule kept |
| RFC-005 | 33 inputs against 3, two targets | Rule kept; stopping rule met |

RFC-005 left one finding worth following. Over 2020–2026 the 33-input gates beat their 3-input controls in all four pairs, on both drawdown and equity, and came close to the rule on SOXX.

| Fund | Arm | Max drawdown | Final equity |
| --- | --- | ---: | ---: |
| SOXX | Frozen rule | −38.0% | 4.06 |
| SOXX | 33 inputs, forward average | −37.2% | 3.91 |
| SOXX | 33 inputs, drawdown | −40.4% | 4.54 |
| QQQ | Frozen rule | −17.9% | 2.82 |
| QQQ | 33 inputs, drawdown | −22.3% | 2.78 |

That history is now used up. A further model tried on it would be partly fitted to results already seen, and RFC-005's stopping rule forbids variants on 2020–2026. The only clean evidence left is a decision recorded before its outcome exists.

### 1.1 What a local exploration on TSM showed

Run on 2026-11-23 in `run/tsm_gate_2026-11-23` (local only, not registered). The frozen rule with TSM's own 200-session trend, and the RFC-005 tree gate on the drawdown label with TSM's own price inputs, over 2020-01-02 to 2026-11-20:

| Arm | Max drawdown | Final equity |
| --- | ---: | ---: |
| Buy and hold | −57.1% | 5.40 |
| Rule with TSM's own trend | −26.3% | 5.45 |
| SOXX's rule positions applied to TSM | −36.2% | 5.31 |
| 3 inputs, trees | −41.3% | 2.95 |
| 33 inputs, trees, median of 7 seeds | −37.1% | 8.27 |

* **The 33-input gate beat buy and hold on both return and drawdown in every one of seven seeds** (equity 6.76 to 9.20, drawdown −33.8% to −39.0%). It did not beat the rule on drawdown in any.
* **The result moves with the random seed**, which is why a seed-averaged gate is added below.
* **Three years carry it** (2021, 2022 and 2026), and TSM moves closely with SOXX, whose result on these years was already known. It is a description, not a fresh test.
* TSM's own inputs were computed directly from its closes, not through the point-in-time pipeline.

**Rerun on 2026-11-23 with the corrections of section 3.5**, through the point-in-time pipeline and to the 2026-11-23 close: buy and hold −57.1% and 6.17; the rule −26.3% and 5.45; the 33-input drawdown model averaged across seven seeds −33.8% and 7.76 (single seeds 7.39 to 8.71). The same run on MU, NVDA and AMD showed how much these figures depend on the training window: dropping the 2009–2012 rows moved the seven-seed final equity from 7.62 to 15.92 on MU and from 19.32 to 43.48 on NVDA, and on AMD the gate ended at 2.93 against 9.44 for buy and hold. None of this is registered, and it is the reason the forward record, not a backtest, decides.

## 2. Gates and their roles

| Model | Inputs | Training target | Scoring | Role |
| --- | --- | --- | --- | --- |
| Frozen rule | The 3 signals | none | Count of stressed signals | The gate in force, and the benchmark for every eligibility test |
| 33-input forward-average model, single seed | RFC-005's 33 | Forward average | One fit, seed 0 | Shadow (Part A) |
| 33-input drawdown model, single seed | RFC-005's 33 | Forward drawdown | One fit, seed 0 | Shadow (Part A); also the reference for the graph-selected model |
| 33-input drawdown model, predictions averaged across seven seeds | RFC-005's 33 | Forward drawdown | Mean score of seven fits, seeds 0 to 6 | Shadow (Part A) |
| Graph-selected-input drawdown model, single seed | Chosen by the causal-map generator, per subject | Forward drawdown | One fit, seed 0 | Shadow (Part B) |

The two drawdown models on 33 inputs differ only in scoring: the inputs, the target, the tree settings and the gate are the same. Registrations use short identifiers for these (`W-avg`, `W-dd`, `E-dd`, `G-dd`); this document uses the full names.

A shadow ticket decides nothing. It vetoes no alpha ticket and moves no exposure.

Keeping both single-seed models costs little, since they share data and schedule, and gives a forward comparison of the two training targets.

**Why a seven-seed model.** The tree model samples columns at random, so one seed is one draw. The seven-seed model fits the same model with seeds 0 to 6 each month and uses the mean of the seven scores. The two single-seed models stay at seed 0, as registered in RFC-005. With the corrections of section 3.5 they train on fewer rows than that backtest did, so the forward record is of the corrected models, not of the exact models RFC-005 scored.

**Subjects.**

| Subject | Gate in force | Shadow models |
| --- | --- | --- |
| SOXX | Frozen rule | Forward-average single seed; drawdown single seed; drawdown seven seeds; later the graph-selected model |
| QQQ | Frozen rule | Forward-average single seed; drawdown single seed; drawdown seven seeds; later the graph-selected model |
| TSM | Frozen rule, with TSM's own 200-session trend | Drawdown seven seeds only, with TSM's own price inputs in place of the fund's |

* **TSM has one shadow model.** The comparisons of single seed against seven seeds, and of the two training targets, are therefore made on SOXX and QQQ only. For TSM the seven-seed model is compared with TSM's rule and with buy and hold.
* **TSM's rule** is the frozen rule unchanged except that its trend signal reads TSM's close. Its thresholds, exposure levels and cost are the sector baseline's.
* **TSM's inputs** are built through the same point-in-time panel as the funds', with TSM's bound close as the subject.
* **The SOXX gate keeps its veto** over a bullish single-stock ticket, as the ticket code already enforces. TSM's own gates are recorded beside that; they do not override it.
* **TSM is not independent evidence.** It shares 26 of its 33 inputs with SOXX and moves closely with it. Its record is reported separately and is never pooled with the funds' to claim a larger sample.

## 3. Part A: shadow tickets from the RFC-005 gates

### 3.1 What is issued

After each US session, for each subject: the rule's ticket, and one shadow ticket from each of its shadow models in section 2. The two single-seed models are as registered in RFC-005 except for the two input corrections of section 3.5: the same 33 inputs, bindings, labels, tree settings, monthly refit on matured outcomes, gate quantiles and step-down. The code is pinned by commit. Nothing is re-tuned.

### 3.2 The information cutoff and the issuance time are two things

* **Decision cutoff**: 17:00 America/New_York on the session's date, as registered in RFC-005. Every input is selected as knowable strictly before it, by the same bindings. A value that became knowable at 18:00 is not used, even though the job runs later.
* **Issuance time**: when the ticket file was written. Both are recorded on the ticket.
* **Live** means issued before the next session opens. A ticket issued after that is marked `replay` and is left out of the forward record.
* **Limit, stated now**: a snapshot downloaded in the evening can hold a value its source revised after 17:00, and that cannot be detected. The snapshot's hash pins what was used, not what was knowable.

### 3.3 Missed tickets and how exposure carries

* A session with no live ticket for a gate is a **missed ticket**: recorded as such, counted in every report, and never back-filled into the record.
* **On a missed session the gate holds the state of its last live ticket**, the rule RFC-005 already applies to a missing input. Before a gate's first live ticket it has no state and its record has not begun.
* **Every trading day stays in the equity and drawdown figures**, including a missed day during a sell-off. A gate cannot gain or lose a day by not issuing.
* A missed run usually affects every gate and the rule's ticket alike. Where it affects only some, each gate holds its own last state and all are still compared over the same sessions.
* A replay ticket may be written afterwards for the record of what the gate would have said. It never changes the held exposure.

### 3.4 Tickets are immutable

Written once, as the existing loop does, with the hashes of the snapshot and of the registration they came from.

### 3.5 Two corrections to the RFC-005 inputs

Found on 2026-11-23 and recorded in RFC-005 section 10. Both apply to every fitted gate here, from the first ticket.

* **No value from before a series was first published.** Cboe first published SKEW on 2011-02-23 and announced VVIX on 2012-03-14; earlier values were calculated afterwards. They are treated as missing. A fitted gate therefore trains on decisions from 2012-03-14, not from 2009-09-18. Each added series' first publication date is recorded in its binding with its source, and a series with no established date is checked before registration.
* **The FOMC schedule as it stood on each date.** A scheduled meeting counts until the day its cancellation or move became known. The one case found is the 17–18 March 2020 meeting, held early on 2020-03-15: `sessions_to_fomc` counts to 2020-03-18 for decisions before 2020-03-15.
* **What this costs.** The corrected models are not the ones RFC-005 scored, so its backtest figures do not describe them exactly. No corrected backtest on SOXX or QQQ is run to replace those figures, because RFC-005's stopping rule forbids it.
* **What is still unchecked.** The date each year's FOMC schedule was published, and the publication times of the added series, remain assumptions as stated in RFC-005.

## 4. Part B: a gate whose inputs the graph selects

RFC-005's 33 inputs were picked by hand and overlap heavily: five use VIX, four use HYG or LQD. Whether the research map selects inputs better is the question RFC-005 left open.

* **Selection**: the causal-map generator is run for SOXX and for QQQ at a stated decision time on a frozen prior. Every candidate it marks measurable with decision-time timing becomes an input. None is added or removed by hand, and none is chosen by how it performed.
* **The graph determines the count, per subject.** The lists may differ between SOXX and QQQ.
* **What must be built first**: the generator makes candidates only from `factor_definitions.json`, which holds four factors today. The 18 series bound inside the RFC-005 study need catalogue bindings with anchors in the research map, and their measures need factor definitions. A series with no honest anchor in the map stays out; that is a finding about the map, to be reported.
* **Model**: the same fixed tree model, drawdown label, gate and trading costs as the 33-input drawdown model, single seed. Only the inputs differ.
* **A backtest of the graph-selected model on the old history** is reported once for description and decides nothing.
* The graph-selected model is built for SOXX and QQQ. Whether TSM gets one is decided when the generator's output for TSM is seen.

Part B is developed in parallel with Part A and does not delay it.

## 5. Clocks, comparisons and the review

### 5.1 Separate start dates

* **Part A's clock** starts at the first session for which the rule and every Part A shadow gate have a live ticket for all three subjects.
* **Part B's clock** starts at the first session for which the graph-selected model also has one. From then it is compared with the 33-input drawdown model, single seed, and with the rule over the same sessions.
* Each clock counts sessions from its start whose 20-session outcome has matured. Missed tickets are inside the count, with held exposure.

### 5.2 The two comparisons

* **Against the rule (eligibility)**: RFC-004's three limits over the review sessions at base cost. Maximum drawdown smaller by at least 2 points, final equity at least 95% of the rule's, whipsaw rate no more than 5 points higher.
* **Graph-selected inputs against the 33 hand-picked inputs, both as single-seed drawdown models (what graph selection adds)**, SOXX and QQQ: on the same live dates, with the same target, model settings and costs: drawdown, equity, tail recall read with alert rate, and the paired recall difference on realised drawdown episodes. Descriptive. It makes nothing eligible.
* **Drawdown target against forward-average target, both 33 inputs and single seed (what the target adds)**, SOXX and QQQ: the same measures. Descriptive.
* **Seven seeds against a single seed, both the 33-input drawdown model (what seed averaging adds)**, SOXX and QQQ only, since TSM has no single-seed model: the same measures. Descriptive.
* **Every gate against buy and hold**: maximum drawdown and final equity over the review sessions, for the rule and each shadow gate. Descriptive. A gate can have more return than the rule and a deeper drawdown; this table shows that trade-off, and the eligibility test above does not.
* **How many gates were looked at** is stated in every review: with up to four shadow models on each fund and one on TSM, one meeting the limits by chance is likely, which is one reason a pass gives eligibility only.

### 5.3 The review point

* **When**: at 250 sessions on a gate's clock. Part A and Part B therefore reach it on different dates.
* **Too calm to tell, per subject**: if buy and hold's own maximum drawdown for a subject over its review sessions is smaller than 10%, that subject's review is recorded as uninformative and repeated once after a further 125 sessions. This applies to SOXX, QQQ and TSM alike, each judged on its own drawdown, and the other subjects are unaffected. No other extension.
* **Failing is binding.** A model that misses any limit for a subject cannot be promoted for that subject from this review.
* **Passing makes a gate eligible, nothing more.** Replacing the rule needs a separate decision by the owner, recorded with its reasons, and a new registration. One drawdown episode can drive most of a one-year result, and the decision may weigh that.
* **If several gates are eligible for a fund**, the separate decision chooses among them; this RFC sets no automatic tie-break.
* **Before the review**: tickets and matured outcomes are visible, since hiding them is not practical. No summary against the rule is computed, and nothing is changed in response to them.

## 6. What this needs to run every day

| Need | State today |
| --- | --- |
| A daily snapshot of every bound series, pinned by hash | Done by hand, twice |
| The 33-input panel built for one new session | Built for whole histories only; takes about four minutes |
| A ticket emitter for a fitted gate | The emitter handles the rule only |
| A session calendar past 2026 | Ends 2026-12-31 |
| The FOMC schedule for 2027 | Not held |
| First publication dates of the added series, and the FOMC schedule as it stood on each date | Pieces verified; daily panel integration pending. The 18 added series and the credit ratio are in the bindings catalogue, and a binding's `first_published` is enforced by every loader, every selection and the outcome-price lookup. The countdown in `quant_pit_data.schedule`, with the withdrawn meeting in `bindings/event_schedules.json`, is tested. No shared code yet builds the 33 inputs from the catalogue or feeds the countdown into the panel; a local check did both and matched the corrected inputs. Nine old series (rate indices, MOVE, the dollar index, three futures) have no established date and no floor |
| A backup of the snapshots | A local copy in `run/backup/snapshots/` with checksums; same machine, not in the repository |
| A scheduled job that runs the loop after each close | Nothing |
| A check that reports a missed run the next morning | Nothing |

## 7. Deliverables

1. A snapshot command that downloads every bound series, writes a manifest and refuses to overwrite a snapshot.
2. Shadow-ticket emission for a registered fitted gate: score, state, model month, input hashes, decision cutoff, issuance time, and the live or replay marking.
3. The missed-ticket record and the held-exposure rule of section 3.3, used by every equity and drawdown figure.
4. A 2027 session calendar and FOMC schedule, each with its source recorded.
5. A scheduled local job working in `monitors/Daily/`, a missed-run check, and a copy of each new snapshot into `run/backup/snapshots/` with its checksums.
6. Catalogue bindings and factor definitions for the added series, with anchors and each series' first publication date, the FOMC schedule as known on each date, and the generator's output for SOXX, QQQ and TSM.
7. Registrations, each pushed before the first ticket it covers: the Part A shadow record with the review rule, TSM's rule and the seven seeds; and later the graph-selected model with its paired comparison.
8. A maturity job that scores each ticket once its 20 sessions are in the data.
9. Invariant tests:
   * a ticket uses no input that became knowable at or after its decision cutoff, whenever it is issued;
   * no training row or ticket uses a value dated before its series was first published;
   * a ticket issued after the next open is a replay and never enters the forward record;
   * a missed session holds the last live state and stays in the equity curve;
   * a shadow ticket cannot veto.

## 8. Acceptance criteria

* The first live shadow tickets exist for all three subjects, each carrying its decision cutoff and an issuance time before the next open.
* Every registration predates the first ticket it covers.
* The review rule cannot be changed without an amendment recorded before the review date.
* Missed tickets are counted in every report, and every equity figure includes every trading day.
* The graph-selected model's inputs are exactly the generator's output on a frozen prior, and its comparison with the 33-input drawdown model, single seed, is registered before its first ticket.

The RFC is complete when both forward records are running and registered. The reviews fall about a year later and are their own piece of work.

## 9. Choices made in this revision

| Choice | Decision |
| --- | --- |
| Which RFC-005 models to shadow | Both single-seed models, plus the drawdown model averaged across seven seeds |
| Subjects | SOXX, QQQ and TSM |
| Who runs the daily loop | A scheduled local job, with a missed-run check |
| Raw files | A backup copy of every snapshot in `run/backup/snapshots/`, git-ignored. It guards against deletion or overwriting; it is on the same machine, so it does not survive loss of the disk and gives nobody else a way to replay |
| Where the daily job works | `monitors/Daily/`, git-ignored: logs, locks and scratch. Tickets and snapshots go under `artifacts/` |
| Part B timing | In parallel, on its own clock |
| Review | A review point at 250 sessions; passing gives eligibility only |
| RFC-005's two input defects | Corrected for every fitted gate from the first ticket; RFC-005 itself is not rerun |

Still open for the owner: the time the job runs. Proposed: 19:00 America/New_York on each session, which is after every same-day input is published and long before the next open.

## 10. Out of scope

* Any further comparison on history to 2026-11-20 that decides something.
* Tuning the tree, the targets or the gate.
* A model of what is specific to TSM after removing its SOXX share. The TSM gates here read TSM's raw price.
* The 10 December 2026 TSMC revenue release, which stays under its own registration.
* Any single stock other than TSM.
* Trading real money on any ticket.
