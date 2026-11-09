# Reproducibility package: "What Does an Ontology Layer Add to a Data Agent? Evaluation Pitfalls and Conditions of Usefulness" [Experiment, Analysis & Benchmark]

PVLDB Volume 20 submission. This directory holds everything needed to recompute every number in the paper from the
released predictions, and to rerun the experiments from scratch.

## Contents

| Directory | What it holds |
|---|---|
| `harness/` | Experiment and analysis scripts (run with Python 3.13; standard library plus `sqlglot` for `make_opaque*.py`). `harness/figures/make_figures.py` draws Figures 3–12 (matplotlib; it reads the experiment-root layout described below, with `make_figures.py` placed in `<root>/paper_vldb/`). |
| `evoontology_changes/` | Our changes to EvoOntology's BIRD benchmark runner, as a patch against the commit in `BASE_COMMIT`, plus new files (DeepSeek configuration, documentation context modes, the compiled-ontology gate). |
| `splits/` | The frozen question splits: `minidev` (fold A→B) and `minidev_rev` (B→A) of BIRD Mini-Dev, `ind_fwd` / `ind_rev` of the two industrial databases. Each has `construction/`, `validation/`, `test/` and a `split_manifest.json`. |
| `benchmarks/` | BIRD-opaque, BIRD-opaque-merged and BIRD-readable-merged without the databases: name mappings, rewritten questions and gold SQL, re-keyed documentation, construction workloads and `verify.json` (result check of every rewritten gold query). `nofk/` and `bare/` hold the checks of the earlier schema variants. |
| `ontologies/` | Every ontology used: `evoontology/<build>/<db>/versions/...` (EvoOntology record format), `datamind/<build>/ir/<db>.json` (DataMind IR with relation evidence), `datamind_converted/` (DataMind ontologies for the merged schemas in EvoOntology's format, as consumed by the agent), `evoontology_ablated/` (EvoOntology's merged-schema ontologies without relations or without term-to-column mappings, Section 6.1). |
| `results/v5/` | Post-hoc runs (Figure 7; Sections 4.2, 5, 6.1, 6.5): component ablation and 60-turn budget on BIRD-opaque-merged, and BIRD-readable-merged; same layout as `results/v4/predictions/`, plus `stats_v5.json`. |
| `results/v4/` | Main matrix (Table 2, Figures 5, 6, 10, 11): `predictions/<setting>_<fold>/<condition>_r<repeat>/all_results.json.gz` (predicted SQL, turns, ontology tool calls) and `rescore.json` (per question: [strict, column-tolerant]); statistics and audits; `dr093_frozen_code/` (code behind Figure 9); `discarded_infra/` (the eight units discarded by the infrastructure audit of Section 3.2 and rerun; kept for inspection, used in no statistic). |
| `results/v2/`, `results/v3/` | Per-question scores of the matched comparison (Table 1, Figure 3) and of the DataMind-chain runs (Figure 12); each system's own-scorer statistics in `results/v2/completion/`; raw outputs of the DataMind chain in `*_datamind_chain_outputs.tar.xz`. |
| `results/v3_validation/` | Validation runs used during development (numbers marked "validation" in Sections 6.1–6.2); their counts in `val_mg_summary.json`; the ontology tool responses of the converted Section 6.2 ontology in `mg_reactdm94_fwd_val_traces.tar.xz`. |

Setting names: `R` readable BIRD Mini-Dev, `O` BIRD-opaque, `MG` BIRD-opaque-merged. Conditions: `none`, `doc`
(documentation), `evo`, `evodoc`, `dm`, `dmdoc`, `M` (compiled DataMind ontology with check), `dm93` / `M93` (Section 6.2).

## Where each number comes from

| Paper | Script | Output |
|---|---|---|
| Table 1, column 1; Figure 3, first bar | each system's own scorer | `results/v2/completion/最终统计与恢复披露.md` (Chinese headings) |
| Table 1, rows 1–2, columns 2–3; Section 3.3; Figure 3 | `rescore_v2.py`, `stats_v3.py` | `results/v2/rescore/统计_v3.md` ("strict"/"tolerant" sections), `results/v2/rescore/check.json` |
| Table 1, row 3 and note (+7.5, +1.6); Section 3.4; Figure 3, last bar | `rescore_v3.py`, `analysis_sec3.py` | `results/v4/analysis_sec3.json` |
| Figure 2 | — | schematic with illustrative values |
| Section 3.2 (reruns after the infrastructure audit) | `audit_infra_v4.py` | `results/v4/discarded_infra/` |
| Section 4, Figure 4 (benchmark checks, leakage audit) | `make_opaque.py`, `make_opaque_merged.py`, `audit_evo_builds.py` | `benchmarks/*/verify.json`, `benchmarks/opaque_merged/mapping.json`, `results/v4/evo_build_audit.json` |
| Table 2, Figure 5, Section 5 | `queue_v4.py` (runs), `score_v4_all.sh` → `score_react_dir.py`, `stats_v4.py` | `results/v4/统计_v4.md`, `results/v4/stats_v4.json` |
| Figure 6 (per source database) | `analysis_perdb.py` | `results/v4/analysis_perdb.json` |
| Figure 7; Sections 4.2, 5 (readable names, large schema), 6.1 (ablation), 6.5 (60 turns) | `make_readable_merged.py`, `make_v5_ablations.py`, `queue_v5.py`, `score_v5_all.sh`, `stats_v5.py` | `results/v5/stats_v5.json`, `benchmarks/readable_merged/verify.json` |
| Section 3.2 (repeat flips), Section 3.4 (BIRD extra columns by type, at most one extra column), Section 5 (Holm, equivalence tests), Section 6.1 (stratification), Section 7 (prior-knowledge sensitivity) | `analysis_v5_robust.py`, `analysis_v5_strat.py`, `analysis_v5_prior.py` (shared code in `analysis_v5_common.py`) | `results/v4/analysis_v5_robust.json`, `analysis_v5_strat.json`, `analysis_v5_prior.json` |
| Section 6.1, Figure 8 | `analysis_table4.py` | `results/v4/analysis_table4.json` |
| Sections 6.1–6.2, validation counts (61 questions of fold A→B; 28 of 61 tool responses) | `analysis_val_mg.py` | `results/v3_validation/val_mg_summary.json` |
| Section 6.2, Figure 9 | `stats_dr093.py`; code in `results/v4/dr093_frozen_code/` | `results/v4/stats_dr093.json` |
| Sections 6.3–6.5, Figures 10–11 | `analysis_sec6.py` | `results/v4/analysis_sec6.json` |
| Section 6.6, Figure 12 | `rescore_v3.py`, `analysis_ablation_v3.py` | `results/v4/analysis_ablation_v3.json` |
| Section 7 (prior-knowledge check) | `audit_prior_knowledge.py` | `results/v4/prior_knowledge_audit.json` |

All intervals: 95% bootstrap, 10,000 resamples, fixed seeds in the scripts; database-cluster resampling for BIRD and
BIRD-opaque, question-level resampling stratified by source database for the merged schema and the industrial databases.

## Recomputing the statistics from the released predictions

The scripts expect one experiment root with this layout (paths are relative to the root):

```
<root>/
  *.py, *.sh                      <- contents of harness/
  EvoOntology/                    <- EvoOntology at BASE_COMMIT, patch applied, new files copied in
    benchmarks/bird/data/mini_dev_data/dev_databases/<db>/<db>.sqlite   <- BIRD Mini-Dev (download separately)
    benchmarks/bird/data/{minidev,minidev_rev,ind_fwd,ind_rev}/         <- splits/
  variants/{opaque,opaque_merged}/                                       <- benchmarks/ plus databases built by make_opaque*.py
  runs/v4/qa/...                  <- results/v4/predictions/ (gunzip all_results.json.gz)
  runs/v2/rescore/, runs/v3/rescore_v3.json                              <- results/v2, results/v3
```

Re-scoring needs the databases, because predicted and gold SQL are executed. With the per-question scores already
released (`rescore.json`), `stats_v4.py`, `stats_dr093.py` and `analysis_ablation_v3.py` run without databases;
`analysis_perdb.py` needs the BIRD databases only to count tables. `analysis_val_mg.py` expects the validation runs under
`runs/v3/dev/<run>/rescore.json` and the extracted traces under `runs/v3/dev/mg_reactdm94_fwd_val/`.

## Rerunning the experiments

1. BIRD Mini-Dev: download from https://github.com/bird-bench/mini_dev (SQLite version) into
   `EvoOntology/benchmarks/bird/data/mini_dev_data/`.
2. Industrial databases `ind108` and `ind200`: generated by `scripts/build_demo_108_db.py` and
   `scripts/build_contract_200_db.py` in this repository (synthetic manufacturing data).
3. Schema variants: `make_schema_variants.py` (nofk, bare), `make_opaque.py`, `make_opaque_merged.py`.
4. Model access: the scripts read an OpenAI-compatible endpoint and key from environment variables
   (`DEEPSEEK_API_KEY` or `BIRD_AGENT_API_KEY`; see `evo_env.sh`). All runs used the `deepseek-flash` model; the
   EvoOntology builder ran through the Claude Code CLI with `--model deepseek-flash`.
5. Ontology builds: `build_evo_v2.py` (EvoOntology builder agent) and `build_dm_v2.py` (DataMind builder: this
   repository at the commit of this branch, checked out next to the experiment root as `../cosmo-datamind-exp`, the
   path `build_dm_v2.py` and `bird_datamind.py` expect); conversion with `dm2evo.py` (`DM2EVO_FULL=1`; Section 6.2 additionally
   `DM2EVO_DEDUP=1 DM2EVO_READABLE=1`).
   Code versions: the ontologies of Sections 3 to 6.1 were built at commit `b9fa576` of this branch (recorded in
   `ontologies/datamind/<build>/meta.json`); the protocol-v3 and v4 answering runs used `b9fa576` plus the ablation
   switches that are part of commit `dbe9260`. Commit `dbe9260` also adds the Section 6.2 builder changes, which are on
   by default; to rebuild the Section 3–6.1 ontologies, check out `b9fa576`, or run `dbe9260` with
   `DATAMIND_ABLATE=grounding,value_semantics` (this still differs from `b9fa576` in not attaching action nodes from the
   manufacturing registry to non-manufacturing schemas). Section 6.2 used the code in `results/v4/dr093_frozen_code/`.
   The answering chain is selected with `DM_ROOT` (set by `run_method.sh` and `queue_v2.py`); the ReAct agent does not
   use DataMind code.
6. Runs: `queue_v4.py plan` then `queue_v4.py run`; `audit_infra_v4.py --requeue` before scoring (runs with an API
   connection error or five or more timeouts are rerun).

7. Protocol v5 (post hoc): `make_readable_merged.py` and `make_v5_ablations.py`; ontologies of BIRD-readable-merged with
   `build_evo_v2.py bird_merged --track w --S readable_merged --dir fwd|rev` and
   `DATAMIND_ABLATE=grounding,value_semantics build_dm_v2.py --track w --S readable_merged --dir fwd|rev bird_merged`
   (one DataMind build at a time), converted with `DM_DBDIR=variants/readable_merged/dev_databases DM2EVO_FULL=1 dm2evo.py`;
   then `queue_v5.py plan` and `queue_v5.py run`, `score_v5_all.sh`, `stats_v5.py`. The 60-turn runs select the
   configurations `deepseek_*_t60.yaml` through `EVO_CFG_SUFFIX=_t60` in `run_method.sh`.

Model outputs vary between runs; the paper reports three repeats per configuration and the released predictions are the
ones behind every number.

Not included: the per-question agent traces of the runs (about 1.9 GB compressed; the traces needed for the 28-of-61
count of Section 6.2 are included), the ontology builders' session logs,
and the databases. They are available from the authors on request.

## Licences

Code: Apache-2.0, as the rest of this repository. Files derived from BIRD (splits, rewritten questions and gold SQL,
re-keyed documentation) are distributed under BIRD's licence, CC BY-SA 4.0, with attribution to Li et al., "Can LLM
Already Serve as a Database Interface? A BIg Bench for Large-Scale Database Grounded Text-to-SQLs", NeurIPS 2023.
The EvoOntology patch applies to EvoOntology (MIT licence), https://github.com/ruc-datalab/EvoOntology.
