# Research knowledge base

Market maps in [prior_knowledge](prior_knowledge/) describe subjects, economic relationships and observable series. The causal-map generator reads these priors and their bindings. Reusable modeling methods live in `math/`. Study-specific combinations live in `experiences/`.

For example, the [barrier-label study](experiences/barrier_label_tree.json) uses [triple-barrier labeling](math/labeling/triple_barrier.json) to define its outcome and a [gradient-boosted tree classifier](math/predictor/gradient_boosted_trees.json) to predict it. [LightGBM](math/tools/lightgbm.json) implements the predictor. The study also points to the existing SOXX and QQQ fund profiles.

Each method or study has a stable `id` and a `kind`. Its `links` are directed from the entry to the referenced ID. Follow `uses_label`, `uses_predictor` and `uses_tool` to find a study's dependencies, `implements` to find the method a tool provides, and `studied_on` to find a market subject by its existing profile ID. Record study settings and evidence in the study entry so other subjects can reuse the same method with different settings.
