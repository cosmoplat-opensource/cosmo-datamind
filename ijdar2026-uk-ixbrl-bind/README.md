# UK-iXBRL-Bind: reproducibility release

Release accompanying the manuscript **"Visual Fact Binding of Rendered Financial Statements to iXBRL: A Benchmark and a
Diagnostic Study"** (Jinze Yu; submitted to the International Journal on Document Analysis and Recognition, IJDAR).
It contains the benchmark (target boxes, references, candidate inventories), every language-model request design and
response, every reported configuration's predictions with their manifests, the dated protocol files, and the code
that builds, runs and scores everything. 1,454 files, 490 MB; `MANIFEST.sha256` lists the sha256 of
every file.

## Quick start

```bash
cd ijdar2026-uk-ixbrl-bind
python3 unpack.py --verify          # check MANIFEST.sha256, then restore the run layout (about 11 GB unpacked)
python3 release_tools/score_offline.py heldout \
    runs/cvpr_complete_v10_20261014/native_correspondence/heldout_v1/llm/fewshot_history/deepseek_deepseek-flash/answers
cd cvpr_complete_v9 && python3 ../paper_ijdar/figures/paper_stats.py      # Table 6 lists, reachability, year ends, seed spread
```

Large files are stored xz-compressed (`<name>.xz`), directories of many small files as `<dir>.tar.xz`, and anything
above 95 MB compressed as numbered parts (`<name>.part000`, `.part001`, ...). `unpack.py` restores all of them in place
(parts are concatenated in order: `cat x.part* > x` is equivalent). Nothing here needs network or API access.

## Contents

| Path (after `unpack.py`) | What it is |
|---|---|
| `cvpr_complete_v9/` | All code: rendering (`native_v30_render.py`, `heldout_render_v1.py`), OCR and word features (`native_v30_perception.py`, `heldout_perception_v1.py`, `encoder.py`), targets (`heldout_targets_v1.py`), candidate inventories (`native_witness_inventory_v23.py`, `native_v26_bank.py`, `native_v30_bank.py`, `heldout_bank_v1.py`), priors (`native_structural_prior_v27..v34*.py`, `native_presentation_v34.py`, `heldout_priors_*.py`), learners (`native_scope_learning_v23..v34*.py`), scoring (`native_scope_score_*.py`, `score_native_binding_v17.py`, `compare_*.py`), language-model baselines (`llm_baseline_v30.py`, `llm_fewshot_*.py`, `heldout_llm_*.py`), sealed selection (`sealed_select_v1.py`, `sealed_sources_v1.py`), the paper's crop figures (`make_paper_figures_v1/v2.py`), shell drivers and unit tests (`tests/`). Earlier exploratory stages of the same project are kept because later stages import them. |
| `paper_ijdar/figures/` | `make_figures.py` (data figures), `paper_stats.py` (Table 6 lists, reachability, year ends, seed spread and other inline numbers), `reanalysis_v1.py` and its output `reanalysis_v1.json` (Holm tests, glyph-rule subset, value errors), `reanalysis_v2.py` and `reanalysis_v2.json` (period errors before the structural prior, repeated row labels), `choices.json` (cells shown in the crop figures). `paper_ijdar/build_supplement_md.py`, `paper_ijdar/latex/build_tex.py`: manuscript build. |
| `runs/.../native_correspondence/public/`, `scoring_only/`, `training_only/` | Development targets (task list with target boxes and page sha256), the scorer's reference provenance and the 2023 teacher cells with filed tuples. `scoring_only/references_resolved_dev.json` holds the 560 development evaluation references as resolved by the paper's scorer (see *Code changes*). |
| `.../witness_composition_v23/`, `.../common_candidate_v26/`, `.../common_candidate_v30/` | The three development candidate inventories (`public/tasks.json`, `records.json`, `witnesses.json`, `manifest.json`): v23 (original), v26 (repair; used by v26-v29), v30 (perception fix and repair II; used by v30-v34 and the no-LLM system). |
| `.../common_candidate_v27` ... `v34_nollm`, `*_ablation_*`, `v33loco` | Every reported configuration: `learning_v1/predictions/` (all six learners x 2 orders x 3 seeds x budgets 0/5/10), `manifest.json` (sha256 of every prediction file and of the design), `design.json`, `development_results.json` (per-configuration and per-company counts, paired comparisons, candidate availability per target), `paired_vs_*.json` (every paired comparison of the paper), `local/` and `parents/*/metadata.json` (training exposure records), `source_snapshot/` (code as run). |
| `.../common_candidate_v30/prior_v27` ... `prior_v34*_field`, `linkbase_v34/` | Fitted prior weights and their features; presentation/calculation-linkbase features of v34. |
| `.../common_candidate_v30/llm_baseline_v30/`, `llm_fewshot_rank_v1/`, `.../common_visual/` | Development language-model runs: `design.json` (model, temperature, prompt-template sha256, start time) and `answers/` (one file per request: parsed answer, binding, raw response text, model identifier returned by the API, token usage). `common_visual/` is the shared page reading (283 batched calls: request batch, raw response, attempt status). `*_ctxfix40` are the 40 re-sent requests of Section 5.3. |
| `.../heldout_v1/`, `.../sealed_v1/` | Held-out and sealed splits: `public/` (targets), `bank/` (candidate inventory and priors), `localization/` (target anchors), `schema/packages/` (package URL, sha256 and member list of every filing), `renders/manifest.json`, `scoring_only/references.json`, `training_only/` (teacher cells), `llm/` (all language-model runs), `evaluation_*` (predictions, manifests, results with per-cell correctness, paired comparisons). Held-out `evaluation_v1..v5` = v31, v32, v33, v34, no-LLM system. Sealed: `selection.json` (rule, seed, order, corrections), `sources_manifest.json`; `llm/fewshot_linkbase/deepseek_deepseek-flash_no_period_end/` is the first annotated-control run reported in Section 5.5. |
| `.../common_candidate_v26/posthoc_visual_audit_v1/` | Tag-cell correspondence audit (Section 3.5): `sample.json` (100 sampled targets with reference tuples, the content of each audit card), `card_list.txt`, `audit_judgments.jsonl` (per-target verdict and rationale), `audit_results.json`. |
| `.../source_qualification/` | Registry checks of the 2026-10-16 roster freeze (`registry_metadata_v1/design.json` lists every reserved LEI and its role). |
| `runs/visual_scope_relations_v8_20261009/` | `native_source_reservation_v1/metadata_reservation.json`: the hash-ordered reservation of 2026-10-09 (16 development/held-out, 8 calibration, 60 sealed, 318 spares) with the sha256 of the index pages; `scope_boundary_development_v1/download_plan.json`: development filing list with filings.xbrl.org URLs. |
| `runs/cvpr_complete_v9_20261013/expanded_development_sources/` | Development render and source manifests (selected pages, viewports, sha256 of every source XHTML and page render) and the reference view of the filed facts on those pages. |
| `ext/xbrl_scope_source_index/` | The filings.xbrl.org GB index pages as fetched on 2026-10-09. |
| `release_meta/` | `split_roster.tsv` (every reserved LEI, its role and split), `sealed_60_leis.txt`, `page_hashes.tsv` (sha256 of every page render used), `llm_calls_index.tsv` (one row per stored response: file time, model identifier returned, usage), `llm_runs.json` (design of every run), `scrubbed_files.tsv`, `requirements.txt`, `derived/dev_glyph_rule.json`. |
| `release_tools/score_offline.py`, `unpack.py` | Offline scorer for any prediction or answer set; archive restore. |

Sizes as stored:

| Directory | Size |
|---|---:|
| `cvpr_complete_v9` | 2.3 MB |
| `cvpr_complete_v9/tests` | 0.2 MB |
| `ext/cvpr_public_v9` | 0.0 MB |
| `ext/shared_semantic_encoder` | 0.0 MB |
| `ext/xbrl_scope_source_index` | 8.1 MB |
| `paper_ijdar` | 0.0 MB |
| `paper_ijdar/figures` | 0.1 MB |
| `paper_ijdar/latex` | 0.0 MB |
| `release_meta` | 1.6 MB |
| `release_meta/derived` | 0.0 MB |
| `release_tools` | 0.0 MB |
| `runs/cvpr_complete_v10_20261014` | 9.7 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/common_candidate_v26/features` | 0.1 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/common_candidate_v26/learning_v1` | 9.8 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/common_candidate_v26/posthoc_visual_audit_v1` | 0.2 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/common_candidate_v26/public` | 60.1 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/common_candidate_v27/learning_v1` | 9.8 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/common_candidate_v28/learning_v1` | 9.8 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/common_candidate_v29/learning_v1` | 9.7 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/common_candidate_v30/features` | 0.1 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/common_candidate_v30/learning_v1` | 10.1 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/common_candidate_v30/linkbase_v34` | 0.2 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/common_candidate_v30/llm_baseline_v30` | 0.5 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/common_candidate_v30/llm_fewshot_rank_v1` | 0.1 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/common_candidate_v30/prior_v27` | 4.4 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/common_candidate_v30/prior_v28` | 0.8 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/common_candidate_v30/prior_v29` | 2.4 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/common_candidate_v30/prior_v31` | 2.3 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/common_candidate_v30/prior_v32` | 4.4 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/common_candidate_v30/prior_v33_field` | 2.4 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/common_candidate_v30/prior_v33_structural` | 4.4 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/common_candidate_v30/prior_v33loco_field` | 2.4 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/common_candidate_v30/prior_v33loco_structural` | 4.5 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/common_candidate_v30/prior_v34_field` | 2.2 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/common_candidate_v30/prior_v34_nollm_field` | 2.2 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/common_candidate_v30/prior_v34loco_field` | 2.3 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/common_candidate_v30/public` | 63.0 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/common_candidate_v30_ablation_none/learning_v1` | 9.9 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/common_candidate_v30_ablation_p27/learning_v1` | 9.9 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/common_candidate_v30_ablation_p27p28/learning_v1` | 9.8 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/common_candidate_v31/learning_v1` | 9.8 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/common_candidate_v32/learning_v1` | 10.0 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/common_candidate_v32_ablation_nollmfield/learning_v1` | 9.8 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/common_candidate_v32_ablation_nopair/learning_v1` | 9.9 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/common_candidate_v32_ablation_nostructural/learning_v1` | 9.9 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/common_candidate_v33/learning_v1` | 9.9 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/common_candidate_v33loco/learning_v1` | 9.8 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/common_candidate_v34/learning_v1` | 10.1 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/common_candidate_v34_nollm/learning_v1` | 10.1 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/common_candidate_v34_top40/learning_v1` | 2.2 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/common_candidate_v34loco/learning_v1` | 2.2 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/full_binding_preparation_v14/training_only` | 0.5 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/heldout_v1/bank` | 14.5 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/heldout_v1/evaluation_v1` | 1.2 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/heldout_v1/evaluation_v2` | 1.2 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/heldout_v1/evaluation_v3` | 1.2 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/heldout_v1/evaluation_v4` | 1.2 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/heldout_v1/evaluation_v5` | 1.2 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/heldout_v1/llm` | 0.1 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/heldout_v1/localization` | 0.0 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/heldout_v1/public` | 0.1 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/heldout_v1/renders` | 0.0 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/heldout_v1/schema` | 1.5 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/heldout_v1/scoring_only` | 0.1 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/heldout_v1/training_only` | 0.0 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/perception_v30/localization` | 0.2 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/perception_v30/renders` | 0.0 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/sealed_v1/bank` | 71.8 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/sealed_v1/evaluation_v34` | 3.8 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/sealed_v1/evaluation_v34_nollm` | 3.9 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/sealed_v1/llm` | 0.5 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/sealed_v1/localization` | 0.1 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/sealed_v1/public` | 0.5 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/sealed_v1/renders` | 0.0 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/sealed_v1/schema` | 1.3 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/sealed_v1/scoring_only` | 0.6 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/sealed_v1/training_only` | 0.2 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/source_qualification/metadata_only_v1` | 0.6 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/source_qualification/registry_metadata_v1` | 0.6 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/witness_composition_v23/engineering_preflights` | 0.0 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/witness_composition_v23/features` | 0.1 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/witness_composition_v23/learning_v1` | 7.8 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/witness_composition_v23/posthoc_candidate_ceiling_v26` | 0.2 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/witness_composition_v23/posthoc_candidate_omissions_v25` | 1.2 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/witness_composition_v23/posthoc_development_v24` | 0.2 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/witness_composition_v23/public` | 8.8 MB |
| `runs/cvpr_complete_v10_20261014/native_correspondence/witness_composition_v23/training_only` | 0.2 MB |
| `runs/cvpr_complete_v9_20261013` | 10.9 MB |
| `runs/visual_scope_relations_v8_20261009` | 0.2 MB |
| `unpack.py` | 0.0 MB |
| `visual_scope_relations_v8` | 0.0 MB |

## Not included, and why

* **Issuer report packages** (Inline XBRL XHTML, extension taxonomies): copyright of the issuers. They are re-fetched
  from the public index by URL; the package and report sha256 are in `schema/packages/*.json`, `sealed_v1/sources_manifest.json`
  and the development source manifests, so a re-fetched file can be checked.
* **Page renders, audit-card images and full-page OCR output** (`ocr/`, `localization/public_pages.json`): page images
  and the complete page text are reproductions of the copyrighted reports. The rendering and OCR scripts, the pinned
  tool versions and the sha256 of every render (`release_meta/page_hashes.tsv`, render manifests) are included, so that
  users can re-create the pages and verify them. Short target-local OCR strings that the methods consume (row label,
  header phrases, the glyph in the box, date/currency/scale witnesses) remain inside the task lists and inventories.
* **Word features and embeddings** (`.npy`, `.npz`, `native_word_features/`, `features/*.npz`): regenerable
  deterministically from the renders with the included scripts and the pinned LayoutLMv3 and MiniLM revisions.
* **Composer weights** (`.pt`): regenerable with the training scripts (fixed seeds 19, 41, 73); every prediction they
  produced is released with its hash. `native_v34_top40_eval.py` loads the v34 parent weights written by
  `native_scope_learning_v34.py`, so that run must be repeated first; the script re-fits the local weights itself and
  checks their exposure sequence against the released v34 records (all 168 re-fitted local weights matched the
  original hashes, and `--check` reproduced the v34 predictions without the restriction).
* **Logs, account probes, credentials**: not released. The API scripts read keys from the environment or a local
  `.env` file, which is not part of the release.
* The tag-cell audit was run in an interactive session (Claude); its cards were images and are not released, but the
  card content (crop location, reference tuple) is in `sample.json` and every verdict with its rationale is released.

## Re-fetching filings and re-rendering pages

All filings are ESEF reports of the public index https://filings.xbrl.org (country GB), whose JSON:API listing
(`https://filings.xbrl.org/api/filings?filter[country]=GB`, paginated) was fetched on **2026-10-09**; the pages as fetched
are in `ext/xbrl_scope_source_index/`. Each filing is addressed as
`https://filings.xbrl.org/<LEI>/<period end>/ESEF/GB/0/<package name>.zip`; the exact package and report URLs are in
`download_plan.json` (development, held-out), `sealed_v1/sources_manifest.json` (sealed) and `schema/packages/*.json`.
Place the reports as `ext/xbrl_scope_additional_development/<LEI>_<period end>/original.xhtml` (development and
held-out) or `ext/xbrl_scope_sealed_test/...` (sealed), then run `native_v30_render.py` / `heldout_render_v1.py`
(Playwright chromium-headless-shell build 1228, device scale factor 2, filing-specific viewport) and
`native_v30_perception.py` / `heldout_perception_v1.py` (Tesseract 5.5.2, English; LayoutLMv3 + MiniLM word
features). Compare your renders with `release_meta/page_hashes.tsv`; rendering is deterministic for a fixed browser
build and font set, and different fonts change the hashes but rarely the OCR.

The sealed roster was audited and frozen on 2026-10-16 (`source_qualification/registry_metadata_v1/design.json`); the
sealed selection rule was frozen at 2026-10-26T14:27:52.573660+00:00 before any sealed filing was downloaded (`sealed_v1/selection.json`).
The fifty unopened sealed companies are listed only by LEI (`release_meta/sealed_60_leis.txt`); their filings are
public, so the seal is a commitment by the author, verifiable only through these dated files.

## Where each number comes from

Scripts run from `cvpr_complete_v9/` after `unpack.py`. "R" = `runs/cvpr_complete_v10_20261014/native_correspondence`.

| Manuscript item | Script | Released inputs |
|---|---|---|
| Table 1, Table S1, Figure S1 | no dedicated script: counts of the files listed (`make_figures.py 5` draws Figure S1 from the Table S1 numbers) | `R/common_candidate_v30/public/tasks.json`, `R/heldout_v1/bank/public/tasks.json`, `R/sealed_v1/bank/public/tasks.json`, `scoring_only/references*.json`, `R/sealed_v1/selection.json`, `ext/xbrl_scope_source_index/` |
| Table 2, Figure 4, Figure S3, Table S3 | `native_scope_score_v23..v34*.py` (wrote the results); `make_figures.py 6 7`; `paper_stats.py` | `R/<config>/learning_v1/development_results.json` and `paired_vs_*.json` for `witness_composition_v23`, `common_candidate_v26..v34`, `v34_nollm`, `v30_ablation_*`, `v32_ablation_*`; reachability = `candidate_availability_*` in the same files |
| Table 3 | `compare_llm_run_v1.py`, `regen_llm_paired_v1.sh`, `rule_only_baseline_v30.py` | `R/common_candidate_v32|v34|v34_nollm/learning_v1/paired_vs_*_llm.json`, `R/common_candidate_v30/llm_baseline_v30/*/answers`, `R/common_candidate_v30/rule_only_baseline_v30.json` |
| Table 4 | `heldout_predict_v1.py`, `compare_heldout_v1.py` | `R/heldout_v1/evaluation_v1..v5/results.json`, `paired_vs_*.json`, `R/heldout_v1/llm/*/answers` |
| Table 5 | `heldout_predict_v1.py` (sealed root), `compare_heldout_v1.py` | `R/sealed_v1/evaluation_v34*/results.json`, `paired_vs_*.json`, `R/sealed_v1/llm/*/answers` |
| Table 6, Figure S7 | `make_figures.py 13`; `paper_stats.py` | predictions of v34 on the three splits, `R/*/llm*/fewshot_*` answers, inventories (40-concept lists) |
| Table S2 | `release_meta/llm_runs.json`, `release_meta/llm_calls_index.tsv` | every `design.json` and `answers/` directory |
| Figure 1 | `make_paper_figures_v2.py` | needs your own render of the page (see above) plus the released tasks, inventory and predictions |
| Figure 2, Figure S2, Figure 5 | `make_paper_figures_v2.py` (fig2_example, fig5_segmentation, fig8_errors) | as Figure 1; `paper_ijdar/figures/choices.json` |
| Figure 3 | `make_figures.py 3` (fig4_pipeline; a diagram, no data) | — |
| Figure 6, Figure 7 | `make_figures.py 8 9` | predictions and answers of the three splits, `paired_vs_fewshot*_budget10.json` |
| Figures S4, S5, S6 | `make_figures.py 10 11 12` | as Figures 6 and 7 |
| Sections 5.1, 5.6, S2.6, S3.6: v34 with leave-one-company-out priors and v34 restricted to the 40-concept prompt list (post hoc) | `native_structural_prior_v34_loco.py`, `native_v34loco_data.py`, `native_v34loco_eval.py`, `native_v34_top40_eval.py`; `paper_ijdar/figures/reanalysis_v3.py` -> `reanalysis_v3.json` | `R/common_candidate_v34loco/`, `R/common_candidate_v34_top40/` (semantic-memory composer only), `R/common_candidate_v30/prior_v34loco_field/` |
| Section 5.2 / S3.3 period errors before the structural prior and repeated row labels | `paper_ijdar/figures/reanalysis_v2.py` -> `reanalysis_v2.json` | v26 and v32 development predictions, `prior_v29/row_labels.json`, references |
| Section 5 inline numbers (Holm tests, glyph-rule subset, value errors) | `paper_ijdar/figures/reanalysis_v1.py` -> `reanalysis_v1.json` | predictions, answers, references; Part 2 additionally reads the page OCR, whose per-cell outcome is released as `release_meta/derived/dev_glyph_rule.json` |

**Recomputing without API access.** Every language-model output used in the paper is stored in an `answers/` file and
is read from there; no script contacts a model unless a request is missing. To recompute the tables from the frozen
predictions use the scripts above or `release_tools/score_offline.py`; to rerun a learner use the corresponding
`native_scope_learning_*.py` / `heldout_predict_v1.py`, which additionally need the word features (regenerate them
first, see above). Prompts are not stored verbatim: each run records the sha256 of its prompt template
(`design.json`), and the request of every cell is rebuilt deterministically by the baseline script from the released
inventory and the page crop (image requests) or the OCR table context.

## Code changes made for the release

The code is the code that produced the results, with two mechanical changes: absolute local paths are rewritten to
paths relative to this directory (files listed in `release_meta/scrubbed_files.tsv` with original and released sha256;
manifests inside the runs hash the original files), and `score_native_binding_v17.scoring_references` reads
`scoring_only/references_resolved_dev.json` when the development filings are absent (if they are present it parses
them, as in the paper). The figure scripts use Helvetica Neue when available.

## Known inconsistencies in released files

- `sealed_v1/selection.json`: the rule text says the filings index was "fetched 2026-10-16". The index files and the
  reservation hashes in this release date from 2026-10-09, the date given in the manuscript; 2026-10-16 is the date the
  sealed roster was audited and frozen (`registry_metadata_v1/design.json`).
- The designation of the main family of comparisons (manuscript Section 5.1) is recorded in the manuscript drafts only;
  no separate dated file exists, and the family was not pre-registered.
- `sealed_v1/selection.json` (`eligible_total` 42 of 60): `sealed_select_v1.py` read the index through the unsorted
  pages `ext/xbrl_scope_source_index/gb_page_*.json`, which repeat 208 and omit 208 of the 2,959 records; in the
  stable-sorted pages `gb_stable_page_*.json` used for the reservation all 60 sealed companies have filings for 2023,
  2024 and 2025. The ten test companies were therefore drawn from 42 rather than 60 (manuscript Section 5.5 and S3.5).
- Design records: the `fewshot_history` and `fewshot_linkbase` `design.json` files store neither the temperature nor a
  prompt-template hash (the code sends temperature 0); the sealed `fewshot_linkbase` design says `split: "heldout"`;
  the Poe designs say `thinking: disabled`, although no thinking parameter is sent. The audit cards themselves are not
  released (sample, card list and verdicts are).

## Environment

Python 3.13.9 (macOS 26, arm64), CPU only for training (2 intra-op threads). Packages: `release_meta/requirements.txt`
(torch 2.14.1, numpy 2.5.3, transformers 5.19.0, lxml 6.1.3, ...). Tesseract 5.5.2 (leptonica 1.87.0, English data);
Playwright 1.61.0 with chromium-headless-shell build 1228; encoders `microsoft/layoutlmv3-base` (revision
cfbbbff0762e6aab37086fdd4739ad14fe7d5db4) and `sentence-transformers/all-MiniLM-L6-v2` (revision
1110a243fdf4706b3f48f1d95db1a4f5529b4d41). Language models: deepseek-flash (DeepSeek API alias), and gpt-5.4,
claude-sonnet-5.5, gemini-3.1-pro through the Poe API (Table S2).

## Licences

* Code: Apache License 2.0, as the rest of this repository (`../LICENSE`).
* Derived data (target lists and boxes, candidate inventories, priors, predictions, audit verdicts, manifests): Creative
  Commons Attribution 4.0 International (CC BY 4.0).
* Filed facts (references, teacher cells) are public regulatory data of the issuers, taken from their filings on
  filings.xbrl.org; annual reports remain the copyright of their issuers and are not redistributed.
* IFRS Accounting Taxonomy element names and labels appear under the IFRS Foundation's taxonomy licence.
* Language-model responses are released as data under the providers' terms of use (DeepSeek; Poe for the GPT,
  Claude and Gemini runs).
* LayoutLMv3 is licensed CC BY-NC-SA 4.0 by its authors; no derived feature is released.

## Citation

Jinze Yu. Visual Fact Binding of Rendered Financial Statements to iXBRL: A Benchmark and a Diagnostic Study.
Manuscript submitted to IJDAR, 2026.
