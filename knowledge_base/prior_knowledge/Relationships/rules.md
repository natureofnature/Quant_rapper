# Industry research maps

Keep the industry prior small. It supplies the durable relationships from which research starts. During a study, explore the existing paths by tracing direct and indirect effects, comparing business roles, and seeking evidence both for and against each explanation. Keep observations, exceptions and open questions in `run/<study>/`. Use [experiences](../../experiences/) for curated, reusable study cases.

When creating or revising a map, first try to express the proposed mechanism through existing paths. Add a node or edge only when those paths miss a distinct, material channel that would still matter across industry cycles. Each edge states a conditional hypothesis for the researcher to test.

Use the [semiconductor map](semiconductor/semiconductor.json) for structure, the [TSM profile](semiconductor/TSM/company.json) for conditional exposures, and the [technology platforms map](technology_platforms/technology_platforms.json) for test criteria.

## Three levels

| Level | Question | Location |
| --- | --- | --- |
| L1 — Macro forces and the industry | How do economic conditions, politics, policy or risk appetite reach this industry? Describe the industry channel. | Industry JSON |
| L2 — Mechanisms shared across the industry | How do demand, supply, pricing and bargaining power interact along the value chain? Include industry-specific mechanisms such as patent expiry in pharmaceuticals or production cycles in agriculture. | Industry JSON |
| L3 — Company or fund exposure | Which business role, revenue stream, cost or holding exposes this subject to the mechanism? Explain why peers can respond differently. | `company.json` or `fund.json` |

## Building the graph

- **Reuse shared concepts.** At L1 or L2, if a concept already exists in `market_structure` or another industry map, link it via `related_concepts` and state the industry-specific question.
- **Explore in both directions.** Trace demand upstream to inputs, labor, equipment and capital. Trace supply shocks downstream through capacity, inventory, substitutes and customers. Compare disclosures from different business roles and periods.
- **Write the mechanism.** Name the cause, channel, affected variable and any condition that changes the direction. For example, a flock loss can raise egg prices while reducing an affected producer's saleable volume. For a structural mechanism with missing data, record the measurement gap. A price correlation alone cannot establish a causal edge.
- **Apply the cross-era check.** Could a researcher decades earlier have proposed this relationship with what was knowable then? Keep enduring mechanisms such as demand for computing capacity in the prior. Put particular booms, winners and observed values in dated evidence. For a newer product or institution, state when or under what conditions the mechanism applies.
- **Describe what can be observed.** On the concept, set `observability` to `direct`, `proxy` or `none`. For `proxy` or `none`, use `observable_with` to describe what data would measure it. If a direct measure exists but has yet to be collected, keep `direct` and record the data gap.
- **Define a test when a path is ready.** Choose a method suited to the mechanism and the timing of its disclosures. A `test_criteria` block specifies `trigger`, `observable`, `window`, `supports_if` and `contradicts_if`. State the expected direction when the hypothesis implies one. Support and contradiction must be mutually exclusive for the same observation and window. Treat a discontinued disclosure as a data gap.
- **Choose proxies before examining returns.** Check overlap with the subject. In the factor definition, set `reads` to `proxy`. Use `note` to explain what the series measures, what it misses and what each additional proxy contributes.
- **Respect release timing.** In the [series binding](../bindings/series_bindings.json), record the earliest verified public release in `first_published`, including for backfilled series. Set `publication_delay_minutes` and `revisable`. For a revisable value, replay only a version demonstrably available before the decision cutoff.

## Before handoff

Walk a demand, supply and external shock through the graph, then trace each major role back to its revenue, costs and risks. Merge duplicate paths and name the material gaps.

When adopting a map revision, update `sector.content_revision` and `sector.content_sha256` in every referencing profile. Validate and commit the map and profiles together. Set `prior_commit` in [factor definitions](../bindings/factor_definitions.json) to that SHA. For each new or changed series binding the generator uses, pin its digest under its series ID in `bindings_sha256`. Verify the projections and commit the updated definitions separately.

Assign prior status per decision, regardless of when the replay runs. Use `exploratory` when `decision_time < frozen_at` and `prior_available_at_decision` otherwise. Out-of-sample evaluation and promotion have their own study criteria.
