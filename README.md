# Quant_rapper

A mid/low-frequency quant research workspace built around a point-in-time causal graph. An LLM proposes economic hypotheses and supply-chain relationships; a pure-math evaluator tests them on beta-adjusted returns; a post-market loop scores forecasts only after they mature.

> **Status:** scaffold only. The packages and folders exist, but no modules are implemented yet. Everything below describes the target architecture.

## Design principles

- **Decision inputs are point-in-time.** Research recipes check the availability of cited evidence. When actual price or series observations are loaded, `quant_pit_data` must select rows available by the decision time.
- **The research map is versioned.** The curated map is a prior: a set of questions to investigate, frozen and recorded by revision for each evaluation. A result for a decision made before that revision was frozen is exploratory, because the leads may have been chosen knowing what happened; such a lead must be confirmed on held-out or later observations.
  Frozen maps use `post_period_knowledge` to flag this pre-freeze use. A recipe records it as `exploratory`. Out-of-sample evaluation also requires a held-out split.
- **Alpha and beta are separate tracks.** Stock-specific signals are evaluated on returns with sector/market beta removed. Sector-level risk gets its own regime track, which can veto alpha signals.
- **No MAE for selection.** Candidates are judged by event-window abnormal returns, directional hit rate and information coefficient.
- **The LLM is fenced in.** Only `quant_causal_graph` may call an LLM. The evaluator has no LLM, network or UI dependencies, so its results are deterministic and replayable.
- **Panel from day one.** Data is keyed `(date, ticker, as_of_time)`. A single-stock study is the N=1 case of the same panel.

## Architecture

System topology: alpha / beta dual track plus the post-market closed loop.

```
              ┌──────────────────────────────────────────────────────┐
              │       quant_contracts  (single contract layer)       │
              │ PIT panel / versioned graph / eval report / forecast │
              └──────────────────────────┬───────────────────────────┘
                                         │  (every package depends on it, one way)
                                         ▼
              ┌──────────────────────────────────────────────────────┐
              │           quant_pit_data  (PIT data base)            │
              │  prices (stocks + ETFs) │ release-date financials    │
              │  macro / volatility     │ news archive               │
              └────────────┬─────────────────────────────┬───────────┘
                           │ immutable PIT snapshots     │ (date, ticker) panel
                           ▼                             ▼
┌───────────────────────────────────────┐   ┌───────────────────────────────────────┐
│          quant_causal_graph           │   │            quant_evaluator            │
│ [only place allowed to call an LLM]   │   │ [pure math: no LLM / network / UI]    │
│                                       │   │                                       │
│ • 3-layer economic hypothesis tree    │   │ ┌─ Alpha track (beta-removed) ──────┐ │
│   (macro / industry / company)        │   │ │ • beta_neutralizer (SOXX / QQQ)   │ │
│ • dated relationship graph            │   │ │ • event_car / directional (no MAE)│ │
│   (2-hop paths)                       │   │ │ • cross_section (phase 3 RankIC)  │ │
│ • evidence mapping                    │   │ └───────────────────────────────────┘ │
│   (direct / proxy / data gap)         │   │ ┌─ Beta track (sector early warning)┐ │
│ • replay of historical decision       │   │ │ • breadth / correlation (crowding)│ │
│   points                              │   │ │ • volatility / macro_slope        │ │
│                                       │   │ │ • regime_gate (red/yellow/green)  │ │
│                                       │   │ └───────────────────────────────────┘ │
│                                       │   │ • splitters (purge + embargo), ledger │
└───────────────────┬───────────────────┘   └───────────────────┬───────────────────┘
                    │  candidate factor recipes  ──────────────▶│
                    └─────────────────────┬─────────────────────┘
                                          ▼
              ┌──────────────────────────────────────────────────────┐
              │      quant_loop  (thin orchestration, closed loop)   │
              │ 1. historical_ablation : fixed-budget flat vs graph  │
              │ 2. post_market_runner  : update → regime light       │
              │ 3. maturity_grader     : grade after 5 / 21 sessions │
              │ 4. feedback_store      : write results back to graph │
              └──────────────────────────┬───────────────────────────┘
                                         │  planned input: benchmark JSON from artifacts/
                                         ▼
              ┌──────────────────────────────────────────────────────┐
              │        apps/  (CLI and frozen read-only workbench)   │
              └──────────────────────────────────────────────────────┘
```

Dependencies are one-way: everything depends on `quant_contracts`, and no core package imports from `apps`.

| Package | Role | May depend on |
| --- | --- | --- |
| `quant_contracts` | Shared schemas: PIT panel, versioned graph edge, factor recipe, evaluation report, forecast ticket. No business logic. | `pydantic` only |
| `quant_pit_data` | Connectors, release-date alignment, immutable snapshots, panel builder. | `contracts` |
| `quant_causal_graph` | Three-layer hypothesis tree (macro / industry / company), dated relationship graph with 2-hop paths, evidence mapping, replay. | `contracts`, `pit_data` |
| `quant_evaluator` | Beta neutralisation, purged and embargoed splits, event-window CAR, directional metrics, sector regime signals. | `contracts` |
| `quant_loop` | Historical ablation, post-market runs, maturity grading, feedback. | all of the above |

## Two loops that must stay separate

**Loop A: historical ablation.** A one-off, budget-limited comparison of `flat-map` against `graph-guided` discovery on frozen PIT snapshots, with a held-out window that is never fed back. Once it passes or fails, parameters are frozen; the same history is not re-tuned.

**Loop B: post-market forward loop.** After each close:

1. Pull new prices, filings, macro series and news.
2. Compute the sector beta regime first (green / yellow / red). Red overrides any stock-level long signal.
3. Update hypothesis states and write an immutable, timestamped forecast ticket for the 5- and 21-session horizons.
4. When a ticket matures, grade it against what actually happened and attribute the miss to either a beta shock or a falsified causal link.

## Roadmap

RFC-002 and RFC-003 can proceed in parallel using the versioned prior and PIT bindings from RFC-001. RFC-004 uses the frozen SOXX/QQQ rule baseline from RFC-002 to evaluate the sector risk model. [Prior and bindings](https://github.com/natureofnature/Quant_rapper/issues/1) · [Market diagnostics](https://github.com/natureofnature/Quant_rapper/issues/2) · [Event causal map](https://github.com/natureofnature/Quant_rapper/issues/3) · [Sector risk model](https://github.com/natureofnature/Quant_rapper/issues/4)

| Phase | Goal | Packages touched |
| --- | --- | --- |
| 1 | Causal chain and PIT relationship graph for a single-stock semiconductor pilot. For any historical decision date, reproducibly output candidate factors and evidence, marking what is observable and what is a data gap. | `quant_contracts`, `quant_pit_data`, `quant_causal_graph`, `configs/sectors` |
| 2 | Dual-track evaluation (beta-adjusted direction plus sector regime warning) and the post-market loop. | `quant_evaluator`, `quant_loop` |
| 3 | Extend to a 20–50 stock panel: cross-sectional ranking, correlation matrix, relative trend. | `configs/universes`, `quant_evaluator` |

## Repository layout

```
pyproject.toml      uv workspace root
packages/           the five packages above, each with its own pyproject.toml
configs/            sectors/  universes/  beta_regimes/
run/<study>/        disposable study scripts, input references and results
apps/               cli/  workbench/
tests/              unit/  invariants/  integration/
artifacts/          snapshots/ for reusable inputs, ablations/ and prospective/ for formal benchmarks
docs/rfcs/          design documents
knowledge_base/     version-controlled research priors, maps and company profiles
```

Before downloading data, check the manifests in `artifacts/snapshots/<version>/` for the source and coverage you need. A study can reuse a frozen input by listing its SHA-256 and repository-relative path in `run/<study>/inputs.sha256`. Run `sha256sum -c run/<study>/inputs.sha256` from the repository root to verify the selected files. Save updated source data under a new snapshot version so earlier studies can replay their original inputs.

Git ignores local studies and raw market data by default. Each snapshot's `manifest.json` can be versioned directly. Add file-specific exceptions for small curated datasets worth preserving, such as filing dates and exchange calendars. When a result becomes a formal reference, add exceptions for its recipe, input references and expected results under `artifacts/ablations/<benchmark>/` or `artifacts/prospective/<benchmark>/`. Record a durable retrieval location for any untracked input required to reproduce that benchmark.

A prospective test is registered before its first event in `artifacts/prospective/<name>/`: `recipe.json` and a `registration.json` that fixes the factors, thresholds, outcome, cutoff rule and reading rule. The commit that adds the registration dates the choice of comparison. That is separate from the recipe's prior status, which dates the research map.

The prior knowledge currently has two broad research groups and one shared layer:

- [Semiconductors](knowledge_base/prior_knowledge/Relationships/semiconductor/semiconductor.json): MU, TSM, and sector funds.
- [Technology platforms](knowledge_base/prior_knowledge/Relationships/technology_platforms/technology_platforms.json): Alphabet (GOOGL), Microsoft (MSFT), and Apple (AAPL). Cloud, advertising, software, AI and device ecosystems are business roles within this group, not separate sectors.

- [Market structure](knowledge_base/prior_knowledge/Relationships/market_structure/market_structure.json): market-wide discount-rate, liquidity and forced-flow mechanisms, with SPY and QQQ as benchmarks. The sector maps refer to these concepts instead of repeating them.

These are research groupings; technology platforms is not an official securities-sector classification.

Each market-structure concept states its observability: `direct` (a public series measures it), `proxy` (a public series needs a stated assumption) or `none`. Concepts that cannot be measured, such as volatility-control flows, are kept so that mechanical causes stay in view. They are never scored as supported or contradicted, and a result conditioned on a proxy is a finding about the proxy. Price-derived indicators such as RSI are not concepts; they belong to the evaluator.
Each company has one profile with candidate links and separate evidence references.
The institution registry links research topics and company disclosures to their sources.
Generate all maps and switch between them using the page's map selector:

```bash
python3 knowledge_base/prior_knowledge/Relationships/relationship_visualizer.py
```

`tests/invariants/` holds the rules that must never break, such as "a decision cannot use evidence or data that was unavailable at its decision time", "a result from before the research map was frozen is labelled exploratory" and "labels never cross into the held-out window".

## Setup

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/). If `uv` is not installed, `pip install uv` provides it.

```bash
uv sync                                                   # install the five packages and pydantic into .venv
uv run python -m unittest discover -s tests/unit
uv run python -m unittest discover -s tests/invariants
```

Both commands must end in a plain `OK`. A result such as `OK (skipped=22)` means the tests ran without pydantic, as they do under a bare system Python: the contract, as-of and event-table tests were skipped, not passed.

## Disclaimer

This is a personal research project. Nothing here is investment advice.
