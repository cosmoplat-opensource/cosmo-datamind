#!/usr/bin/env python3
"""Figures of the PVLDB paper, drawn from the result files (no number is typed in by hand except diagram labels).
Output: tex/figures/*.pdf (vector). Palette: fixed categorical order (blue, orange, aqua, yellow) for documentation,
EvoOntology, DataMind, compiled; neutral gray for the no-ontology baseline; validated with the dataviz validator.
usage: python3 make_figures.py
"""
import glob
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Rectangle  # noqa: E402

HERE = Path(__file__).resolve().parent
EXP = HERE.parent
V4 = EXP / "runs/v4"
OUT = HERE / "tex/figures"
OUT.mkdir(parents=True, exist_ok=True)

INK, INK2, MUTED, GRID, AXIS, SURF = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7", "#ffffff"
C = {"none": "#898781", "doc": "#2a78d6", "evo": "#eb6834", "dm": "#1baf7a", "M": "#eda100"}
NAME = {"none": "No ontology", "doc": "Documentation", "evo": "EvoOntology", "dm": "DataMind", "M": "Compiled + check"}
SET = {"R": "Readable", "O": "Opaque", "MG": "Merged (60 tables)"}
COL, TXT = 3.33, 7.0            # column and text width in inches (acmart sigconf)

plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 8, "axes.labelsize": 8.5, "axes.titlesize": 9,
                     "xtick.labelsize": 8, "ytick.labelsize": 8, "legend.fontsize": 8, "axes.edgecolor": AXIS,
                     "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2, "axes.linewidth": 0.8,
                     "pdf.fonttype": 42, "savefig.bbox": "tight", "savefig.pad_inches": 0.02})


def load(name):
    return json.loads((V4 / name).read_text())


def clean(ax, grid="y"):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    if grid:
        ax.grid(axis=grid, color=GRID, linewidth=0.6)
        ax.set_axisbelow(True)
    ax.tick_params(length=2.5, width=0.6)


def save(fig, name, exact=False):
    """exact=True keeps the figure at its set size (single-column figures, so that 8 pt text prints at 8 pt);
    otherwise the bounding box is cropped tight."""
    from matplotlib.transforms import Bbox
    kw = {"bbox_inches": Bbox.from_bounds(0, 0, *fig.get_size_inches())} if exact else {}   # None would fall back to rc "tight"
    fig.savefig(OUT / f"{name}.pdf", **kw)
    fig.savefig(OUT / f"{name}.png", dpi=200, **kw)
    plt.close(fig)


# --------------------------------------------------------------------------- teaser
def box(ax, x, y, w, h, text, fc="#f4f4f1", ec=AXIS, color=INK, size=7.5, weight="normal"):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.12", fc=fc, ec=ec, lw=0.9))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", color=color, fontsize=size, weight=weight,
            linespacing=1.15)


def arrow(ax, p, q, color=MUTED, lw=1.0, style="-|>", rad=0.0):
    ax.add_patch(FancyArrowPatch(p, q, arrowstyle=style, mutation_scale=8, color=color, lw=lw,
                                 connectionstyle=f"arc3,rad={rad}", shrinkA=1, shrinkB=1))


def badge(ax, x, y, n):
    ax.scatter([x], [y], s=78, color="#e34948", edgecolors="none", zorder=5)      # marker: round at any aspect
    ax.text(x, y, str(n), ha="center", va="center", color="white", fontsize=7.2, weight="bold", zorder=6)


def teaser():
    st = load("stats_v4.json")
    fig = plt.figure(figsize=(TXT, 2.15))
    a = fig.add_axes([0.0, 0.02, 0.56, 0.96])
    a.set_xlim(-0.05, 10); a.set_ylim(0, 4.6); a.axis("off")
    # inputs -> two builders -> same agent (+ optional side information) -> one scorer
    box(a, 0.1, 1.7, 1.72, 1.2, "Database +\nconstruction\nquestions", fc="#ffffff", size=7.3)
    box(a, 2.15, 2.75, 1.95, 0.85, "EvoOntology\nbuilder", fc="#fde7dd", ec=C["evo"])
    box(a, 2.15, 1.0, 1.95, 0.85, "DataMind\nbuilder", fc="#dcf3ea", ec=C["dm"])
    arrow(a, (1.86, 2.45), (2.13, 3.1)); arrow(a, (1.86, 2.15), (2.13, 1.45))
    box(a, 4.75, 1.75, 2.25, 1.1, "Same\nReAct agent", fc="#ffffff", weight="bold")
    arrow(a, (4.12, 3.15), (4.73, 2.55)); arrow(a, (4.12, 1.42), (4.73, 2.05))
    box(a, 4.75, 3.5, 2.25, 0.7, "Docs / exemplars", fc="#ffffff", size=7)
    arrow(a, (5.875, 3.48), (5.875, 2.87))
    badge(a, 7.0, 4.2, 3)                                   # on the corner of the side-information box
    box(a, 7.75, 1.75, 1.5, 1.1, "One\nscorer", fc="#ffffff")
    arrow(a, (7.02, 2.3), (7.73, 2.3))
    badge(a, 9.25, 2.85, 1); badge(a, 9.25, 1.75, 2)        # on the corners of the scorer box
    a.text(0.05, 0.45, "Pitfalls controlled", ha="left", va="center", color=INK2, fontsize=7)
    for i, (x, t) in enumerate(zip((2.6, 5.1, 7.35), ["multiset vs set", "extra column", "unequal side info"])):
        badge(a, x, 0.45, i + 1)
        a.text(x + 0.25, 0.45, t, ha="left", va="center", color=INK2, fontsize=7)
    # right: accuracy by setting
    b = fig.add_axes([0.64, 0.17, 0.355, 0.66])
    conds, w = ["none", "doc", "evo", "dm"], 0.19
    for i, c in enumerate(conds):
        xs = [k + (i - 1.5) * w for k in range(3)]
        ys = [st[S]["conditions"][c]["tolerant"] for S in ("R", "O", "MG")]
        b.bar(xs, ys, width=w - 0.025, color=C[c], label=NAME[c], zorder=3)
    mg = {c: st["MG"]["conditions"][c]["tolerant"] for c in conds}
    for i, c in enumerate(conds):
        b.text(2 + (i - 1.5) * w, mg[c] + 1.2, f"{mg[c]:.0f}", ha="center", va="bottom", fontsize=7.2, color=INK)
    b.set_xticks(range(3)); b.set_xticklabels(["Readable", "Opaque", "Merged\n60 tables"])
    b.set_ylim(0, 64); b.set_ylabel("Accuracy (%)")
    clean(b)
    b.legend(ncol=2, frameon=False, loc="lower left", bbox_to_anchor=(-0.02, 1.0), handlelength=1.0, columnspacing=0.8,
             handletextpad=0.4, borderaxespad=0.1)
    save(fig, "teaser")


# --------------------------------------------------------------------------- pitfalls
def md_row(path, key, section=None):
    """(diff, lo, hi) of a row '| key | ... | +d [lo, hi] | ...' in a statistics markdown file, optionally within a
    '## section'."""
    import re
    text = Path(path).read_text()
    if section:
        text = text.split(f"## {section}", 1)[1].split("\n## ", 1)[0]
    line = next(l for l in text.splitlines() if l.startswith(f"| {key} |"))
    nums = re.findall(r"[+\-]?\d+\.\d+", line.split("|", 3)[3])
    m = re.search(r"([+\-]?\d+\.\d+)\s*\|?\s*\[([+\-]?\d+\.\d+),\s*([+\-]?\d+\.\d+)\]", line)
    return tuple(float(x) for x in m.groups()) if m else tuple(map(float, nums[:3]))


def pitfalls():
    a3 = load("analysis_sec3.json")["bird_full_info_matched"]["tolerant"]
    own = md_row(EXP / "runs/v2/completion_20261013/最终统计与恢复披露.md", "BIRD / full / native")
    setr = md_row(EXP / "runs/v2/rescore/统计_v3.md", "BIRD / full / native", "strict")
    tol = md_row(EXP / "runs/v2/rescore/统计_v3.md", "BIRD / full / native", "tolerant")
    rows = [("Own scorers", *own), ("One set rule", *setr), ("+ column tolerance", *tol),
            ("+ same side info", a3["diff"], *a3["ci"])]
    fig, ax = plt.subplots(figsize=(COL, 1.45))
    ys = list(range(len(rows)))[::-1]
    for y, (lab, d, lo, hi) in zip(ys, rows):
        ax.plot([lo, hi], [y, y], color=C["doc"], lw=1.6, solid_capstyle="round", zorder=3)
        ax.plot([d], [y], "o", ms=5.5, color=C["doc"], mec="white", mew=1.0, zorder=4)
        ax.text(hi + 0.5, y, f"{d:+.1f}".replace("-", "−"), va="center", ha="left", fontsize=7, color=INK)
    ax.axvline(0, color=AXIS, lw=0.9, zorder=1)
    ax.set_yticks(ys); ax.set_yticklabels([r[0] for r in rows])
    ax.set_xlim(-8, 16); ax.set_xticks([-5, 0, 5, 10, 15]); ax.set_xlabel("DataMind − EvoOntology (points)")
    clean(ax, grid="x")
    ax.spines["left"].set_visible(False); ax.tick_params(axis="y", length=0)
    fig.subplots_adjust(left=0.38, right=0.9, top=0.97, bottom=0.27)
    save(fig, "pitfalls", exact=True)


# --------------------------------------------------------------------------- benchmarks
def benchmarks():
    fig = plt.figure(figsize=(TXT, 1.55))
    a = fig.add_axes([0.0, 0.0, 0.48, 1.0]); a.set_xlim(0, 10.4); a.set_ylim(0, 4.15); a.axis("off")
    orig = ["CDSCode", "County Name", "School Name", "Free Meal Count (K-12)", "Enrollment (K-12)"]
    opq = ["fld001", "fld006", "fld008", "fld020", "fld019"]
    mrg = ["cl0001", "cl0006", "cl0008", "cl0020", "cl0019"]

    def card(x, w, title, items, mono=False):
        a.add_patch(FancyBboxPatch((x, 0.25), w, 3.3, boxstyle="round,pad=0.02,rounding_size=0.12", fc="#ffffff",
                                   ec=AXIS, lw=0.9))
        a.add_patch(Rectangle((x, 3.0), w, 0.55, fc="#eef3fb", ec="none"))
        a.text(x + w / 2, 3.27, title, ha="center", va="center", fontsize=7.5, weight="bold", color=INK,
               family="DejaVu Sans Mono" if mono else "DejaVu Sans")
        for i, t in enumerate(items):
            a.text(x + 0.16, 2.6 - i * 0.5, t, ha="left", va="center", fontsize=7.3, color=INK2,
                   family="DejaVu Sans Mono" if mono else "DejaVu Sans")
    card(0.05, 3.95, "frpm", orig)
    card(4.95, 2.05, "tab01", opq, mono=True)
    card(8.2, 2.05, "tb001", mrg, mono=True)
    arrow(a, (4.08, 1.75), (4.9, 1.75), color=INK2); arrow(a, (7.08, 1.75), (8.15, 1.75), color=INK2)
    for x, w, lab in ((0.05, 3.95, "original"), (4.95, 2.05, "BIRD-opaque"), (8.2, 2.05, "BIRD-opaque-merged")):
        a.text(x + w / 2, 3.72, lab, ha="center", va="bottom", fontsize=7, color=INK2)
    # right: 9 databases into one 60-table schema
    mm = json.loads((EXP / "variants/opaque_merged/mapping.json").read_text())
    sizes = [len(v["tables"]) for v in mm.values()]
    b = fig.add_axes([0.52, 0.06, 0.47, 0.86]); b.set_xlim(0, 20.5); b.set_ylim(-1.0, 3.6); b.axis("off")
    col = 0
    shades = ["#2a78d6", "#86b6ef"]
    for k, n in enumerate(sizes):
        for j in range(n):
            cx, cy = (col + j) % 20, 2 - (col + j) // 20
            b.add_patch(Rectangle((cx + 0.08, cy + 0.08), 0.84, 0.84, fc=shades[k % 2], ec="white", lw=0.8))
        col += n
    b.text(10, 3.25, f"{len(sizes)} databases → one schema of {sum(sizes)} tables",
           ha="center", va="center", fontsize=7, color=INK2)
    b.text(10, -0.45, "Shade alternates by source database\ntables per database: " + ", ".join(map(str, sizes)),
           ha="center", va="center", linespacing=1.2,
           fontsize=7.2, color=MUTED)
    save(fig, "benchmarks")


# --------------------------------------------------------------------------- paired differences
def forest_v4():
    st = load("stats_v4.json")
    pairs = [("doc-none", "Documentation − none"), ("evo-none", "EvoOntology − none"), ("dm-none", "DataMind − none"),
             ("evodoc-doc", "EvoOntology + doc − doc"), ("dmdoc-doc", "DataMind + doc − doc"), ("M-doc", "Compiled + check − doc")]
    fig, axs = plt.subplots(1, 3, figsize=(TXT, 1.75), sharey=True)
    ys = list(range(len(pairs)))[::-1]
    for ax, S in zip(axs, ("R", "O", "MG")):
        for y, (k, _) in zip(ys, pairs):
            p = st[S]["pairs"][f"{k}:tolerant"]
            lo, hi = p["ci"]; sig = lo > 0 or hi < 0
            color = C[{"doc-none": "doc", "evo-none": "evo", "dm-none": "dm", "evodoc-doc": "evo", "dmdoc-doc": "dm",
                       "M-doc": "M"}[k]]
            ax.plot([lo, hi], [y, y], color=color, lw=1.5, solid_capstyle="round", zorder=3)
            ax.plot([p["diff"]], [y], "o", ms=5, color=color if sig else "white", mec=color, mew=1.2, zorder=4)
        ax.axvline(0, color=AXIS, lw=0.9, zorder=1)
        ax.set_title(SET[S], color=INK, pad=3)
        ax.set_xlim(-24, 30)
        clean(ax, grid="x"); ax.spines["left"].set_visible(False); ax.tick_params(axis="y", length=0)
    axs[0].set_yticks(ys); axs[0].set_yticklabels([p[1] for p in pairs])
    axs[1].set_xlabel("Difference in column-tolerant accuracy (points); filled: 95% interval excludes 0")
    fig.subplots_adjust(wspace=0.08)
    save(fig, "forest_v4")


# --------------------------------------------------------------------------- ontology contents
def contents():
    t4 = load("analysis_table4.json")
    rows = []
    for key, lab in (("evoontology", "EvoOntology"), ("datamind", "DataMind"), ("datamind_dr093", "Rebuilt (§6.2)")):
        for f, fl in (("fwd", "A→B"), ("rev", "B→A")):
            c = t4[f][key]
            rows.append((f"{lab} {fl}", c["column_mappings_business_term"], c["column_mappings"] - c["column_mappings_business_term"]))
    fig, ax = plt.subplots(figsize=(COL, 1.95))
    ys = list(range(len(rows)))[::-1]
    for y, (lab, good, bad) in zip(ys, rows):
        ax.barh(y, good, height=0.62, color=C["doc"], zorder=3, label="Business-named term" if y == ys[0] else None)
        ax.barh(y, bad, left=good + 6, height=0.62, color="#c9c8c1", zorder=3, label="Code-named term" if y == ys[0] else None)
        ax.text(good / 2, y, str(good), ha="center", va="center", color="white", fontsize=7.2)
        if bad > 60:
            ax.text(good + 6 + bad / 2, y, str(bad), ha="center", va="center", color=INK, fontsize=7.2)
        elif bad > 0:
            ax.text(good + bad + 14, y, str(bad), ha="left", va="center", color=INK, fontsize=7.2)
    ax.set_yticks(ys); ax.set_yticklabels([r[0] for r in rows])
    ax.set_xlabel("Column-level mappings")
    ax.set_xlim(0, 1000); ax.set_xticks([0, 250, 500, 750, 1000])
    clean(ax, grid="x"); ax.spines["left"].set_visible(False); ax.tick_params(axis="y", length=0)
    ax.legend(frameon=False, ncol=2, loc="lower right", bbox_to_anchor=(1.02, 1.0), handlelength=1.0, columnspacing=0.9,
              handletextpad=0.4, borderaxespad=0.1)
    fig.subplots_adjust(left=0.35, right=0.93, top=0.86, bottom=0.21)
    save(fig, "contents", exact=True)


# --------------------------------------------------------------------------- turns
def turns():
    def mg_turns(c):
        out = []
        for f in ("fwd", "rev"):
            for rep in (1, 2, 3):
                res = sorted(glob.glob(str(V4 / "qa" / f"MG_{f}" / f"{c}_r{rep}" / "*/*/all_results.json")))[-1]
                out += [int(r.get("turns") or 0) for r in json.loads(Path(res).read_text())["results"]]
        return out
    a6 = load("analysis_sec6.json")
    fig, (a, b) = plt.subplots(1, 2, figsize=(TXT, 1.8), gridspec_kw={"width_ratios": [1.15, 1]})
    order = ["none", "doc", "evo", "dm", "M"]
    for c in order:
        t = mg_turns(c)
        xs = list(range(0, 30))                     # finished within x turns; runs that hit the limit never finish
        ys = [100 * sum(1 for v in t if v <= x and v < 30) / len(t) for x in xs]
        a.step(xs, ys, where="post", color=C[c], lw=1.6, label=NAME[c])
    a.axvline(30, color=AXIS, lw=0.9, ls=(0, (3, 2)))
    a.set_xlim(0, 31); a.set_ylim(0, 102); a.set_xticks([0, 5, 10, 15, 20, 25, 30])
    a.set_xlabel("Turns (merged schema; dashed: 30-turn limit)"); a.set_ylabel("Finished within (%)")
    clean(a, grid="both")
    conds, w = ["none", "doc", "evo", "dm"], 0.19
    for i, c in enumerate(conds):
        xs = [k + (i - 1.5) * w for k in range(3)]
        ys = [a6[S][c]["turn_limit_pct"] for S in ("R", "O", "MG")]
        b.bar(xs, ys, width=w - 0.025, color=C[c], zorder=3)
        b.text(2 + (i - 1.5) * w, ys[2] + 1.2, f"{ys[2]:.0f}", ha="center", va="bottom", fontsize=7.2, color=INK)
    b.set_xticks(range(3)); b.set_xticklabels(["Readable", "Opaque", "Merged"])
    b.set_ylabel("Used all 30 turns (%)"); b.set_ylim(0, 60)
    clean(b)
    h, l = a.get_legend_handles_labels()
    fig.legend(h, l, ncol=5, frameon=False, loc="lower center", bbox_to_anchor=(0.5, 0.98), handlelength=1.4,
               columnspacing=1.4)
    fig.subplots_adjust(wspace=0.3)
    save(fig, "turns")


# --------------------------------------------------------------------------- compiled excerpt coverage
def coverage():
    a6 = load("analysis_sec6.json")
    fig, (a, b) = plt.subplots(1, 2, figsize=(COL, 1.45), sharey=True, gridspec_kw={"width_ratios": [1, 1.25]})
    ys, labs = [2, 1, 0], ["Readable", "Opaque", "Merged"]
    vals = [a6[S]["M"]["gold_tables_covered_pct"] for S in ("R", "O", "MG")]
    a.barh(ys, vals, height=0.58, color=C["M"], zorder=3)
    for y, v in zip(ys, vals):
        a.text(v + 3, y, f"{v:.0f}%", ha="left", va="center", fontsize=7, color=INK)
    a.set_xlim(0, 125); a.set_xticks([0, 50, 100]); a.set_xlabel("Excerpt has all\ngold tables (%)")
    a.set_yticks(ys); a.set_yticklabels(labs)
    h = 0.34
    for y, S in zip(ys, ("R", "O", "MG")):
        m = a6[S]["M"]
        b.barh(y + h / 2, m["acc_covered"], height=h - 0.04, color=C["M"], zorder=3, label="covered" if S == "R" else None)
        b.barh(y - h / 2, m["acc_not_covered"], height=h - 0.04, color="#f6d48a", zorder=3, label="not covered" if S == "R" else None)
        b.text(m["acc_covered"] + 2, y + h / 2, f"{m['acc_covered']:.0f}", ha="left", va="center", fontsize=7.0, color=INK)
        b.text(m["acc_not_covered"] + 2, y - h / 2, f"{m['acc_not_covered']:.0f}", ha="left", va="center", fontsize=7.0, color=INK)
    b.set_xlim(0, 100); b.set_xticks([0, 50, 100]); b.set_xlabel("Accuracy (%)")
    b.legend(frameon=False, loc="lower right", bbox_to_anchor=(1.03, 0.98), ncol=2, handlelength=0.9, columnspacing=0.8,
             handletextpad=0.35, borderaxespad=0.1)
    for ax in (a, b):
        clean(ax, grid="x"); ax.spines["left"].set_visible(False); ax.tick_params(axis="y", length=0)
    fig.subplots_adjust(left=0.2, right=0.97, top=0.84, bottom=0.36, wspace=0.12)
    save(fig, "coverage", exact=True)


# --------------------------------------------------------------------------- builder changes (Section 6.2)
def rebuilt():
    s4, s93 = load("stats_v4.json")["MG"], load("stats_dr093.json")
    rows = [("Shared tools", "dm", "dm93", C["dm"]), ("Compiled + check", "M", "M93", C["M"])]
    fig, ax = plt.subplots(figsize=(COL, 1.6))
    refs = [("none", "No ontology"), ("evo", "EvoOntology"), ("doc", "Documentation")]
    for k, lab in refs:                             # reference lines, labelled vertically beside the line
        v = s4["conditions"][k]["tolerant"]
        ax.axvline(v, color=C[k], lw=1.0, ls=(0, (3, 2)), zorder=1)
        ax.text(v + 0.6, -0.48, lab, rotation=90, ha="left", va="bottom", fontsize=7.0, color=C[k] if k != "none" else INK2)
    for y, (lab, before, after, color) in zip([1, 0], rows):
        b0 = s93["conditions"][before]["tolerant"]; a = s93["conditions"][after]
        lo, hi = a["tolerant_ci"]
        ax.plot([lo, hi], [y, y], color=color, lw=7, alpha=0.22, solid_capstyle="butt", zorder=2)   # 95% interval of "after"
        ax.plot([b0, a["tolerant"]], [y, y], color=INK2, lw=0.9, ls=(0, (2, 1.5)), zorder=3)
        ax.plot([b0], [y], "o", ms=5, color="white", mec=color, mew=1.3, zorder=5)
        ax.plot([a["tolerant"]], [y], "o", ms=5.5, color=color, mec="white", mew=0.8, zorder=5)
    ax.set_yticks([1, 0]); ax.set_yticklabels([r[0] for r in rows])
    ax.set_ylim(-0.5, 1.45); ax.set_xlim(22, 58)
    ax.set_xlabel("Test accuracy, merged schema (%)")
    clean(ax, grid=None); ax.spines["left"].set_visible(False); ax.tick_params(axis="y", length=0)
    fig.subplots_adjust(left=0.33, right=0.97, top=0.97, bottom=0.24)
    save(fig, "rebuilt", exact=True)


# --------------------------------------------------------------------------- DataMind-chain ablations
def ablations():
    ab = load("analysis_ablation_v3.json")
    comps = [("noex", "exemplars"), ("nodict", "column documentation"), ("noexdict", "exemplars and documentation"),
             ("norel", "relations"), ("nosem", "concepts and caveats"), ("nolex", "Chinese names"),
             ("nomet", "metrics and value catalogues"), ("gaterel", "relation checks on the SQL"),
             ("intent", "intent cross-check")]
    fig, axs = plt.subplots(1, 2, figsize=(TXT, 1.95), sharey=True)
    ys = list(range(len(comps)))[::-1]
    for ax, pool, title in zip(axs, ("BIRD", "IND"), ("BIRD (full chain {:.1f}%)", "Industrial (full chain {:.1f}%)")):
        d = ab[pool]
        for y, (k, _) in zip(ys, comps):
            if k not in d:
                ax.text(0.3, y, "not run", va="center", ha="left", fontsize=7.0, color=MUTED)
                continue
            p = d[k]["tolerant"]; lo, hi = p["ci"]; sig = lo > 0 or hi < 0
            ax.plot([lo, hi], [y, y], color=C["dm"], lw=1.5, solid_capstyle="round", zorder=3)
            ax.plot([p["diff"]], [y], "o", ms=5, color=C["dm"] if sig else "white", mec=C["dm"], mew=1.2, zorder=4)
        ax.axvline(0, color=AXIS, lw=0.9, zorder=1)
        ax.set_title(title.format(d["full_chain"]["tolerant"]), color=INK, pad=3)
        ax.set_xlim(-11, 5.5); ax.set_xticks([-10, -5, 0, 5])
        clean(ax, grid="x"); ax.spines["left"].set_visible(False); ax.tick_params(axis="y", length=0)
    axs[0].set_yticks(ys); axs[0].set_yticklabels(["– " + c[1] for c in comps])
    fig.supxlabel("Change when removed (points); filled: interval excludes 0", fontsize=8, color=INK2, x=0.58, y=-0.06)
    fig.subplots_adjust(wspace=0.08)
    save(fig, "ablations")


# --------------------------------------------------------------------------- scoring rules (Section 3, schematic)
def scoring():
    """Schematic of the two scoring differences of Sections 3.3-3.4 on one question; the values are illustrative."""
    fig = plt.figure(figsize=(TXT, 1.7))
    a = fig.add_axes([0, 0, 1, 1]); a.set_xlim(0, 28); a.set_ylim(0, 7); a.axis("off")
    a.text(0.2, 6.6, "Question: Which customer has the highest total estimated amount?", ha="left", va="center",
           fontsize=8.5, color=INK, style="italic")

    def table(x, y, header, rows, widths, title):
        a.text(x, y + 0.95, title, ha="left", va="bottom", fontsize=8, color=INK2, weight="bold")
        cx = x
        for h, w in zip(header, widths):
            a.add_patch(Rectangle((cx, y + 0.35), w, 0.55, fc="#eef3fb", ec=AXIS, lw=0.7))
            a.text(cx + 0.15, y + 0.62, h, ha="left", va="center", fontsize=8, color=INK, family="DejaVu Sans Mono")
            cx += w
        for r, row in enumerate(rows):
            cx = x
            for v, w in zip(row, widths):
                a.add_patch(Rectangle((cx, y - 0.2 - 0.55 * r), w, 0.55, fc="#ffffff", ec=AXIS, lw=0.7))
                a.text(cx + 0.15, y + 0.07 - 0.55 * r, v, ha="left", va="center", fontsize=8, color=INK2)
                cx += w

    def verdict(x, y, rule, ok):
        col = "#1f7a4d" if ok else "#c0392b"
        a.text(x, y, ("✓ correct" if ok else "✗ wrong"), ha="left", va="center", fontsize=8, color=col, weight="bold")
        a.text(x + 2.7, y, rule, ha="left", va="center", fontsize=8, color=INK2)
    table(0.3, 4.4, ["name"], [["Customer 17"]], [4.2], "Gold result")
    table(6.2, 4.4, ["name"], [["Customer 17"], ["Customer 17"]], [4.2], "Answer A: a duplicate row")
    verdict(6.2, 2.8, "multiset rule (sorted row lists)", False)
    verdict(6.2, 2.1, "set rule (BIRD reference scorer)", True)
    table(17.6, 4.4, ["name", "total"], [["Customer 17", "4,820,000"]], [4.2, 3.4], "Answer B: an extra column")
    verdict(17.6, 2.8, "strict execution accuracy", False)
    verdict(17.6, 2.1, "column-tolerant accuracy", True)
    a.plot([16.6, 16.6], [1.4, 5.8], color=GRID, lw=0.9)
    a.text(0.3, 0.65, "Values are illustrative; Sections 3.3 and 3.4 give the counts on the benchmarks.",
           ha="left", va="center", fontsize=7.5, color=MUTED)
    save(fig, "scoring")


# --------------------------------------------------------------------------- per source database (Section 5)
def perdb():
    pd = load("analysis_perdb.json")
    mg = pd["MG"]
    dbs = sorted(mg, key=lambda d: (mg[d]["tables"], d))[::-1]
    short = {"california_schools": "california_schools", "thrombosis_prediction": "thrombosis", "toxicology": "toxicology",
             "debit_card_specializing": "debit_card", "card_games": "card_games", "financial": "financial",
             "student_club": "student_club", "superhero": "superhero", "formula_1": "formula_1"}
    fig, axs = plt.subplots(1, 2, figsize=(TXT, 2.05), sharey=True)
    ys = list(range(len(dbs)))
    for ax, k, title in zip(axs, ("evo", "doc"), ("EvoOntology − no ontology", "Documentation − no ontology")):
        for y, d in zip(ys, dbs):
            o, m = pd["O"][d]["gain"][k]["diff"], mg[d]["gain"][k]["diff"]
            ax.plot([o, m], [y, y], color=AXIS, lw=1.6, zorder=2, solid_capstyle="round")
            ax.plot([o], [y], "o", ms=5, color="white", mec=C[k], mew=1.3, zorder=4)
            ax.plot([m], [y], "o", ms=5.5, color=C[k], mec="white", mew=0.8, zorder=5)
        ax.axvline(0, color=AXIS, lw=0.9, zorder=1)
        ax.set_title(title, color=INK, pad=3)
        ax.set_xlim(-32, 48); ax.set_xticks([-20, 0, 20, 40])
        clean(ax, grid="x"); ax.spines["left"].set_visible(False); ax.tick_params(axis="y", length=0)
    axs[0].set_yticks(ys)
    axs[0].set_yticklabels([f"{short[d]} ({mg[d]['tables']} tables, {mg[d]['n']} q.)" for d in dbs])
    from matplotlib.lines import Line2D
    h = [Line2D([], [], marker="o", ls="", ms=5, mfc="white", mec=INK2, mew=1.2, label="own database, opaque names (O)"),
         Line2D([], [], marker="o", ls="", ms=5.5, mfc=INK2, mec="white", label="inside the 60-table merged schema (MG)")]
    fig.legend(handles=h, ncol=2, frameon=False, loc="lower center", bbox_to_anchor=(0.6, 0.97), handletextpad=0.3,
               columnspacing=1.6)
    fig.supxlabel("Gain in column-tolerant accuracy (points), same test questions", fontsize=8, color=INK2, x=0.6, y=-0.04)
    fig.subplots_adjust(wspace=0.08)
    save(fig, "perdb")


# --------------------------------------------------------------------------- protocol v5: ablation, turn budget, readable names
def v5():
    st = json.loads((EXP / "runs/v5/stats_v5.json").read_text())
    cd = st["conditions"]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(TXT, 2.05), gridspec_kw={"width_ratios": [1.15, 1]})
    # (a) merged schema with opaque names: which part of EvoOntology's ontology matters, and the turn budget
    rows = [("MG:none", "No ontology", C["none"]), ("MG:none_t60", "No ontology, 60 turns", C["none"]),
            ("MG:evo", "EvoOntology", C["evo"]), ("MG:evo_t60", "EvoOntology, 60 turns", C["evo"]),
            ("MG:evo_norel", "EvoOntology w/o relations", C["evo"]), ("MG:evo_nomap", "EvoOntology w/o mappings", C["evo"])]
    ys = list(range(len(rows)))[::-1]
    for y, (k, lab, col) in zip(ys, rows):
        v = cd[k]; lo, hi = v["tolerant_ci"]
        ax1.plot([lo, hi], [y, y], color=col, lw=6, alpha=0.22, solid_capstyle="butt", zorder=2)
        ax1.plot([v["tolerant"]], [y], "o", ms=5.5, color=col, mec="white", mew=0.8, zorder=5)
        for f, m in (("fwd", "^"), ("rev", "s")):
            ax1.plot([v["tolerant_by_fold"][f]], [y + 0.28], m, ms=3.4, color="white", mec=col, mew=0.9, zorder=4)
    ax1.axvline(cd["MG:doc"]["tolerant"], color=C["doc"], lw=1.0, ls=(0, (3, 2)), zorder=1)
    ax1.text(cd["MG:doc"]["tolerant"] + 0.6, len(rows) - 0.35, "Documentation", rotation=90, ha="left", va="top", fontsize=7.0, color=C["doc"])
    ax1.set_yticks(ys); ax1.set_yticklabels([r[1] for r in rows]); ax1.tick_params(axis="y", length=0)
    ax1.set_ylim(-0.6, len(rows) - 0.3); ax1.set_xlim(15, 60)
    ax1.set_xlabel("Test accuracy, merged schema, opaque names (%)")
    ax1.set_title("a  Ablation and turn budget", loc="left", fontsize=8, fontweight="bold")
    clean(ax1, grid=None); ax1.spines["left"].set_visible(False)
    # (b) the same 60 tables and questions with opaque and with readable names
    conds = [("none", "No ontology"), ("doc", "Documentation"), ("evo", "EvoOntology"), ("dm", "DataMind")]
    ys2 = list(range(len(conds)))[::-1]
    for y, (c, lab) in zip(ys2, conds):
        a, b = cd[f"MG:{c}"], cd[f"MGR:{c}_b9"] if c == "dm" and "MGR:dm_b9" in cd else cd[f"MGR:{c}"]   # DataMind: same builder code on both schemas
        ax2.plot([a["tolerant"], b["tolerant"]], [y, y], color=GRID, lw=2.2, zorder=1, solid_capstyle="round")
        ax2.plot([a["tolerant"]], [y], "o", ms=5.5, color="white", mec=C[c], mew=1.3, zorder=4)
        lo, hi = b["tolerant_ci"]
        ax2.plot([lo, hi], [y, y], color=C[c], lw=6, alpha=0.22, solid_capstyle="butt", zorder=2)
        ax2.plot([b["tolerant"]], [y], "o", ms=5.5, color=C[c], mec="white", mew=0.8, zorder=5)
    ax2.set_yticks(ys2); ax2.set_yticklabels([c[1] for c in conds]); ax2.tick_params(axis="y", length=0)
    ax2.set_ylim(-0.6, len(conds) - 0.4); ax2.set_xlim(15, 60)
    ax2.set_xlabel("Test accuracy, same 60 tables and questions (%)")
    ax2.set_title("b  Opaque (open) vs. readable names (filled)", loc="left", fontsize=8, fontweight="bold")
    clean(ax2, grid=None); ax2.spines["left"].set_visible(False)
    fig.subplots_adjust(left=0.2, right=0.985, top=0.88, bottom=0.2, wspace=0.62)
    save(fig, "v5")


if __name__ == "__main__":
    for f in (teaser, scoring, pitfalls, benchmarks, forest_v4, perdb, contents, rebuilt, coverage, turns, ablations, v5):
        f()
        print("drew", f.__name__)
