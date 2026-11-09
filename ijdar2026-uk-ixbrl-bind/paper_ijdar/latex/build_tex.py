"""Convert the Markdown section drafts into one Springer Nature (sn-jnl) LaTeX manuscript.

usage: python3 build_tex.py [main|supp]   (run from paper_ijdar/latex)
  main (default): the manuscript, two-column (sn-jnl iicol), from 01-06_*.md -> manuscript.tex
  supp: Online Resource 1, one-column, from S1-S3_*.md -> supplement.tex; its sections, figures and tables are numbered
        S1, S2, ...; "Figure 5" or "Section 5.3" in the supplement refer to the manuscript and stay plain text, and
        "Figure S3" in the manuscript stays plain text.
The Markdown files remain the editing source; this script is deterministic and re-runnable.
"""

import re
import shutil
import sys
import unicodedata

import numpy as np
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
MODE = sys.argv[1] if len(sys.argv) > 1 else "main"
assert MODE in ("main", "supp"), __doc__
SUPP = MODE == "supp"
P = "S" if SUPP else ""  # number prefix of the sections, figures and tables of this document
SECTIONS = (["S1_benchmark.md", "S2_system.md", "S3_results.md"] if SUPP else
            ["01_introduction.md", "02_related_work.md", "03_dataset.md", "04_method.md", "05_experiments.md", "06_discussion.md"])
TITLE = "Visual Fact Binding of Rendered Financial Statements to iXBRL: A Benchmark and a Diagnostic Study"
KEYWORDS = "Inline XBRL, financial statements, table understanding, fact binding, benchmark, language models"
NARROW = {"fig6_ceiling", "fig11_companies", "fig14_abstention"}  # single-column figures
SCALED = {"fig3_benchmark": "0.94\\textwidth"}  # shares a page with Table 2
MAIN_SCALED = {"fig8_errors": "0.8\\textwidth"}  # two-column manuscript: a tall figure set narrower than the text block

SYMBOLS = {"−": "$-$", "×": "$\\times$", "→": "$\\rightarrow$", "←": "$\\leftarrow$", "±": "$\\pm$", "≥": "$\\geq$", "≤": "$\\leq$", "·": "$\\cdot$",
           "–": "--", "—": "---", "’": "'", "‘": "`", "“": "``", "”": "''", "…": "\\ldots{}", "Δ": "$\\Delta$", "≈": "$\\approx$", "£": "\\pounds{}",
           "€": "EUR\\,", "⟨": "$\\langle$", "⟩": "$\\rangle$", "ﬁ": "fi", "\u00a0": "~", "\u2009": "\\,", "≠": "$\\neq$", "∈": "$\\in$", "²": "$^2$"}


def esc(s, quotes=True):
    if quotes:
        s = re.sub(r'"([^"\n]*)"', r"``\1''", s)
        s = re.sub(r"(?<![\w'])'([^'\n]{1,40}?)'(?![\w'])", r"`\1'", s)  # 'x' as a quotation, not an apostrophe
    s = s.replace("\\", "\x01")
    s = re.sub(r"([%&#_$])", r"\\\1", s)
    s = s.replace("{", "\\{").replace("}", "\\}")
    s = s.replace("~", "\\textasciitilde{}").replace("^", "\\textasciicircum{}")
    s = s.replace("\x01", "\\textbackslash{}")
    for a, b in SYMBOLS.items():
        s = s.replace(a, b)
    return s


# ------------------------------------------------------------------ bibliography
def fold(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)).lower()


def load_bibliography():
    text = (SRC / "02_related_work.md").read_text()
    refs = [l[2:].strip() for l in text.split("## References")[1].splitlines() if l.startswith("* ")]
    entries, seen = [], {}
    for r in refs:
        authors = r.split(": ", 1)[0]
        names = [a.strip() for a in re.split(r",\s*(?=[A-ZŠ][^,]*?,\s*[A-Z]\.|et al\.)", authors)] if ", " in authors else [authors]
        surnames = [n.split(",")[0].strip() for n in names if n and n != "et al."]
        if "et al." in authors:
            surnames.append("et al.")
        core = re.split(r"\b(?:doi:|arXiv:|https?://)", r)[0]
        m = re.search(r"\(((?:19|20)\d{2})", core)
        years = re.findall(r"\b((?:19|20)\d{2})\b", core)
        year = m.group(1) if m else (years[-1] if years else "n.d.")
        first = surnames[0]
        key = re.sub(r"[^a-z]", "", fold(first)) + year
        entries.append({"base": key, "year": year, "first": first, "surnames": surnames, "text": r})
    # assign a/b suffixes where a base key repeats
    counts = {}
    for e in entries:
        counts[e["base"]] = counts.get(e["base"], 0) + 1
    idx = {}
    for e in entries:
        if counts[e["base"]] > 1:
            idx[e["base"]] = idx.get(e["base"], 0); positional = "abc"[idx[e["base"]]]; idx[e["base"]] += 1
            m = re.search(r"\((?:19|20)\d{2}([a-z])\)", e["text"])
            e["suffix"] = m.group(1) if m else positional
        else:
            e["suffix"] = ""
        e["key"] = e["base"] + e["suffix"]
        s = e["surnames"]
        if len(s) == 1:
            label = s[0]
        elif len(s) == 2 and s[1] != "et al.":
            label = s[0] + " and " + s[1]
        else:
            label = s[0] + " et al."
        e["label"] = label
    return entries


def cite_key(token, entries):
    """token like 'Göbel et al., 2013' or 'Smock et al., 2023a' or 'ESMA, 2024' or 'Yu, 2026'."""
    m = re.match(r"(.+?),\s*((?:19|20)\d{2})([a-z]?)$", token.strip())
    if not m:
        return None
    name, year, suf = m.groups()
    surname = re.sub(r"\s+(et al\.|and .*)$", "", name).strip()
    base = re.sub(r"[^a-z]", "", fold(surname)) + year
    hits = [e for e in entries if e["base"] == base]
    if hits:
        exact = [e for e in hits if e["suffix"] == suf]
        if len(exact) == 1:
            return exact[0]["key"]
        if not suf and len(hits) == 1:
            return hits[0]["key"]
        raise KeyError("ambiguous citation " + token)
    same = [e for e in entries if re.sub(r"[^a-z]", "", fold(e["first"])) == re.sub(r"[^a-z]", "", fold(surname)) and e["year"] == year]
    if len(same) == 1:
        return same[0]["key"]
    raise KeyError(token)


def replace_citations(s, entries):
    def bracket(m):
        toks = [t.strip() for t in m.group(1).split(";")]
        keys = [cite_key(t, entries) for t in toks]
        if any(k is None for k in keys):
            return m.group(0)
        return "\\citep{" + ",".join(keys) + "}"
    s = re.sub(r"\[((?:[A-ZŠ][^\[\]]*?,\s*(?:19|20)\d{2}[a-z]?)(?:;\s*[A-ZŠ][^\[\]]*?,\s*(?:19|20)\d{2}[a-z]?)*)\]", bracket, s)

    def narrative(m):
        key = cite_key(m.group(1) + ", " + m.group(2), entries)
        return "\\citet{" + key + "}"
    s = re.sub(r"([A-ZŠ][\w'’-]+(?: et al\.| and [A-ZŠ][\w'’-]+)?) \[((?:19|20)\d{2}[a-z]?)\]", narrative, s)
    return s


# ------------------------------------------------------------------ inline markdown
def inline(s, entries):
    s = replace_citations(s, entries)
    # protect citation commands and the code spans from escaping
    holes = []

    def hole(t):
        holes.append(t); return "\x00%d\x00" % (len(holes) - 1)
    s = re.sub(r"\\cite[tp]\{[^}]*\}", lambda m: hole(m.group(0)), s)
    s = re.sub(r"`([^`]+)`", lambda m: hole("\\texttt{" + esc(m.group(1), quotes=False).replace('"', "\\textquotedbl{}") + "}"), s)
    s = esc(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"\\textbf{\1}", s)
    s = re.sub(r"(?<![\w\\])\*(?!\s)(.+?)(?<!\s)\*(?!\w)", r"\\emph{\1}", s)
    n = P + r"(\d+)"                      # a figure or table number of this document
    sn = P + r"(\d+(?:\.\d+)?)"           # a section number of this document
    s = re.sub(r"Figures %s to %s" % (n, n), r"Figures~\\ref{fig:%s\1} to~\\ref{fig:%s\2}" % (P, P), s)
    s = re.sub(r"Figures %s, %s and %s" % (n, n, n), r"Figures~\\ref{fig:%s\1}, \\ref{fig:%s\2} and~\\ref{fig:%s\3}" % (P, P, P), s)
    s = re.sub(r"Figures %s and %s" % (n, n), r"Figures~\\ref{fig:%s\1} and~\\ref{fig:%s\2}" % (P, P), s)
    s = re.sub(r"Figure %s" % n, r"Figure~\\ref{fig:%s\1}" % P, s)
    s = re.sub(r"Tables %s to %s" % (n, n), r"Tables~\\ref{tab:%s\1} to~\\ref{tab:%s\2}" % (P, P), s)
    s = re.sub(r"Tables %s, %s and %s" % (n, n, n), r"Tables~\\ref{tab:%s\1}, \\ref{tab:%s\2} and~\\ref{tab:%s\3}" % (P, P, P), s)
    s = re.sub(r"Tables %s and %s" % (n, n), r"Tables~\\ref{tab:%s\1} and~\\ref{tab:%s\2}" % (P, P), s)
    s = re.sub(r"Table %s" % n, r"Table~\\ref{tab:%s\1}" % P, s)
    s = re.sub(r"Sections %s, %s, %s and %s" % (sn, sn, sn, sn), r"Sections~\\ref{sec:%s\1}, \\ref{sec:%s\2}, \\ref{sec:%s\3} and~\\ref{sec:%s\4}" % (P, P, P, P), s)
    s = re.sub(r"Sections %s, %s and %s" % (sn, sn, sn), r"Sections~\\ref{sec:%s\1}, \\ref{sec:%s\2} and~\\ref{sec:%s\3}" % (P, P, P), s)
    s = re.sub(r"Sections %s and %s" % (sn, sn), r"Sections~\\ref{sec:%s\1} and~\\ref{sec:%s\2}" % (P, P), s)
    s = re.sub(r"Sections? %s" % sn, lambda m: ("Sections" if m.group(0).startswith("Sections") else "Section") + "~\\ref{sec:" + P + m.group(1) + "}", s)
    s = re.sub(r"\x00(\d+)\x00", lambda m: holes[int(m.group(1))], s)
    return s


# ------------------------------------------------------------------ tables
TW, TH = (372.0, 552.7) if SUPP else (455.2, 614.6)  # pt, measured with \\the\\textwidth, \\the\\textheight (sn-basic; iicol)
# sn-jnl redefines the size commands: \footnotesize is 7/8 pt and \scriptsize 9/9 pt (larger, with no leading), so
# "\scriptsize" as a smaller fallback set tables at 9 pt.  Table sizes are therefore given explicitly.
EM = 0.45  # average character width of the table text per pt of font size (digits are 0.5 em, brackets and commas narrower)
WRAP = 0.52  # the same for wrapped prose in a ragged-right column (word breaks waste part of every line)
CAPLINES_PT = 9.5  # baseline skip of the table caption, pt (estimate)
MIN_TEXT = 80.0  # narrowest acceptable text column, pt; narrower columns wrap row labels into three or more lines


def split_header(h, width=10):
    """Wrap a column header into short lines (greedy, at most four)."""
    words = []
    for w in h.split(" "):  # a long hyphenated word may break after its hyphen
        if len(w) > width and "-" in w.strip("-"):
            parts = w.split("-"); words += [p + "-" for p in parts[:-1]] + [parts[-1]]
        else:
            words.append(w)
    if len(h) <= width or len(words) == 1:
        return [h]
    lines, cur = [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width and len(lines) < 3:
            lines.append(cur); cur = w
        else:
            cur = (cur + ("" if cur.endswith("-") else " ") + w).strip()
    lines.append(cur)
    return lines


def text_lines(n_chars, width, size):
    return max(1, -(-int(n_chars * WRAP * size) // max(1, int(width))))


def pipe_table(lines, caption, number, entries):
    rows = [[c.strip() for c in l.strip().strip("|").split("|")] for l in lines]
    header, align, body = rows[0], rows[1], rows[2:]
    ncol = len(header)
    assert len(align) == ncol and all(len(r) <= ncol for r in body), "table %s: a row has more cells than the header" % number
    body = [r + [""] * (ncol - len(r)) for r in body]
    clean = lambda c: re.sub(r"[*`]", "", c)
    bodylen = [max([len(clean(r[j])) for r in body] + [1]) for j in range(ncol)]
    is_text = [bl > 22 for bl in bodylen]
    just = ["r" if a.endswith(":") and not a.startswith(":") else ("c" if a.startswith(":") and a.endswith(":") else "l") for a in align]
    CI = re.compile(r"^(\*{0,2}[+−-]?\d[\d.]*\*{0,2}) (\[[^\]]*\])(\*{0,2})$")
    s_body = [max([len(clean(CI.sub(r"\2", r[j]) if CI.match(clean(r[j])) else r[j])) for r in body] + [1]) for j in range(ncol)]
    # bold figures are about 12% wider; used for the widths of the numeric columns only
    bold = lambda c: 1.12 if "**" in c else 1.0
    w_body = [max([bold(r[j]) * len(clean(r[j])) for r in body] + [1]) for j in range(ncol)]
    w_sbody = [max([bold(r[j]) * len(clean(CI.sub(r"\2", r[j]) if CI.match(clean(r[j])) else r[j])) for r in body] + [1]) for j in range(ncol)]
    text_idx = [j for j in range(ncol) if is_text[j]]

    def body_lines(widths, size, stacked):
        """Estimated printed lines of the body: each row is as tall as its most-wrapped text cell (two lines where a
        value and its interval are stacked)."""
        return sum(max([text_lines(len(clean(r[j])), widths[j], size) for j in text_idx] + [2 if stacked and any(CI.match(clean(c)) for c in r) else 1]) for r in body)

    def layout(size, min_text, cell_len, colsep, hw, stacked):
        """Widths of the text columns (None for the others): at least min_text each, split so as to minimise the
        estimated number of body lines.  Returns (fits, widths, header parts, estimated height)."""
        hparts = [[h] if is_text[j] else split_header(h, hw) for j, h in enumerate(header)]
        hlen = [max(len(clean(x)) for x in hp) for hp in hparts]
        avail = TW - 2 * colsep * (ncol - 1)  # @{} removes the outer padding
        numeric = sum(EM * size * max(b, h) + 2 for j, (b, h) in enumerate(zip(cell_len, hlen)) if not is_text[j])
        rest = avail - numeric
        widths = [None] * ncol
        if text_idx:
            if rest < min_text * len(text_idx):
                return False, widths, hparts, 0.0
            best = None
            step = 2.0
            free = rest - min_text * len(text_idx)
            if len(text_idx) == 1:
                cands = [[rest]]
            elif len(text_idx) == 2:
                cands = [[min_text + a, rest - min_text - a] for a in np.arange(0, free + 1e-9, step)]
            elif len(text_idx) == 3:
                cands = [[min_text + a, min_text + b, rest - 2 * min_text - a - b] for a in np.arange(0, free + 1e-9, step) for b in np.arange(0, free - a + 1e-9, step)]
            else:  # proportional to the square root of the longest cell
                wts = [bodylen[j] ** 0.5 for j in text_idx]
                cands = [[min_text + free * w / sum(wts) for w in wts]]
            for c in cands:
                trial = list(widths)
                for j, w in zip(text_idx, c):
                    trial[j] = w
                n = body_lines(trial, size, stacked)
                if best is None or n < best[0]:
                    best = (n, trial)
            widths = best[1]
        lead = size * 1.2
        head = max(len(hp) for hp in hparts) if not text_idx else max([len(hp) for hp in hparts] + [text_lines(len(clean(header[j])), widths[j], size) for j in text_idx])
        cap = -(-int(len(caption) * WRAP * 8.0) // int(TW)) * CAPLINES_PT
        height = cap + 18 + lead * (head + body_lines(widths, size, stacked))  # 18 pt: rules and the caption skip
        return rest >= 0, widths, hparts, height

    # every table is set upright at text width: at the largest font size that fits, the shorter (in estimated height)
    # of one line per cell and value-over-interval stacking; then smaller fonts and narrower text columns.  A table
    # taller than the text block does not fit.
    attempts = [(8.0, MIN_TEXT, 12, 2.5), (7.0, 72.0, 10, 2.0), (6.5, 64.0, 9, 1.6), (6.5, 48.0, 8, 1.4)]
    for size, mt, hw, colsep in attempts:
        fits = []
        for stack in (False, True):
            ok, widths, hparts, height = layout(size, mt, w_sbody if stack else w_body, colsep, hw, stack)
            if ok and height <= TH:
                fits.append((height, -min([w for w in widths if w is not None] + [TW]), stack, widths, hparts))
        if fits:
            height, _, stack, widths, hparts = min(fits, key=lambda f: f[:2])  # ties: the wider text columns
            break
    else:
        raise SystemExit("table %s does not fit the text block upright" % number)
    print("table %s %.1fpt %s, est. height %.0f pt" % (number, size, "stacked" if stack else "single-line", height))
    xs = [w for w in widths if w is not None]
    # tabularx gives every X column the same width unless \hsize is scaled; the factors sum to the number of X columns
    spec = "".join((">{\\raggedright\\arraybackslash\\hsize=%.3f\\hsize}X" % (len(xs) * w / sum(xs)) if w is not None else j) for w, j in zip(widths, just))
    env, width = ("table" if SUPP else "table*"), "\\textwidth"
    heads = []
    for h, hp, j in zip(header, hparts, just):
        if len(hp) == 1:
            heads.append(inline(h, entries))
        else:
            heads.append("\\begin{tabular}[b]{@{}%s@{}}%s\\end{tabular}" % (j, "\\\\{}".join(inline(x, entries) for x in hp)))
    out = ["\\begin{%s}[!htbp]" % env, "\\centering", "\\fontsize{%.1f}{%.1f}\\selectfont" % (size, size * 1.2), "\\setlength{\\tabcolsep}{%.1fpt}" % colsep,
           "\\caption{" + inline(caption, entries) + "}\\label{tab:%s}" % number, "\\begin{tabularx}{%s}{@{}%s@{}}" % (width, spec), "\\toprule",
           " & ".join(heads) + "\\tabularnewline", "\\midrule"]
    for r in body:
        cells = []
        for c, j in zip(r, just):
            m = CI.match(c)
            if stack and m:
                cells.append("\\begin{tabular}[t]{@{}%s@{}}%s\\\\{}%s\\end{tabular}" % (j, inline(m.group(1) + m.group(3), entries), inline(m.group(2), entries)))
            else:
                cells.append(inline(c, entries))
        out.append(" & ".join(cells) + "\\tabularnewline")
    out += ["\\botrule", "\\end{tabularx}", "\\end{%s}" % env, ""]
    return "\n".join(out)


# ------------------------------------------------------------------ sections
def convert(md, entries):
    md = re.sub(r"<!--.*?-->", "", md, flags=re.S)
    lines = md.splitlines()
    out, i = [], 0
    pending_caption = None
    while i < len(lines):
        l = lines[i]
        if l.startswith("## References"):
            break
        m = re.match(r"^# (%s\d+)\. (.*)$" % P, l)
        if m:
            # the supplement starts every top-level section on a new page, so that its floats stay with their section
            out.append(("\\clearpage\n" if SUPP and m.group(1) != "S1" else "") + "\\section{%s}\\label{sec:%s}" % (inline(m.group(2), entries), m.group(1))); i += 1; continue
        m = re.match(r"^## (%s\d+\.\d+) (.*)$" % P, l)
        if m:
            out.append("\\subsection{%s}\\label{sec:%s}" % (inline(m.group(2), entries), m.group(1))); i += 1; continue
        m = re.match(r"^\*\*Figure (%s\d+)\.\*\* (.*?)\s*\(`figures/(fig[^`]+)\.png`[^)]*\)\s*$" % P, l)
        if m:
            num, cap, f = m.groups()
            if SUPP:
                env, width = "figure", ("0.58\\textwidth" if f in NARROW else SCALED.get(f, "\\textwidth"))
            else:  # two-column manuscript: single-column figures in a column, the others across both columns
                env, width = ("figure", "\\columnwidth") if f in NARROW else ("figure*", MAIN_SCALED.get(f, "\\textwidth"))
            out.append("\\begin{%s}[!tb]\n\\centering\n\\includegraphics[width=%s]{figures/%s.pdf}\n\\caption{%s}\\label{fig:%s}\n\\end{%s}" % (env, width, f, inline(cap, entries), num, env)); i += 1; continue
        m = re.match(r"^\*\*Table (%s\d+)\.\*\* (.*)$" % P, l)
        if m:
            pending_caption = (m.group(1), m.group(2)); i += 1; continue
        if l.startswith("|"):
            block = []
            while i < len(lines) and lines[i].startswith("|"):
                block.append(lines[i]); i += 1
            if pending_caption is None:
                raise ValueError("table without a **Table N.** caption at line %d" % i)
            num, cap = pending_caption
            out.append(pipe_table(block, cap, num, entries)); pending_caption = None; continue
        if re.match(r"^\d+\. ", l):
            out.append("\\begin{enumerate}")
            while i < len(lines) and re.match(r"^\d+\. ", lines[i]):
                out.append("\\item " + inline(re.sub(r"^\d+\. ", "", lines[i]), entries)); i += 1
            out.append("\\end{enumerate}"); continue
        if re.match(r"^[*-] ", l):
            out.append("\\begin{itemize}")
            while i < len(lines) and re.match(r"^[*-] ", lines[i]):
                out.append("\\item " + inline(lines[i][2:], entries)); i += 1
            out.append("\\end{itemize}"); continue
        if l.strip() == "":
            out.append(""); i += 1; continue
        out.append(inline(l, entries)); i += 1
    if pending_caption is not None:
        raise ValueError("**Table %s.** caption without a table" % pending_caption[0])
    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def abstract_text(entries):
    md = (SRC / "00_abstract.md").read_text()
    paras = [p for p in re.sub(r"<!--.*?-->", "", md, flags=re.S).split("\n") if p.strip() and not p.startswith("#")]
    return "\n\n".join(inline(p, entries) for p in paras)


def bibliography(entries):
    out = ["\\begin{thebibliography}{99}"]
    for e in entries:
        urls = []
        raw = re.sub(r"https?://\S+", lambda m: (urls.append(m.group(0)), "\x02%d\x02" % (len(urls) - 1))[1], e["text"])
        text = esc(raw)
        text = re.sub(r"\x02(\d+)\x02", lambda m: "\\url{" + urls[int(m.group(1))] + "}", text)
        out.append("\\bibitem[%s(%s%s)]{%s} %s" % (esc(e["label"]), e["year"], e["suffix"], e["key"], text))
    out.append("\\end{thebibliography}")
    return "\n".join(out)


DECLARATIONS = r"""\section*{Statements and Declarations}

\bmhead{Funding} This work was supported by the Taishan Industrial Experts Programme (Grant No. tscx202507132).

\bmhead{Competing interests} The author declares no competing interests.

\bmhead{Ethics approval} Not applicable: the study uses public regulatory filings and involves no human participants.

\bmhead{Data availability} The benchmark, inventories, model responses, predictions and code are available at \url{https://github.com/cosmoplat-opensource/cosmo-datamind/tree/ijdar2026-uk-ixbrl-bind} (directory \texttt{ijdar2026-uk-ixbrl-bind}; code Apache-2.0, derived data CC BY 4.0), as described in Section~\ref{sec:3.5}.

\bmhead{Author contributions} J.Y. designed the benchmark and the systems, ran the experiments and wrote the manuscript.

\bmhead{Use of language models} Language models were used as components of the systems and baselines and for the ground-truth audit (Sections~\ref{sec:3.5} and~\ref{sec:5.3}), and in preparing the manuscript text; the author verified all content.
"""


def main():
    entries = load_bibliography()
    body = "\n\n".join(convert((SRC / f).read_text(), entries) for f in SECTIONS)
    if True:  # each document lists only the works it cites
        cited = set(re.findall(r"\\cite[tp]\{([^}]*)\}", body))
        cited = {k.strip() for c in cited for k in c.split(",")}
        entries = [e for e in entries if e["key"] in cited]
    # figures
    (HERE / "figures").mkdir(exist_ok=True)
    for name in sorted(set(re.findall(r"\{figures/(fig[^}]+)\.pdf\}", body))):
        src = SRC / "figures" / (name + (".pdf" if (SRC / "figures" / (name + ".pdf")).exists() else ".png"))
        shutil.copy(src, HERE / "figures" / src.name)
    # figures that exist only as PNG are included as PNG
    body = re.sub(r"\{figures/(fig[^}]+)\.pdf\}", lambda m: "{figures/%s.%s}" % (m.group(1), "pdf" if (HERE / "figures" / (m.group(1) + ".pdf")).exists() else "png"), body)
    tex = r"""%% Generated by build_tex.py from the Markdown section drafts; edit the Markdown, not this file.
\documentclass[pdflatex,sn-basic""" + ("" if SUPP else ",iicol") + r"""]{sn-jnl}
\usepackage[T1]{fontenc}
\usepackage{graphicx}
\usepackage{amsmath,amssymb}
\usepackage{textcomp}
\usepackage{array}
\usepackage{rotating}
\usepackage{tabularx}
\raggedbottom

""" + (r"""\renewcommand{\thesection}{S\arabic{section}}
\renewcommand{\thefigure}{S\arabic{figure}}
\renewcommand{\thetable}{S\arabic{table}}
""" if SUPP else "") + r"""
\begin{document}

\title[Visual Fact Binding of Financial Statements to iXBRL]{""" + (r"Online Resource 1 for: " if SUPP else "") + esc(TITLE) + r"""}

\author*[1]{\fnm{Jinze} \sur{Yu}}\email{jinze.yu.kp@gmail.com}

\affil*[1]{\orgname{State Key Laboratory of Mass Personalized Customization System and Technology}, \orgaddress{\city{Qingdao}, \country{China}}}

""" + (r"\abstract{Supplementary material to the manuscript: details of the benchmark and the system, and additional results. Sections, figures and tables of this document are numbered S1, S2, \ldots; Section, Figure and Table numbers without the prefix refer to the manuscript.}" if SUPP else r"\abstract{" + abstract_text(entries) + "}\n\n" + r"\keywords{" + KEYWORDS + r"}") + r"""

\maketitle

""" + body + r"""

""" + ("" if SUPP else DECLARATIONS) + r"""

""" + (bibliography(entries) if entries else "") + r"""

\end{document}
"""
    labels = set(re.findall(r"\\label\{((?:fig|tab):S?\d+)\}", tex)); refs = set(re.findall(r"\\ref\{((?:fig|tab):S?\d+)\}", tex))
    assert labels <= refs, "unreferenced floats: %s" % sorted(labels - refs)
    all_labels = set(re.findall(r"\\label\{([^}]+)\}", tex)); all_refs = set(re.findall(r"\\ref\{([^}]+)\}", tex))
    assert all_refs <= all_labels, "references to missing labels: %s" % sorted(all_refs - all_labels)
    n_fig_lines = sum(len(re.findall(r"^\*\*Figure %s\d+\.\*\*" % P, (SRC / f).read_text(), flags=re.M)) for f in SECTIONS); n_floats = len(re.findall(r"\\begin\{figure\*?\}", tex))
    assert n_fig_lines == n_floats, "figure captions without a float: %d lines, %d floats" % (n_fig_lines, n_floats)
    assert not re.search(r"\[[^\]]*,\s*(?:19|20)\d{2}[a-z]?\]", tex.split("\\begin{thebibliography}")[0]), "literal citation left"
    out = "supplement.tex" if SUPP else "manuscript.tex"
    (HERE / out).write_text(tex)
    print("wrote", out, len(entries), "references")


if __name__ == "__main__":
    main()
