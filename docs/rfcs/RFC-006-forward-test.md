# RFC-006: A Forward Test of the Fitted Gates, and Graph-Selected Inputs

* **Status**: Draft, revision 2. Nothing registered, no ticket issued.
* **Date**: 2026-11-23
* **Repository**: `Quant_rapper`
* **Depends on**: the frozen rule baseline, the frozen RFC-005 comparison (`artifacts/ablations/soxx_qqq_wide_inputs`), the ticket loop of RFC-004 (`quant_loop.tickets`) and the causal-map generator of RFC-003.
* **Scope**: daily shadow tickets from fitted gates beside the rule's, a review point fixed now, and one new gate whose inputs are chosen by the causal-map generator. No backtest decides anything.

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

## 2. Gates and their roles

| Gate | Inputs | Label | Role |
| --- | --- | --- | --- |
| Rule | The 3 signals, frozen | none | The gate in force, and the benchmark for every promotion |
| `W-avg` | RFC-005's 33 | forward average | Shadow (Part A) |
| `W-dd` | RFC-005's 33 | forward drawdown | Shadow (Part A); also the reference for `G-dd` |
| `G-dd` | Chosen by the causal-map generator, per fund | forward drawdown | Shadow (Part B) |

A shadow ticket decides nothing. It vetoes no alpha ticket and moves no exposure.

Keeping both `W-avg` and `W-dd` costs little, since they share data and schedule, and gives a forward comparison of the two training targets.

## 3. Part A: shadow tickets from the RFC-005 gates

### 3.1 What is issued

After each US session, for SOXX and QQQ: the rule's ticket as now, and one shadow ticket each from `W-avg` and `W-dd`, exactly as registered in RFC-005. The same 33 inputs, bindings, labels, tree settings, monthly refit on matured outcomes, gate quantiles and step-down. The code is pinned by commit. Nothing is re-tuned.

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

## 4. Part B: a gate whose inputs the graph selects

RFC-005's 33 inputs were picked by hand and overlap heavily: five use VIX, four use HYG or LQD. Whether the research map selects inputs better is the question RFC-005 left open.

* **Selection**: the causal-map generator is run for SOXX and for QQQ at a stated decision time on a frozen prior. Every candidate it marks measurable with decision-time timing becomes an input. None is added or removed by hand, and none is chosen by how it performed.
* **The graph determines the count, per fund.** The lists may differ between SOXX and QQQ.
* **What must be built first**: the generator makes candidates only from `factor_definitions.json`, which holds four factors today. The 18 series bound inside the RFC-005 study need catalogue bindings with anchors in the research map, and their measures need factor definitions. A series with no honest anchor in the map stays out; that is a finding about the map, to be reported.
* **Model**: the same fixed tree model, drawdown label, gate and trading costs as `W-dd`. Only the inputs differ.
* **A backtest of `G-dd` on the old history** is reported once for description and decides nothing.

Part B is developed in parallel with Part A and does not delay it.

## 5. Clocks, comparisons and the review

### 5.1 Separate start dates

* **Part A's clock** starts at the first session for which the rule, `W-avg` and `W-dd` all have a live ticket for both funds.
* **Part B's clock** starts at the first session for which `G-dd` also has one. From then, `G-dd` is compared with `W-dd` and with the rule over the same sessions.
* Each clock counts sessions from its start whose 20-session outcome has matured. Missed tickets are inside the count, with held exposure.

### 5.2 The two comparisons

* **Against the rule (eligibility)**: RFC-004's three limits over the review sessions at base cost. Maximum drawdown smaller by at least 2 points, final equity at least 95% of the rule's, whipsaw rate no more than 5 points higher.
* **`G-dd` against `W-dd` (what graph selection adds)**: on the same live dates, with the same target, model settings and costs: drawdown, equity, tail recall read with alert rate, and the paired recall difference on realised drawdown episodes. Descriptive. It makes nothing eligible.
* **`W-dd` against `W-avg` (what the target adds)**: the same measures. Descriptive.

### 5.3 The review point

* **When**: at 250 sessions on a gate's clock. Part A and Part B therefore reach it on different dates.
* **Too calm to tell, per fund**: if buy and hold's own maximum drawdown for a fund over the review sessions is smaller than 10%, that fund's review is recorded as uninformative and repeated once after a further 125 sessions. The other fund is unaffected. No other extension.
* **Failing is binding.** A gate that misses any limit for a fund cannot be promoted for that fund from this review.
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
| A backup of the snapshots | A local copy in `run/backup/snapshots/` with checksums; same machine, not in the repository |
| A scheduled job that runs the loop after each close | Nothing |
| A check that reports a missed run the next morning | Nothing |

## 7. Deliverables

1. A snapshot command that downloads every bound series, writes a manifest and refuses to overwrite a snapshot.
2. Shadow-ticket emission for a registered fitted gate: score, state, model month, input hashes, decision cutoff, issuance time, and the live or replay marking.
3. The missed-ticket record and the held-exposure rule of section 3.3, used by every equity and drawdown figure.
4. A 2027 session calendar and FOMC schedule, each with its source recorded.
5. A scheduled local job working in `monitors/Daily/`, a missed-run check, and a copy of each new snapshot into `run/backup/snapshots/` with its checksums.
6. Catalogue bindings and factor definitions for the added series, with anchors, and the generator's output for both funds.
7. Registrations, each pushed before the first ticket it covers: the Part A shadow record with the review rule, and later the `G-dd` gate with its paired comparison.
8. A maturity job that scores each ticket once its 20 sessions are in the data.
9. Invariant tests:
   * a ticket uses no input that became knowable at or after its decision cutoff, whenever it is issued;
   * a ticket issued after the next open is a replay and never enters the forward record;
   * a missed session holds the last live state and stays in the equity curve;
   * a shadow ticket cannot veto.

## 8. Acceptance criteria

* The first live shadow tickets exist for both funds, each carrying its decision cutoff and an issuance time before the next open.
* Every registration predates the first ticket it covers.
* The review rule cannot be changed without an amendment recorded before the review date.
* Missed tickets are counted in every report, and every equity figure includes every trading day.
* `G-dd`'s inputs are exactly the generator's output on a frozen prior, and its comparison with `W-dd` is registered before its first ticket.

The RFC is complete when both forward records are running and registered. The reviews fall about a year later and are their own piece of work.

## 9. Choices made in this revision

| Choice | Decision |
| --- | --- |
| Which RFC-005 gates to shadow | Both |
| Who runs the daily loop | A scheduled local job, with a missed-run check |
| Raw files | A backup copy of every snapshot in `run/backup/snapshots/`, git-ignored. It guards against deletion or overwriting; it is on the same machine, so it does not survive loss of the disk and gives nobody else a way to replay |
| Where the daily job works | `monitors/Daily/`, git-ignored: logs, locks and scratch. Tickets and snapshots go under `artifacts/` |
| Part B timing | In parallel, on its own clock |
| Review | A review point at 250 sessions; passing gives eligibility only |

Still open for the owner: the time the job runs. Proposed: 19:00 America/New_York on each session, which is after every same-day input is published and long before the next open.

## 10. Out of scope

* Any further comparison on history to 2026-11-20 that decides something.
* Tuning the tree, the targets or the gate.
* TSM or any single stock, including the 10 December 2026 revenue release, which stays under its own registration.
* Trading real money on any ticket.
