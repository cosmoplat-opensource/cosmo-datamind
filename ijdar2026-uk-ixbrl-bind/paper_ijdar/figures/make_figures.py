"""Data figures for the IJDAR manuscript.

Functions keep their authoring numbers; the saved names carry the manuscript numbers: fig3() -> fig4_pipeline, fig5() -> fig3_benchmark,
fig6() -> fig6_ceiling, fig7() -> fig7_ladder, fig8() -> fig9_complementarity, fig9() -> fig10_forest, fig10() -> fig11_companies,
fig11() -> fig12_budget, fig12() -> fig13_errors, fig13() -> fig14_abstention.  Literal numbers are copied from Tables 1, 2, 4, 5, 6, 7, 8.

Run from cvpr_complete_v9:  python3 ../paper_ijdar/figures/make_figures.py
Reads frozen run outputs only (predictions, LLM answers, paired-comparison files, scoring references at
figure time, after every prediction is frozen).  Writes PNG (300 dpi) and PDF to paper_ijdar/figures/.
"""

import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import patches
from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm
import numpy as np

CODE = Path(__file__).resolve().parents[2] / "cvpr_complete_v9"
sys.path.insert(0, str(CODE))
from compare_runs_v27 import COMPONENTS, SEEDS, BUDGETS  # noqa: E402
from score_native_binding_v17 import component_correct, scoring_references  # noqa: E402

R = CODE.parent / "runs/cvpr_complete_v10_20261014/native_correspondence"
assert CODE.is_dir() and R.is_dir(), (CODE, R)
OUT = Path(__file__).resolve().parent
DEV_LLM = R / "common_candidate_v30/llm_baseline_v30"

# ---------------------------------------------------------------- style
BLUE, ORANGE, AQUA, YELLOW, MAGENTA, GREEN, VIOLET, RED = "#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"
GRAY, INK, INK2, MUTED, GRID = "#8a8a86", "#0b0b0b", "#52514e", "#8a8a86", "#e6e5e1"
SEQ = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"]
SYS = {"v34": BLUE, "nollm": AQUA, "fewshot": ORANGE, "annotated": VIOLET, "zeroshot": GRAY}
SPLITS = [("dev", "Development (14 companies)"), ("heldout", "Held-out (2 companies)"), ("sealed", "Sealed (10 companies, opened once)")]
MARK = {"dev": "o", "heldout": "s", "sealed": "D"}
FACE = {"dev": BLUE, "heldout": SEQ[2], "sealed": BLUE}  # held-out: light filled square
SHORT = {"dev": "Development (14)", "heldout": "Held-out (2)", "sealed": "Sealed (10, opened once)"}
COMPNAME = {"semantic_tuple_exact": "exact tuple", "field": "concept", "period": "period", "dimensions": "dimensions", "unit": "unit", "quantity": "value"}

def register_faces():
    """matplotlib reads only the first face of a .ttc, so Helvetica Neue had no bold or italic and every
    fontweight="bold" fell back to the regular face.  Extract the two faces once into the temp directory."""
    import tempfile
    from matplotlib import font_manager
    src = Path("/System/Library/Fonts/HelveticaNeue.ttc")
    if not src.exists():
        return
    cache = Path(tempfile.gettempdir()) / "mpl_helveticaneue_faces"
    cache.mkdir(exist_ok=True)
    coll = None
    for idx, name in ((1, "HelveticaNeue-Bold.ttf"), (2, "HelveticaNeue-Italic.ttf")):
        f = cache / name
        if not f.exists():
            from fontTools.ttLib import TTCollection
            coll = coll or TTCollection(str(src))
            coll.fonts[idx].save(str(f))
        font_manager.fontManager.addfont(str(f))


register_faces()
plt.rcParams.update({
    "font.family": ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"], "font.size": 8, "axes.titlesize": 8.5, "axes.labelsize": 8,
    "xtick.labelsize": 7.5, "ytick.labelsize": 7.5, "legend.fontsize": 7.5, "axes.edgecolor": INK2, "axes.linewidth": 0.6,
    "xtick.color": INK2, "ytick.color": INK2, "axes.labelcolor": INK, "text.color": INK, "axes.spines.top": False, "axes.spines.right": False,
    "xtick.major.size": 2.5, "ytick.major.size": 2.5, "xtick.major.width": 0.5, "ytick.major.width": 0.5, "legend.frameon": False,
    "savefig.dpi": 300, "figure.dpi": 110, "pdf.fonttype": 42,
})
W1, W2 = 3.0, 5.15  # 0.58 and 1.0 of the sn-jnl text width (372 pt = 5.17 in)


def grid(ax, axis="y"):
    ax.grid(True, axis=axis, color=GRID, linewidth=0.5, zorder=0)
    ax.set_axisbelow(True)


NARROW = {"fig6_ceiling", "fig11_companies", "fig14_abstention"}  # placed at 0.58 of the text width (build_tex.py)


def save(fig, name):
    """Save so that the tight bounding box is exactly the print width (W1 or W2): LaTeX then places the figure at
    100% and the font sizes set here are the printed sizes.  The figure width is rescaled until the tight box fits."""
    target = (W1 if name in NARROW else W2) - 2 * 0.04
    for _ in range(15):
        fig.canvas.draw()
        w = fig.get_tightbbox(fig.canvas.get_renderer()).width
        if abs(w - target) < 0.01:
            break
        fw, fh = fig.get_size_inches()
        fig.set_size_inches(fw + 0.8 * (target - w), fh)
    else:
        print("warning: %s is %.2f in wide, target %.2f in" % (name, w + 0.08, target + 0.08))
    fig.savefig(OUT / f"{name}.png", bbox_inches="tight", pad_inches=0.04, facecolor="white")
    fig.savefig(OUT / f"{name}.pdf", bbox_inches="tight", pad_inches=0.04, facecolor="white")
    plt.close(fig)
    print("wrote", name)


# ---------------------------------------------------------------- data
def dev_refs():
    from native_v30_bank import DEST as BANK
    tasks = json.loads((BANK / "public/tasks.json").read_text())
    future = {t["task_id"]: t for t in tasks if t["source_year"] != 2023}
    refs = scoring_references(future)[0]
    return refs, {t: future[t]["company"] for t in refs}


def held_refs(root):
    tasks = json.loads((root / "bank/public/tasks.json").read_text())
    future = {t["task_id"]: t for t in tasks if t["source_year"] != 2023}
    allrefs = json.loads((root / "scoring_only/references.json").read_text())
    refs = {k: allrefs[k] for k in future}
    return refs, {t: future[t]["company"] for t in refs}


def pred_rows(split, root, refs, company, seed, budget, method="semantic_memory"):
    rows = {}
    for c in sorted(set(company.values())):
        f = (root / "predictions/hash_prefix" / c / method / str(seed) / f"{budget}.json") if split == "dev" else (root / "predictions" / c / method / str(seed) / f"{budget}.json")
        for q in json.loads(f.read_text())["predictions"]:
            if q["task_id"] in refs:
                rows[q["task_id"]] = q["predicted_binding"]
    missing = [t for t in refs if t not in rows]
    assert not missing, (split, root, seed, budget, len(missing))
    return rows


def llm_bindings(answers_dir, refs):
    out = {}
    for tid in refs:
        f = answers_dir / (tid + ".json")
        out[tid] = json.loads(f.read_text()).get("predicted_binding") if f.exists() else None
    return out


def correct(bindings, refs, strict=True):
    """Per-target component correctness.  strict: every target must have a binding (system predictions);
    otherwise a missing or unparsable answer counts as wrong (language-model answers)."""
    missing = [t for t in refs if not bindings.get(t)]
    assert not (strict and missing), "%d targets without a binding" % len(missing)
    empty = {c: False for c in COMPONENTS}
    return {t: (component_correct(bindings[t], refs[t]) if bindings.get(t) else dict(empty)) for t in refs}


SPLIT_DATA = {}


def load_all():
    refs, comp = dev_refs()
    SPLIT_DATA["dev"] = dict(refs=refs, company=comp, roots={"v34": R / "common_candidate_v34/learning_v1", "nollm": R / "common_candidate_v34_nollm/learning_v1"},
                             llm={"fewshot": DEV_LLM / "fewshot_history/deepseek_deepseek-flash/answers", "annotated": DEV_LLM / "fewshot_linkbase/deepseek_deepseek-flash/answers", "zeroshot": DEV_LLM / "vision/answers"})
    H = R / "heldout_v1"
    refs, comp = held_refs(H)
    SPLIT_DATA["heldout"] = dict(refs=refs, company=comp, roots={"v34": H / "evaluation_v4", "nollm": H / "evaluation_v5"},
                                 llm={"fewshot": H / "llm/fewshot_history/deepseek_deepseek-flash/answers", "annotated": H / "llm/fewshot_linkbase/deepseek_deepseek-flash/answers", "zeroshot": H / "llm/vision/answers"})
    S = R / "sealed_v1"
    refs, comp = held_refs(S)
    SPLIT_DATA["sealed"] = dict(refs=refs, company=comp, roots={"v34": S / "evaluation_v34", "nollm": S / "evaluation_v34_nollm"},
                                llm={"fewshot": S / "llm/fewshot_history/deepseek_deepseek-flash/answers", "annotated": S / "llm/fewshot_linkbase/deepseek_deepseek-flash/answers", "zeroshot": S / "llm/vision/answers"})
    for split, d in SPLIT_DATA.items():
        d["sys"] = {}
        for sysname, root in d["roots"].items():
            d["sys"][sysname] = {(s, b): correct(pred_rows(split, root, d["refs"], d["company"], s, b), d["refs"]) for s in SEEDS for b in BUDGETS}
        d["llmc"] = {k: correct(llm_bindings(p, d["refs"]), d["refs"], strict=False) for k, p in d["llm"].items()}
        d["llmb"] = {k: llm_bindings(p, d["refs"]) for k, p in d["llm"].items()}
        d["sysb"] = {sysname: {s: pred_rows(split, root, d["refs"], d["company"], s, 10) for s in SEEDS} for sysname, root in d["roots"].items()}
        print(split, len(d["refs"]), "targets", {k: round(100 * np.mean([v[t]["semantic_tuple_exact"] for t in d["refs"]]), 1) for k, v in d["llmc"].items()},
              {k: round(100 * np.mean([d["sys"][k][(s, 10)][t]["semantic_tuple_exact"] for s in SEEDS for t in d["refs"]]), 1) for k in d["sys"]})


def acc(rows_by_rep, refs, comp="semantic_tuple_exact", subset=None):
    ts = list(refs) if subset is None else subset
    return 100 * np.mean([float(rows[t][comp]) for rows in rows_by_rep for t in ts])


# ---------------------------------------------------------------- Fig 5: benchmark composition
def fig5():
    fig = plt.figure(figsize=(W2, 2.9))
    gs = fig.add_gridspec(1, 2, width_ratios=[1.0, 1.75], wspace=0.9)
    ax = fig.add_subplot(gs[0, 0])
    rows = [("Development\n14 companies, 42 filings", 280, 560), ("Held-out\n2 companies, 6 filings", 40, 160), ("Sealed, opened once\n10 companies, 30 filings", 200, 800), ("Sealed, unopened\n50 companies", 0, 0)]
    y = np.arange(len(rows))[::-1]
    t = [r[1] for r in rows]; e = [r[2] for r in rows]
    ax.barh(y, t, color=SEQ[3], height=0.62, label="2023 teacher cells (company history;\non development also fitting the priors)", zorder=3)
    ax.barh(y, e, left=t, color=BLUE, height=0.62, label="2024 and 2025 evaluation cells", zorder=3)
    for yi, (lab, a, b) in zip(y, rows):
        if a + b:
            ax.text(a + b + 15, yi, f"{a + b:,}", va="center", ha="left", fontsize=7.5, color=INK2)
        else:
            ax.text(30, yi, "not prepared", va="center", ha="left", fontsize=7, color=MUTED, style="italic")
    ax.set_yticks(y); ax.set_yticklabels([r[0] for r in rows], fontsize=7.5)
    ax.set_xlabel("cells"); ax.set_xlim(0, 1250); ax.set_xticks([0, 500, 1000]); ax.set_xticklabels(["0", "500", "1,000"]); grid(ax, "x")
    ax.legend(loc="upper center", bbox_to_anchor=(0.3, -0.16), ncol=1, handlelength=1.2, fontsize=6.4)
    ax.set_title("a  Splits", loc="left", fontweight="bold")

    ax = fig.add_subplot(gs[0, 1])
    cols = ["teach.", "eval."] * 3
    props = ["Explicitly dimensioned", "Extension concept", "Instant period", "Zero-valued fact", "Negative filed value", "Non-GBP unit"]
    M = np.array([[36, 40, 30, 29, 26, 25], [18, 14, 15, 20, 21, 16], [27, 32, 53, 42, 34, 33], [29, 21, 10, 10, 10, 9], [12, 19, 13, 19, 13, 18], [17, 16, 3, 1, 23, 22]], dtype=float)  # Table 2, recomputed from the references
    cmap = LinearSegmentedColormap.from_list("seq", [SEQ[0], SEQ[7]])
    ax.imshow(M, cmap=cmap, vmin=0, vmax=60, aspect="auto")
    for i in range(M.shape[0]):
        for j in range(M.shape[1]):
            ax.text(j, i, f"{M[i, j]:.0f}", ha="center", va="center", fontsize=7.5, color="white" if M[i, j] > 36 else INK)
    ax.set_xticks(range(len(cols))); ax.set_xticklabels(cols, fontsize=6.5)
    # split names under the column labels, each over its two columns (offsets in points below the heatmap)
    from matplotlib.transforms import offset_copy
    rule = offset_copy(ax.get_xaxis_transform(), fig=fig, y=-14, units="points")
    for j, g in enumerate(("Development", "Held-out", "Sealed")):
        ax.plot([2 * j - 0.42, 2 * j + 1.42], [0, 0], transform=rule, color=INK2, linewidth=0.5, clip_on=False)
        ax.annotate(g, xy=(2 * j + 0.5, 0), xycoords=("data", "axes fraction"), xytext=(0, -16.5), textcoords="offset points", ha="center", va="top", fontsize=6.3, color=INK2, fontweight="bold")
    ax.set_yticks(range(len(props))); ax.set_yticklabels(props, fontsize=7)
    ax.tick_params(length=0)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_title("b  Composition, per cent of cells", loc="left", fontweight="bold")
    save(fig, "fig3_benchmark")


# ---------------------------------------------------------------- Fig 6: the inventory ceiling
def fig6():
    steps = ["v23", "v26", "v27", "v28", "v29", "v30", "v31", "v32", "v33", "v34"]
    reach = [53.2, 87.5, 87.5, 87.5, 87.5, 94.3, 94.3, 94.3, 94.3, 94.3]
    exact = [14.6, 16.8, 28.2, 29.2, 32.0, 33.2, 40.0, 44.3, 44.5, 53.2]
    x = np.arange(len(steps))
    fig, ax = plt.subplots(figsize=(W1, 2.3))
    ax.fill_between(x, 0, reach, step=None, color=SEQ[1], zorder=1, linewidth=0)
    ax.plot(x, reach, color=SEQ[6], linewidth=1.4, zorder=2)
    ax.fill_between(x, 0, exact, color=BLUE, alpha=0.9, zorder=3, linewidth=0)
    ax.plot(x, exact, color=SEQ[11], linewidth=1.4, zorder=4)
    ax.set_xticks(x); ax.set_xticklabels(steps, fontsize=7)
    ax.set_ylim(0, 100); ax.set_ylabel("% of 560 development cells"); grid(ax, "y")
    ax.text(2.3, 75, "reference reachable\nin the candidate inventory", color=SEQ[11], fontsize=7.5, va="center", ha="left")
    ax.text(6.0, 18, "exact tuple\nselected", color="white", fontsize=7.5, va="center", ha="center", fontweight="bold")
    for xi, lab in ((1, "inventory\nrepair I"), (5, "perception fix\n+ repair II")):
        ax.annotate(lab, xy=(xi, reach[xi]), xytext=(xi, 104), fontsize=6.5, color=INK2, ha="center", va="bottom",
                    arrowprops=dict(arrowstyle="-", color=INK2, linewidth=0.5), annotation_clip=False)
    for xi, lab in ((2, "structural\nprior"), (6, "model\nreadings"), (9, "linkbases")):
        ax.annotate(lab, xy=(xi, exact[xi]), xytext=(xi + (0.0 if xi < 9 else -0.9), exact[xi] + (14 if xi in (2, 6) else 22)), fontsize=6.5, color=SEQ[11], ha="center",
                    arrowprops=dict(arrowstyle="-", color=SEQ[11], linewidth=0.5))
    ax.set_ylim(0, 112); ax.set_yticks([0, 25, 50, 75, 100])
    ax.spines["left"].set_bounds(0, 100)
    save(fig, "fig6_ceiling")


# ---------------------------------------------------------------- Fig 7: where accuracy comes from (ladder)
def fig7():
    steps = ["none", "+ structural prior", "+ concept \u00d7 unit/value", "+ concept disambiguation", "+ LLM concept ranking", "+ LLM period/dims choices", "+ few-shot reading (v33)", "+ linkbases (v34)"]
    exact = [17.0, 29.0, 29.6, 33.2, 40.0, 44.3, 44.5, 53.2]
    delta = [None, (12.0, 9.1, 15.0), (1.5, 0.6, 2.4), (2.2, 0.5, 3.8), (7.4, 5.2, 9.8), (4.9, 2.6, 6.8), (0.4, -1.7, 2.2), (8.3, 5.8, 11.7)]
    comp = np.array([[np.nan] * 3, [17.9, 11.1, 1.4], [0.9, 0.2, 0.1], [0.7, -0.2, 4.9], [0.7, 0.5, 11.9], [7.6, 2.3, 0.6], [1.9, -0.7, 0.3], [-1.2, 0.2, 14.7]])
    fig = plt.figure(figsize=(W2, 2.7))
    gs = fig.add_gridspec(1, 2, width_ratios=[2.3, 1.15], wspace=0.2)
    ax = fig.add_subplot(gs[0, 0])
    y = np.arange(len(steps))[::-1]
    colors = [GRAY] + [BLUE] * 3 + [YELLOW] * 3 + [BLUE]
    ax.barh(y, exact, color=colors, height=0.6, zorder=3)
    for yi, v, d in zip(y, exact, delta):
        ax.text(v + 0.8, yi, f"{v:.1f}", va="center", fontsize=7.5, color=INK)
        if d:
            ax.annotate(f"{d[0]:+.1f}  [{d[1]:+.1f}, {d[2]:+.1f}]".replace("-", "\u2212"), (v, yi), xytext=(20, 0), textcoords="offset points", va="center", fontsize=7, color=INK2)
    ax.set_yticks(y); ax.set_yticklabels(steps, fontsize=7.5)
    ax.set_xlim(0, 100); ax.set_xticks(range(0, 61, 10)); ax.set_xlabel("exact-tuple accuracy, development, budget 10 (%)\npaired Δ to the row above, pooled over policies, budgets and seeds [95% interval, 14 company clusters]", fontsize=7); grid(ax, "x")
    fig.legend(handles=[patches.Patch(color=BLUE, label="taxonomy and page structure"), patches.Patch(color=YELLOW, label="language-model readings")], loc="upper center", bbox_to_anchor=(0.45, -0.07), ncol=2, handlelength=1.0, fontsize=6.6)
    ax.spines["bottom"].set_bounds(0, 60); ax.set_ylim(-0.5, len(steps) - 0.5)
    ax.set_title("a  Priors added one at a time", loc="left", fontweight="bold")

    ax = fig.add_subplot(gs[0, 1])
    cmap = LinearSegmentedColormap.from_list("div", ["#c2410c", "#f4f4f2", BLUE])
    norm = TwoSlopeNorm(vmin=-5, vcenter=0, vmax=18)
    M = comp  # row i of (b) is row i of (a): "none" at the top, v34 at the bottom
    ax.imshow(np.where(np.isnan(M), 0, M), cmap=cmap, norm=norm, aspect="auto")
    for i in range(M.shape[0]):
        for j in range(M.shape[1]):
            if np.isnan(M[i, j]):
                ax.text(j, i, "\u2014", ha="center", va="center", fontsize=7, color=MUTED)
            else:
                ax.text(j, i, f"{M[i, j]:+.1f}".replace("-", "\u2212"), ha="center", va="center", fontsize=6.6, color="white" if abs(M[i, j]) > 13 else INK)
    ax.set_xticks(range(3)); ax.set_xticklabels(["period", "dims.", "concept"], fontsize=6.6)
    ax.set_yticks([]); ax.tick_params(length=0)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_title("b  Δ per component (points)", loc="left", fontweight="bold")
    save(fig, "fig7_ladder")


# ---------------------------------------------------------------- Fig 8: complementarity (concept vs period)
def fig8():
    # v30 and v33 are not loaded by load_all(): their (concept, period) at budget 10 are copied from Table 4 (development,
    # hash-prefix policy, seed mean) and Table 7 (held-out).  Every other system and control point is computed from the
    # frozen predictions and answers and checked against the values printed in Tables 4, 6, 7 and 8.
    pts = {"dev": {"none (v30)": (57.9, 77.4), "readings only (v33)": (70.8, 86.0)}, "heldout": {"readings only (v33)": (73.3, 84.6)}, "sealed": {}}
    printed = {"dev": {"nollm": (80.7, 79.8), "v34": (84.6, 85.5), "fewshot": (64.8, 84.1), "annotated": (72.7, 83.6)},
               "heldout": {"nollm": (81.2, 64.4), "v34": (86.9, 84.2), "fewshot": (71.9, 86.2), "annotated": (80.0, 85.6)},
               "sealed": {"nollm": (74.0, 68.3), "v34": (81.4, 83.5), "fewshot": (64.9, 86.4), "annotated": (73.9, 86.8)}}
    keyname = {"nollm": "linkbases only (no-LLM system)", "v34": "typed system (v34)", "fewshot": "LLM few-shot (ten history cells)", "annotated": "LLM few-shot + annotation"}
    for split, d in SPLIT_DATA.items():
        for k in ("nollm", "v34", "fewshot", "annotated"):
            reps = [d["sys"][k][(s, 10)] for s in SEEDS] if k in d["sys"] else [d["llmc"][k]]
            xy = (acc(reps, d["refs"], "field"), acc(reps, d["refs"], "period"))
            assert all(abs(a - b) < 0.051 for a, b in zip(xy, printed[split][k])), (split, k, xy, printed[split][k])
            pts[split][keyname[k]] = xy
    # commercial models, zero-shot on the page crop (Tables 6 and 7); not run on the sealed companies
    commercial = {"dev": {"Claude Sonnet 5.5": (71.2, 95.7), "Gemini 3.1 Pro": (67.9, 89.5), "GPT-5.4": (61.3, 82.7)}, "heldout": {"Claude Sonnet 5.5": (79.4, 95.0), "Gemini 3.1 Pro": (77.5, 85.6)}, "sealed": {}}
    style = {"none (v30)": (GRAY, "o", True), "linkbases only (no-LLM system)": (AQUA, "o", True), "readings only (v33)": (YELLOW, "o", True), "typed system (v34)": (BLUE, "o", True),
             "LLM few-shot (ten history cells)": (ORANGE, "s", False), "LLM few-shot + annotation": (VIOLET, "s", False)}
    fig, axes = plt.subplots(1, 3, figsize=(W2, 2.5), sharex=True, sharey=True)
    for ax, (split, title) in zip(axes, SPLITS):
        title = SHORT[split]
        grid(ax, "both")
        d = pts[split]
        # arrows from "none"/linkbases to both
        if "none (v30)" in d:
            for a, b in (("none (v30)", "linkbases only (no-LLM system)"), ("none (v30)", "readings only (v33)"), ("linkbases only (no-LLM system)", "typed system (v34)"), ("readings only (v33)", "typed system (v34)")):
                ax.annotate("", xy=d[b], xytext=d[a], arrowprops=dict(arrowstyle="-|>", color=MUTED, linewidth=0.7, shrinkA=4, shrinkB=4, mutation_scale=7))
        else:
            for a in ("linkbases only (no-LLM system)", "readings only (v33)"):
                if a in d:  # the readings-only arrow bows downwards so that it passes under the control and Gemini markers
                    cs = "arc3,rad=0.25" if a == "readings only (v33)" else "arc3,rad=0"
                    ax.annotate("", xy=d["typed system (v34)"], xytext=d[a], arrowprops=dict(arrowstyle="-|>", color=MUTED, linewidth=0.7, shrinkA=4, shrinkB=4, mutation_scale=7, connectionstyle=cs))
        for name, (x, y) in d.items():
            c, m, filled = style[name]
            ax.scatter([x], [y], s=20, marker=m, color=c if filled else "white", edgecolor=c, linewidth=1.0, zorder=5 if filled else 4)
        for name, (x, y) in commercial[split].items():
            ax.scatter([x], [y], s=20, marker="^", color="white", edgecolor=INK2, linewidth=0.9, zorder=6)
            off, ha, va = {("heldout", "Gemini 3.1 Pro"): ((-30, 16), "center", "bottom"), ("dev", "GPT-5.4"): ((-5, 0), "right", "center"), ("dev", "Claude Sonnet 5.5"): ((0, 5), "center", "bottom"), ("dev", "Gemini 3.1 Pro"): ((5, 0), "left", "center")}.get((split, name), ((-5, 0), "right", "center"))
            leader = dict(arrowstyle="-", color=INK2, linewidth=0.5, shrinkA=0, shrinkB=3) if (split, name) == ("heldout", "Gemini 3.1 Pro") else None
            ax.annotate(name, (x, y), xytext=off, textcoords="offset points", fontsize=6.5, color=INK2, ha=ha, va=va, arrowprops=leader)
        ax.set_title(title, loc="left", fontsize=7.5)
        ax.set_xlim(49, 91); ax.set_xticks([60, 70, 80, 90]); ax.set_ylim(58, 99)
    axes[0].set_ylabel("period accuracy (%)"); fig.text(0.5, -0.02, "concept accuracy (%)", ha="center", fontsize=8)
    short = {"none (v30)": "none (v30)", "linkbases only (no-LLM system)": "linkbases only (no-LLM system)", "readings only (v33)": "readings only (v33)", "typed system (v34)": "both (v34)",
             "LLM few-shot (ten history cells)": "history-matched control", "LLM few-shot + annotation": "annotated control"}
    handles = [plt.Line2D([], [], marker=m, color=c if f else "white", markeredgecolor=c, markeredgewidth=1.2, linestyle="", markersize=6, label=short[n]) for n, (c, m, f) in style.items()]
    handles.append(plt.Line2D([], [], marker="^", color="white", markeredgecolor=INK2, markeredgewidth=1.0, linestyle="", markersize=6, label="commercial, zero-shot"))
    fig.legend(handles=handles, loc="lower center", bbox_to_anchor=(0.5, -0.27), ncol=3, handletextpad=0.3, columnspacing=0.8)
    fig.subplots_adjust(wspace=0.12)
    save(fig, "fig9_complementarity")


# ---------------------------------------------------------------- Fig 9: forest plot system minus model
def fig9():
    files = {
        "dev": {"fewshot": R / "common_candidate_v34/learning_v1/paired_vs_fewshot_llm.json", "annotated": R / "common_candidate_v34/learning_v1/paired_vs_fewshot_linkbase_llm.json"},
        "heldout": {"fewshot": R / "heldout_v1/evaluation_v4/paired_vs_fewshot_llm_budget10.json", "annotated": R / "heldout_v1/evaluation_v4/paired_vs_fewshot_linkbase_llm_budget10.json"},
        "sealed": {"fewshot": R / "sealed_v1/evaluation_v34/paired_vs_fewshot_llm_budget10.json", "annotated": R / "sealed_v1/evaluation_v34/paired_vs_fewshot_linkbase_llm_budget10.json"},
    }
    fig, axes = plt.subplots(1, 3, figsize=(W2, 2.6), sharey=True)
    comps = list(COMPONENTS)
    y0 = np.arange(len(comps))[::-1]
    for ax, (split, title) in zip(axes, SPLITS):
        grid(ax, "x"); ax.axvline(0, color=INK2, linewidth=0.6, zorder=2)
        for k, (base, off) in enumerate((("fewshot", 0.17), ("annotated", -0.17))):
            rep = json.loads(files[split][base].read_text())["methods"]["semantic_memory"]["pooled"]
            for c, yi in zip(comps, y0):
                e = rep[c]; d = 100 * e["difference"]; key = {"dev": "ci95", "heldout": "ci95", "sealed": "ci95_company_cluster"}[split]; assert key in e, (split, c); ci = e[key]; lo, hi = 100 * ci[0], 100 * ci[1]
                col = SYS[base]
                ax.plot([lo, hi], [yi + off, yi + off], color=col, linewidth=1.3, solid_capstyle="round", zorder=3)
                ax.scatter([d], [yi + off], s=22 if c == "semantic_tuple_exact" else 14, color=col, zorder=4, marker="D" if c == "semantic_tuple_exact" else "o")
        ax.set_yticks(y0); ax.set_yticklabels([COMPNAME[c] for c in comps])
        ax.set_title(SHORT[split] + ("\npooled over policies,\nbudgets and seeds" if split == "dev" else "\nbudget 10\n"), loc="left", fontsize=7, linespacing=1.15)
        ax.set_xlim(-22, 34); ax.set_xticks([-15, 0, 15, 30])
        ax.axhspan(y0[0] - 0.5, y0[0] + 0.5, color="#f4f4f2", zorder=0)
    axes[1].set_xlabel("← model higher          system higher →\ntyped system (v34) minus language model, percentage points, 95% interval")
    handles = [plt.Line2D([], [], marker="o", color=SYS["fewshot"], linestyle="-", markersize=4, label="vs history-matched control"),
               plt.Line2D([], [], marker="o", color=SYS["annotated"], linestyle="-", markersize=4, label="vs annotated control")]
    fig.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, 1.12), ncol=2)
    fig.subplots_adjust(wspace=0.1)
    save(fig, "fig10_forest")


# ---------------------------------------------------------------- Fig 10: per-company scatter
def fig10():
    fig, ax = plt.subplots(figsize=(W1, 3.2))
    grid(ax, "both")
    ax.plot([0, 100], [0, 100], color=INK2, linewidth=0.6, linestyle=(0, (3, 2)), zorder=2)
    pts = []
    for split, title in SPLITS:
        d = SPLIT_DATA[split]
        for c in sorted(set(d["company"].values())):
            ts = [t for t in d["refs"] if d["company"][t] == c]
            xs = 100 * np.mean([d["llmc"]["fewshot"][t]["semantic_tuple_exact"] for t in ts])
            ys = 100 * np.mean([d["sys"]["v34"][(s, 10)][t]["semantic_tuple_exact"] for s in SEEDS for t in ts])
            pts.append((split, c, xs, ys))
            ax.scatter([xs], [ys], marker=MARK[split], s=24 if split == "heldout" else 20, color=FACE[split], edgecolor=BLUE if split == "heldout" else "white", linewidth=1.0 if split == "heldout" else 0.6, zorder=7 if split == "heldout" else 5)
    for split, c, xs, ys in pts:
        lab = {"213800I7RWQ3FV72EZ26": "XP Power", "213800VAYO5KCAXZHK83": "Moonpig", "213800PFUQT4HHFYZJ63": "Wilmington", "213800F59868OZQU6E56": "Bay Capital", "0TMBS544NMO7GLCE7H90": "Aberdeen"}.get(c)
        if lab:
            off = {"Aberdeen": (6, -9), "Bay Capital": (6, 4), "Moonpig": (22, -26), "Wilmington": (6, -8), "XP Power": (0, -6)}[lab]
            lead = dict(arrowstyle="-", color=INK2, linewidth=0.5, shrinkA=0, shrinkB=3) if lab == "Moonpig" else None
            ax.annotate(lab, (xs, ys), xytext=off, textcoords="offset points", fontsize=6.5, color=INK2, ha="center" if lab == "XP Power" else "left", va="center" if lab == "Moonpig" else ("top" if lab == "XP Power" else "baseline"), arrowprops=lead)
    ax.set_xlim(0, 100); ax.set_ylim(0, 100); ax.set_aspect("equal")
    ax.set_xlabel("history-matched control, exact tuple (%)"); ax.set_ylabel("typed system (v34), exact tuple (%)")
    ax.text(90, 70, "model\nhigher", fontsize=7, color=MUTED, ha="center"); ax.text(12, 70, "system\nhigher", fontsize=7, color=MUTED, ha="center")
    handles = [plt.Line2D([], [], marker=MARK[s], color=FACE[s], markeredgecolor=BLUE, linestyle="", markersize=5, label=SHORT[s]) for s, t in SPLITS]
    ax.legend(handles=handles, loc="lower right", fontsize=6.6, handletextpad=0.3, borderaxespad=0.3)
    n_ahead = sum(1 for p in pts if p[3] > p[2]); ax.set_title(f"{n_ahead} of {len(pts)} companies above the diagonal (budget 10)", loc="left", fontsize=7.5)
    save(fig, "fig11_companies")


# ---------------------------------------------------------------- Fig 11: budget curves
def fig11():
    fig, axes = plt.subplots(1, 3, figsize=(W2, 2.4), sharey=True)
    names = {"zeroshot": "LLM zero-shot", "fewshot": "history-matched\ncontrol", "annotated": "annotated\ncontrol", "v34": "v34", "nollm": "no-LLM\nsystem"}
    for ax, (split, title) in zip(axes, SPLITS):
        d = SPLIT_DATA[split]; labels = []
        for gy in (20, 30, 40, 50, 60, 70):
            ax.hlines(gy, -1.5, 10.6, color=GRID, linewidth=0.5, zorder=0)
        for base in ("zeroshot", "fewshot", "annotated"):
            v = acc([d["llmc"][base]], d["refs"])
            ax.hlines(v, -1.5, 10.6, color=SYS[base], linewidth=1.0, linestyle=(0, (4, 2)), zorder=2)
            labels.append([v, base])
        for sysname in ("v34", "nollm"):
            ys = [acc([d["sys"][sysname][(s, b)] for s in SEEDS], d["refs"]) for b in BUDGETS]
            ax.plot(BUDGETS, ys, color=SYS[sysname], linewidth=1.6, marker="o", markersize=4.5, markeredgecolor="white", markeredgewidth=0.8, zorder=5)
            labels.append([ys[-1], sysname])
        # stack the right-hand labels so that none overlaps (heights in data units of the 20-70 axis)
        labels.sort(key=lambda e: e[0]); height = lambda k: 5.6 if "\n" in names[k] else 3.0
        ys_ = [v for v, _ in labels]; hs = [height(k) for _, k in labels]
        for _ in range(200):  # move overlapping neighbours apart symmetrically, so labels stay next to their lines
            moved = False
            for i in range(len(ys_) - 1):
                gap = (hs[i] + hs[i + 1]) / 2 - (ys_[i + 1] - ys_[i])
                if gap > 1e-6:
                    ys_[i] -= gap / 2; ys_[i + 1] += gap / 2; moved = True
            if not moved:
                break
        pos = list(zip(ys_, hs))
        for (v, k), (yp, _) in zip(labels, pos):
            ax.text(11.4, yp, names[k], fontsize=6.3, color=SYS[k], va="center", ha="left", linespacing=1.0, fontweight="bold" if k in ("v34", "nollm") else "normal")
        ax.set_xticks(BUDGETS); ax.set_xlim(-1.5, 21.5); ax.set_title(SHORT[split], loc="left", fontsize=7.5)
        ax.spines["bottom"].set_bounds(0, 10)
    axes[0].set_ylim(20, 70); axes[0].set_ylabel("exact tuple (%)"); axes[1].set_xlabel("company's own 2023 cells given to the composer (budget)")
    fig.text(0.5, -0.06, "solid: systems (seed means); dashed: deepseek-flash baselines (single runs)", ha="center", va="top", fontsize=6.6, color=INK2)
    fig.subplots_adjust(wspace=0.08)
    save(fig, "fig12_budget")


# ---------------------------------------------------------------- Fig 12: error anatomy
def first_failure(r):
    if r["semantic_tuple_exact"]:
        return "correct"
    for c, lab in (("field", "concept"), ("period", "period"), ("dimensions", "dimensions"), ("unit", "unit"), ("quantity", "value")):
        if not r[c]:
            return lab
    return "entity"


def fig12():
    cats = ["correct", "concept", "period", "dimensions", "unit", "value"]
    cols = {"correct": BLUE, "concept": "#f9c9b5", "period": "#f39a73", "dimensions": "#eb6834", "unit": "#8a8a86", "value": "#7a2f13"}
    fig, ax = plt.subplots(figsize=(W2, 2.9))
    labels, bars = [], []
    for split, title in SPLITS:
        d = SPLIT_DATA[split]
        for sysname, lab in (("v34", "typed system (v34)"), ("fewshot", "history-matched control"), ("annotated", "annotated control")):
            if sysname == "v34":
                reps = [d["sys"]["v34"][(s, 10)] for s in SEEDS]
            else:
                reps = [d["llmc"][sysname]]
            cnt = {c: 0.0 for c in cats + ["entity"]}
            for rows in reps:
                for t in d["refs"]:
                    cnt[first_failure(rows[t])] += 1.0 / len(reps)
            assert cnt["entity"] == 0, (split, sysname, cnt["entity"])  # the entity is supplied to every method
            n = len(d["refs"]); bars.append([100 * cnt[c] / n for c in cats]); labels.append((split, lab))
    y = np.array([11.0 - (i + 1.1 * (i // 3)) for i in range(len(bars))])
    left = np.zeros(len(bars))
    for j, c in enumerate(cats):
        vals = np.array([b[j] for b in bars])
        ax.barh(y, vals, left=left, color=cols[c], height=0.64, zorder=3, edgecolor="white", linewidth=0.8)
        for yi, l, v in zip(y, left, vals):
            if v >= 6:
                ax.text(l + v / 2, yi, f"{v:.0f}", ha="center", va="center", fontsize=6.8, color="white" if c in ("correct", "dimensions", "unit", "value") else INK)
        left += vals
    ax.set_yticks(y); ax.set_yticklabels([l for _, l in labels], fontsize=7.2)
    ax.set_xlim(0, 100); ax.set_xlabel("% of evaluation cells, by the first component that fails\n(in the order concept, period, dimensions, unit, value)"); grid(ax, "x")
    for k, (split, title) in enumerate(SPLITS):
        ax.text(1.0, y[3 * k] + 0.72, title, fontsize=7, color=INK2, ha="left", fontweight="bold", zorder=6, bbox=dict(facecolor="white", edgecolor="none", pad=0.6))
    ax.set_ylim(y[-1] - 0.6, y[0] + 1.2)
    handles = [patches.Patch(color=cols[c], label=c if c != "correct" else "exact tuple correct") for c in cats]
    ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.24), ncol=3, handlelength=1.0, columnspacing=1.2)
    save(fig, "fig13_errors")


# ---------------------------------------------------------------- Fig 13: abstention by agreement
def whole_agreement(s, l):
    """System and model bindings agree on every open component (same comparison as the scorer)."""
    if not s or not l or any(l.get(k) is None for k in ("field", "period", "dimensions", "unit", "quantity")):
        return False
    return component_correct(s, {**l, "entity": s.get("entity")})["semantic_tuple_exact"]


def fig13():
    fig, ax = plt.subplots(figsize=(W1, 2.6))
    grid(ax, "both")
    rules = [("all cells", lambda s, l: True), ("agree on concept", lambda s, l: s and l and s["field"] == l["field"]),
             ("agree on period", lambda s, l: s and l and s["period"] == l["period"]),
             ("agree on concept and period", lambda s, l: s and l and s["field"] == l["field"] and s["period"] == l["period"]),
             ("agree on the whole tuple", lambda s, l: whole_agreement(s, l))]
    for split, title in SPLITS:
        d = SPLIT_DATA[split]
        xs, ys = [], []
        for name, rule in rules:
            nkept, ncorrect = 0, 0
            for s in SEEDS:
                sysb = d["sysb"]["v34"][s]; kept = [t for t in d["refs"] if rule(sysb.get(t), d["llmb"]["fewshot"].get(t))]
                nkept += len(kept); ncorrect += sum(int(d["sys"]["v34"][(s, 10)][t]["semantic_tuple_exact"]) for t in kept)
            xs.append(100 * nkept / (len(SEEDS) * len(d["refs"]))); ys.append(100 * ncorrect / nkept if nkept else float("nan"))
        order = np.argsort(xs); ax.plot(np.array(xs)[order], np.array(ys)[order], color=BLUE, linewidth=1.0, zorder=3, alpha=0.6)
        ax.scatter(xs, ys, marker=MARK[split], s=24, color=FACE[split], edgecolor=BLUE, linewidth=1.0, zorder=5)
        if split == "sealed":
            for k, ((name, _), x, y) in enumerate(zip(rules, xs, ys)):
                ax.annotate(str(k + 1), (x, y), xytext=(0, -11), textcoords="offset points", fontsize=7, color=INK, ha="center", fontweight="bold")
    ax.set_xlabel("coverage: cells kept (%)"); ax.set_ylabel("precision: exact tuple among kept (%)")
    handles = [plt.Line2D([], [], marker=MARK[s], color=FACE[s], markeredgecolor=BLUE, linestyle="", markersize=5, label=t.split(" (")[0]) for s, t in SPLITS]
    ax.legend(handles=handles, loc="upper right", fontsize=6.8)
    ax.set_ylim(40, 92)
    ax.set_xlim(25, 103)
    save(fig, "fig14_abstention")




# ---------------------------------------------------------------- Fig 3: lean pipeline schematic
def fig3():
    from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
    fig, ax = plt.subplots(figsize=(W2, 2.45)); ax.set_xlim(0, 100); ax.set_ylim(-1.6, 33.4); ax.axis("off"); fig.subplots_adjust(left=0.005, right=0.995, top=0.995, bottom=0.005)

    def box(x, y, w, h, title, lines=(), fill="#f4f6fb", edge=SEQ[7], dashed=False, title_color=INK):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=1.2", linewidth=0.9, edgecolor=edge, facecolor=fill, linestyle=(0, (3, 2)) if dashed else "-", zorder=2))
        ax.text(x + w / 2, y + h - 2.6, title, ha="center", va="center", fontsize=7.2, fontweight="bold", color=title_color, zorder=3)
        for i, l in enumerate(lines):
            ax.text(x + w / 2, y + h - 6.0 - 3.0 * i, l, ha="center", va="center", fontsize=6.6, color=INK2, zorder=3)

    def chip(x, y, w, text, color):
        ax.add_patch(FancyBboxPatch((x, y), w, 3.2, boxstyle="round,pad=0,rounding_size=1.0", linewidth=0, facecolor=color, zorder=3))
        ax.text(x + w / 2, y + 1.6, text, ha="center", va="center", fontsize=6.1, color="white", zorder=4)

    def arrow(x0, x1, y, dashed=False):
        ax.add_patch(FancyArrowPatch((x0, y), (x1, y), arrowstyle="-|>", mutation_scale=9, linewidth=1.0, color=INK2, linestyle=(0, (3, 2)) if dashed else "-", zorder=5))

    # 1 page
    box(1, 7, 17, 26, "Rendered page", ("one numeric cell", "marked by its box,", "its OCR words", "and word features"))
    # 2 inventory
    box(23, 7, 21, 26, "Candidate inventory", ("built without reading", "any filed fact"))
    for i, t in enumerate(("concepts", "periods", "dimensions", "unit \u00d7 value")):
        chip(25.5, 18.8 - 3.5 * i, 16, t, SEQ[8])
    # 3 priors
    box(49, 7, 22, 26, "Typed priors", ("weights fitted on 280", "teacher cells"))
    for i, (t, c) in enumerate((("page structure", BLUE), ("taxonomy declarations", BLUE), ("linkbases", BLUE), ("model readings", YELLOW))):
        chip(50.5, 18.8 - 3.5 * i, 19, t, c)
    # 4 composer and its output
    box(76, 19.5, 23, 13.5, "Witness composer", ("scores the tuple whose", "page witnesses agree", "with the priors"))
    box(76, 7, 23, 10.5, "Predicted tuple", ("concept \u00b7 period", "dimensions \u00b7 unit \u00b7 value"), fill="#e9f5ee", edge=GREEN, title_color=GREEN)
    # scorer under the output: the only place that reads the filed facts
    box(75, -1.4, 25, 5.8, "", (), fill="white", edge=INK2, dashed=True)
    ax.text(87.5, 1.5, "scorer: exact match against\nthe filed fact (read only here)", ha="center", va="center", fontsize=6.1, color=INK2, zorder=3, linespacing=1.1)
    for x0, x1 in ((18, 23), (44, 49), (71, 76)):
        arrow(x0, x1, 25.0)
    for y0, y1 in ((19.5, 17.6), (7, 4.5)):
        ax.add_patch(FancyArrowPatch((87.5, y0), (87.5, y1), arrowstyle="-|>", mutation_scale=9, linewidth=1.0, color=INK2, zorder=5))
    # kept left of the scorer box (x >= 75): the language-model baselines choose from the same inventory, but their
    # prompts list only its top 40 concept and 25 dimension candidates and carry no word features (Section 5.3)
    ax.text(1, 3.9, "Every method selects from this inventory; the language-model baselines see only\nits top 40 concepts and 25 dimension tuples, and no word features.", ha="left", va="center", fontsize=6.4, color=INK2, linespacing=1.15)
    ax.add_patch(FancyBboxPatch((1, -1.2), 2.6, 1.8, boxstyle="round,pad=0,rounding_size=0.5", linewidth=0, facecolor=YELLOW, zorder=3))
    ax.text(4.4, -0.3, "language-model readings (optional; absent in the no-LLM system)", ha="left", va="center", fontsize=6.4, color=INK2)
    save(fig, "fig4_pipeline")


if __name__ == "__main__":
    which = sys.argv[1:] or ["3", "5", "6", "7", "8", "9", "10", "11", "12", "13"]
    if any(w in ("8", "10", "11", "12", "13") for w in which):
        load_all()
    for w in which:
        globals()["fig" + w]()
