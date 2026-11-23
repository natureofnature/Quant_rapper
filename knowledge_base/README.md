# Research knowledge base

Browse the [quantitative methods index · 量化方法速查](math/README.md) by research question. To search in Chinese or English and follow method relationships, run `python3 knowledge_base/prior_knowledge/Relationships/relationship_visualizer.py methods` from the repository root and open `knowledge_base/math/methods.html`.

Market maps in [prior_knowledge](prior_knowledge/) describe subjects, economic relationships and observable series. The causal-map generator reads these priors and their bindings. Reusable modeling methods live in `math/`. Study-specific combinations live in `experiences/`.

The [triple-barrier study](experiences/barrier_label_tree.json) uses [triple-barrier labeling](math/labeling/triple_barrier.json) to define its outcome and a [gradient-boosted tree classifier](math/predictor/gradient_boosted_trees.json) to predict it. [LightGBM](math/tools/lightgbm.json) implements the predictor. The study also points to the existing SOXX and QQQ fund profiles.

Each method or study has a stable `id` and a `kind`. Its `links` are directed from the entry to the referenced ID. Follow `uses_label`, `uses_predictor` and `uses_tool` to find a study's dependencies, `implements` to find the method a tool provides, and `studied_on` to find a market subject by its existing profile ID. Record study settings and evidence in the study entry so other subjects can reuse the same method with different settings.

Within the method catalogue, `uses_method` identifies a component, `can_use` identifies an optional choice, `extends` names a base method, and `supports` links a modeling tool to a problem type it can express. A method entry documents a research option; its presence alone makes no claim about performance on any fund.
