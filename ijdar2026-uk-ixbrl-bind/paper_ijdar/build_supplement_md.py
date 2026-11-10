"""Assemble the Online Resource 1 drafts (S1-S3) from paragraphs of the pre-condensation manuscript, renumbering references."""
import re, sys
from pathlib import Path
PAPER = Path("paper_ijdar")
B = PAPER / "review/pre_condense_20261109"
OLD = "\n".join((B / f).read_text() for f in ["03_dataset.md", "04_method.md", "05_experiments.md"])
paras = [p for p in OLD.split("\n\n")]

SEC = {"3.4": "Section 3.3", "3.5": "Section 3.4", "3.6": "Section 3.5", "3.7": "Section 3.5", "4.1": "Section S1.3", "4.2": "Section 4.1",
       "4.3": "Section 4.2", "4.4": "Section 4.3", "4.5": "Section 4.1", "5.4": "Section 5.7", "5.5": "Section S3.3", "5.6": "Section 5.4",
       "5.7": "Section 5.5", "5.8": "Section 5.6"}
TAB = {"1": "1", "2": "S1", "3": "S2", "4": "2", "5": "S3", "6": "3", "7": "4", "8": "5", "9": "6"}
FIG = {"1": "1", "2": "2", "3": "S1", "4": "3", "5": "S2", "6": "4", "7": "S3", "8": "5", "9": "6", "10": "7", "11": "S4", "12": "S5", "13": "S6", "14": "S7"}

def renum(s):
    s = s.replace("Tables 4 and 5", "Table 2 and Table S3").replace("Tables 5 to 8", "Tables 2 to 5")
    s = re.sub(r"Sections (\d\.\d) and (\d\.\d)", lambda m: "%s and %s" % (SEC.get(m.group(1), "Section " + m.group(1)), SEC.get(m.group(2), "Section " + m.group(2))), s)
    s = re.sub(r"Sections (\d\.\d), (\d\.\d) and (\d\.\d)", lambda m: ", ".join(SEC.get(x, "Section " + x) for x in m.groups()[:2]) + " and " + SEC.get(m.group(3), "Section " + m.group(3)), s)
    s = re.sub(r"Section (\d\.\d)", lambda m: "\x01" + SEC.get(m.group(1), "Section " + m.group(1)) + "\x01", s)
    s = re.sub(r"\bTable (\d+)", lambda m: "\x01Table " + TAB[m.group(1)] + "\x01", s)
    s = re.sub(r"\bFigure (\d+)", lambda m: "\x01Figure " + FIG[m.group(1)] + "\x01", s)
    return s.replace("\x01", "")

def take(prefix, renumber=True):
    hits = [p for p in paras if p.startswith(prefix)]
    assert len(hits) == 1, (prefix, len(hits))
    return renum(hits[0]) if renumber else hits[0]

def table(caption_prefix):  # caption paragraph plus its table block
    i = [k for k, p in enumerate(paras) if p.startswith(caption_prefix)]
    assert len(i) == 1, caption_prefix
    return renum(paras[i[0]]), renum(paras[i[0] + 1])

out = {}

def J(*xs): return "\n\n".join(xs) + "\n"

S1 = J("# S1. Benchmark details",
"## S1.1 Companies and composition",
"The 14 development companies are Standard Life plc (LEI 2138001P49OLAEU33T68; named Phoenix Group Holdings plc until February 2026), PPHE Hotel Group, Hunting, Warehouse REIT, J Smart & Co, Rainbow Rare Earths, Oxford Instruments, Cordiant Digital Infrastructure, Amala Foods, OSB Group, Imperial Brands, Liontrust Asset Management, First Class Metals and Capita. Bay Capital and Wilmington form the held-out split. The ten sealed companies opened for the frozen test are AstraZeneca, Castelnau, Bellway, XP Power, Moonpig, Bank of Ireland, Aberdeen Group, GSK, PZ Cussons and Greencoat UK Wind; the remaining fifty sealed companies are identified by their LEIs in the release. The 26 LEIs of the development, held-out and opened sealed companies are distinct; in particular Standard Life is a different legal entity from the sealed issuer Aberdeen Group (LEI 0TMBS544NMO7GLCE7H90). Bank of Ireland Group, incorporated in Ireland, appears in the GB index and is included as indexed. Two issuers of an earlier pilot were excluded before the hash ordering. Table S1 and Figure S1 give the composition of each split.",
*table("**Table 2.**"), take("**Figure 3.**"),
"## S1.2 Rendering, targets and references",
take("Each filing is rendered page by page"), take("A target is one `ix:nonFraction`"), take("Zero-valued cells (printed as dashes)"),
"## S1.3 Candidate inventory construction",
take("**Concepts.** For a filing"), take("**Periods.** Every explicit date"), take("**Dimensions.** The empty tuple"), take("**Units and values.** Currency"),
take("**Witnesses.** Every candidate value"), take("The inventories of the three splits were built"),
"## S1.4 Audit details",
take("**Tag–cell correspondence.**"), take("**Rendering defect.**"), take("**Issuer-side defects"), take("**Sampling caveats.**"),
"## S1.5 Release, licensing and reproducibility",
take("**What is released.**"), take("**Licensing.**"), take("**Reproducibility of the language-model components.**"),
"## S1.6 Language-model calls",
*table("**Table 3.**"))

S2 = J("# S2. System details",
"## S2.1 Priors and the structural prior",
take("Each prior is a small linear scorer"), take("**Structural column/row ownership"), take("**Figure 5.**"),
"## S2.2 Concept × unit–value pairing", take("**Concept × unit–value pairing.**"),
"## S2.3 Concept disambiguation", take("**Concept disambiguation.**"), take("* the retrieval scores;"),
"## S2.4 Presentation and calculation linkbases", take("**Presentation and calculation structure.**"), take("* membership of any network"), take("With these features the leave-one-company-out"),
"## S2.5 Witness composer and language-model readings", take("The composer is a 94k-parameter model"), take("A multimodal language model (deepseek-flash through"), take("The structural segmentation, the period and unit grammars"),
"## S2.6 Training protocol and learners", take("**Leave-one-company-out training.**"), take("**Prior fitting.**"), take("**Learners.** Six learners"), take("1. the *prior-only* learner"), take("The pair (scope, geometry-zeroed)"))

S3 = J("# S3. Additional results",
"## S3.1 Prior ablation on the development set",
take("Figure 6 separates reachability from selection"), *table("**Table 5.**"), take("**Figure 7.**"),
take("Every learner gains by a similar amount"), take("The last row of Table 5 tests"),
"## S3.2 Baseline details", take("**Baseline protocol.**"), take("**Controls.** The *few-shot* baseline"),
"## S3.3 Analyses on the development set",
take("**Where the structural prior acts.**"), take("**Zero cells.**"), take("**Prior-year supervision.**"), take("**Near-duplicate rows.**"), take("**Per-company spread.**"),
take("**Abstention.** A user"), take("**Page-level consistency.**"), take("**Cost.**"),
"## S3.4 Held-out companies",
take("**Result with the frozen system (v31).**"), take("**Diagnosis.** On these two companies"), take("**Re-test after the zero-shot period"), take("**Re-test after the few-shot refit"),
take("**Re-test of the linkbase prior (v34).**"), take("**v34 against the deepseek-flash controls.**"), take("**The no-LLM system.** The no-LLM system matches"), take("**Commercial models.** Claude Sonnet 5.5, run zero-shot on the same 160"),
"## S3.5 Sealed companies",
take("Two deviations from the written protocol"), take("**Result.** The final system reaches 46.9%"), take("**Annotated control and no-LLM system.**"), take("**Summary of the frozen test.**"),
"## S3.6 The three splits: additional figures",
take("*Per company* (Figure 11)"), take("*History* (Figure 12)"), take("*Error anatomy* (Figure 13)"), take("*Abstention* (Figure 14)"),
take("**Figure 11.**"), take("**Figure 12.**"), take("**Figure 13.**"), take("**Figure 14.**"),
"## S3.7 The geometric scope mechanism", take("The original hypothesis, that a learned geometric scope"))

FIXES = {"S1": [("the OCR output and word features; the structural priors with their fitted weights; the reference files of the ten opened sealed companies with their checksums; the audit cards; every language-model request and response (prompts, the model identifier returned by the API, dates, usage);",
                "the references; the structural priors with their fitted weights; the reference files of the ten opened sealed companies with their checksums; the audit sample with per-target verdicts and rationale; every language-model response with the design record of its run (the model identifier returned by the API, dates, usage and, for most runs, the decoding parameters and prompt-template hash), from which the scripts rebuild each request;"),
               ("the comparisons designated in dated files before the ablations (Section 5.1) and the sealed selection rule of Section 5.5 are released as dated files with their hashes.",
                "the roster freeze and the sealed selection rule of Section 5.5 are released as dated files with their hashes."),
               ("The word features are derived with LayoutLMv3 and MiniLM and are released under the terms of those models;",
                "Page images and full-page OCR output are not redistributed; the OCR and the word features (LayoutLMv3 and MiniLM, pinned versions) are regenerated by the released scripts;"),
               ("A scoring script fetches their references from the index at evaluation time.",
                "Their references can be derived from the public index with the released target and reference builders used for the ten opened companies."),
               ("All calls are cached and released, so every number","All responses are cached and released with their run designs, so every number")],
         "S2": [("(Section 5.7 and Section S3.3)", "(Sections 5.4 and 5.5)")],
         "S3": [("## S3.4 Held-out companies", "**Re-analyses of the released predictions** (figures/reanalysis_v1.py). *Multiplicity*: two-sided bootstrap p-values (2,000 company-cluster resamples, resolution 0.001) for the eleven designated tests are 0.001 for the structural prior (+11.0), the language model's concept ranking (+7.4), its period and dimension choices (+4.9), and v32 against the rule-only baseline, the two zero-shot baselines and GPT-5.4; 0.002 against Claude Sonnet 5.5; 0.009 against the history-matched control; 0.99 against Gemini 3.1 Pro; and 0.093 for the scope composer against its zeroed control on the v32 inventory. Holm's procedure at 0.05 keeps all but the last two. *Glyph rule*: applying the held-out and sealed sampling test to the corrected OCR fails 61 development cells (38 without a numeral, 11 with several, 12 inconsistent); on the 499 others v34 reaches 56.3%, the history-matched control 41.5% and the annotated control 47.1% (v34 − controls +14.8 [+8.7, +21.2] and +9.2 [+4.2, +14.4], 12 of 14 companies each); on the 61 failing cells all three are at 26% to 28%. *Value errors* (v34 at budget 10, seeds pooled): sign-only errors make up 56.5%, 76.5% and 87.6% of the value errors on the development, held-out and sealed splits and scale errors 10.8%, 23.5% and 12.4%; on the development split 4.3% involve zero cells, 18.7% other glyph readings and 9.7% cells without a value. For the history-matched control the sign-only shares are 77.0%, 100% and 85.7%." + "\n\n## S3.4 Held-out companies"), ("Table S1 and Table S3", "Table 2 and Table S3"), ("for the eleven designated tests are", "for the eleven tests of the main family are"), ("Section 5.6 shows these four agreement rules", "Figure S7 shows these four agreement rules"),
                ("The scope composer is indistinguishable from its own geometry-zeroed control (differences within ±0.7 points, intervals containing zero).",
                 "Against its own geometry-zeroed control the scope composer differs by at most ±0.7 points; recomputed with the bootstrap of Section 5.1 (figures/reanalysis_v1.py), the interval excludes zero only on v27, against the mechanism (−0.6 [−1.1, −0.02]).")]}

FIXES["S1"] += [
 ("the audit cards and the per-target verdicts and rationale are released with the benchmark.",
  "the audit sample, the list of cards and the per-target verdicts and rationale are released with the benchmark; the rendered cards are not."),
 ("the Poe API returned the identifiers gpt-5.4, claude-sonnet-5.5 and gemini-3.1-pro.",
  "the Poe API returned the identifiers gpt-5.4 (in 387 of 880 calls written GPT-5.4), claude-sonnet-5.5 and gemini-3.1-pro. The design records of the few-shot runs do not store the temperature or a prompt-template hash; their code sends temperature 0."),
 ("2026-10-26 (held-out) and 2026-11-04 (sealed, rerun with the period end); 560 + 160 + 800 calls |",
  "2026-10-26 (held-out), 2026-10-26 (sealed, first run without the period end) and 2026-11-04 (sealed, rerun with the period end); 560 + 160 + 800 + 800 calls |"),
]
FIXES["S2"] += [
 ("## S2.2 Concept × unit–value pairing\n\n**Concept × unit–value pairing.** ", "## S2.2 Concept × unit–value pairing\n\n"),
 ("## S2.3 Concept disambiguation\n\n**Concept disambiguation.** Sibling IFRS elements (OtherComprehensiveIncome vs ComprehensiveIncome, TradeAndOtherPayables vs NoncurrentPayables) are",
  "## S2.3 Concept disambiguation\n\nConfusions between sibling IFRS elements (OtherComprehensiveIncome versus ComprehensiveIncome, TradeAndOtherPayables versus NoncurrentPayables) are"),
 ("## S2.4 Presentation and calculation linkbases\n\n**Presentation and calculation structure.** ", "## S2.4 Presentation and calculation linkbases\n\n"),
 ("the Inline XBRL report itself is not opened.", "the linkbase prior does not read the Inline XBRL report."),
 ("Within the development population the priors depend little", "Within the development population the v33 priors depend little"),
]
FIXES["S3"] += [
 ("printed as landscape spreads (Section 3.5);", "printed as landscape spreads (Section S1.4);"),
 ("GPT-5.4 and Gemini 3.1 Pro were not re-sent.", "the re-sending of GPT-5.4 and Gemini 3.1 Pro was not completed within the API budget, and no partial result is used."),
 ("77% of period errors chose the neighbouring year column", "83% of period errors chose a period ending one year earlier or later (the neighbouring year column; figures/reanalysis_v2.py)"),
 ("**Near-duplicate rows.** For 109 of the 560 evaluation targets (19%) the row label of the shared page reading is identical to that of one of the company's own 2023 teachers; v32 scores 52.3% on these and 42.4% on the rest",
  "**Repeated row labels.** For 119 of the 560 evaluation targets (21%) the OCR row label (lower-cased, punctuation removed) is identical to that of one of the company's own 2023 teachers; v32 scores 52.9% on these and 42.0% on the rest (figures/reanalysis_v2.py)"),
 ("after all other held-out results were in,", "on 3 November, after the system's held-out results were in,"),
 ("On the v30 inventory, before any language-model prior, the interval lies below zero (−1.1 [−1.8, −0.5]); on v31 its upper bound is zero (−1.1 [−2.2, 0.0]).",
  "On the v30 inventory, before any language-model prior, the scope composer is below the semantic-memory composer (−1.1 [−1.8, −0.5]); on v31 the upper bound of that interval is zero (−1.1 [−2.2, +0.0])."),
 ("**Result.** The final system reaches 46.9%",
  "A defect of the selection input was found after the test. The selection script read the filings index through unsorted pages, which repeat 208 and omit 208 of its 2,959 records; in them 42 of the 60 sealed companies show filings for three consecutive year ends, whereas in the stable-sorted pages used for the reservation (Section 3.2) all 60 do. The ten companies were therefore drawn from 42 rather than 60. The rule and its input were fixed before any sealed filing was downloaded, so the defect does not depend on any result; the 18 companies it excluded remain unopened.\n\n**Result.** The final system reaches 46.9%"),
]
GLOBAL = [("shared page reading", "shared page transcription"), ("one zero-shot reading of each target's table region", "one zero-shot transcription of each target's table region")]

def fix_s1_caption(t):
    i = t.index("**Figure S1.**"); j = t.index("\n", i)
    return t[:i] + t[i:j].replace("(Section 3.3)", "(Section S1.2)") + t[j:]
S1 = fix_s1_caption(S1)
for k, pairs in FIXES.items():
    for a, b in pairs:
        if a != b:
            assert globals()[k].count(a) == 1, a
            globals()[k] = globals()[k].replace(a, b)
for k in ("S1", "S2", "S3"):
    for a, b in GLOBAL:
        globals()[k] = globals()[k].replace(a, b)
for name, txt in (("S1_benchmark.md", globals()["S1"]), ("S2_system.md", globals()["S2"]), ("S3_results.md", globals()["S3"])):
    (PAPER / name).write_text(txt)
    print(name, len(txt.split()), "words")
