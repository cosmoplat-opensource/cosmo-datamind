"""Sealed-company frozen test, step S: approved download of the selected filings (user approval 2026-10-26).

For each selected filing the ESEF report package is fetched from filings.xbrl.org, checksum-verified against the
frozen index record in selection.json, stored whole at <root>/packages/<report>/package.zip (its _pre.xml/_cal.xml
are read later by the linkbase prior), and its reports/*.xhtml member is extracted to
<sources>/<LEI>_<period_end>/original.xhtml for rendering.  The publisher's xBRL-JSON facts file is fetched to
<sources>/<LEI>_<period_end>/published_facts_scoring_only.json: it is read only by the target/reference step and
by scorers, never by candidate generation.  A company whose package exceeds the byte cap or fails the checksum is
replaced by the next in the pre-registered order and the replacement is recorded.
"""

import io
import json
import os
import zipfile
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

import requests
from native_schema_catalog_v10 import MAX_PACKAGE_BYTES, fetch
from prepare import save, sha
from sealed_select_v1 import ROOT, YEARS, N

SOURCES = Path(os.environ.get("HELDOUT_SOURCES", str(ROOT.parents[3] / "ext/xbrl_scope_sealed_test")))
HOST = "https://filings.xbrl.org"
MAX_FACTS_BYTES = 64 * 1024**2


def main():
    sel = json.loads((ROOT / "selection.json").read_text())
    done = {}
    with requests.Session() as session:
        session.headers["User-Agent"] = "ixbrl-bind-benchmark/1.0 (academic; sealed frozen test, approved 2026-10-26)"
        queue = list(sel["order"]); chosen = []
        while len(chosen) < N and queue:
            lei = queue.pop(0)
            if lei in sel["index_records"]:
                recs = sel["index_records"][lei]; ends = tuple(sel["filings"][lei])
            else:  # replacement company: look its filings up in the frozen index exactly as the selector did
                from sealed_select_v1 import index_filings
                by_end = index_filings([lei])[lei]
                ends = next((tuple(y + e[4:] for y in YEARS) for e in sorted(by_end) if e[:4] == YEARS[0] and all((y + e[4:]) in by_end for y in YEARS)), None)
                recs = {e: by_end[e] for e in ends} if ends else None
            if not recs:
                sel["replacements"].append({"lei": lei, "reason": "no index record"}); continue
            ok = True; entries = {}
            for end in ends:
                rec = recs[end]; report = lei + "_" + end[:4]; folder = SOURCES / (lei + "_" + end)
                pkg = ROOT / "packages" / report / "package.zip"
                try:
                    if pkg.exists() and sha(pkg) == rec["sha256"]:
                        body = pkg.read_bytes(); status = "already present"
                    else:
                        body = fetch(session, HOST + rec["package_url"], MAX_PACKAGE_BYTES)
                        if sha256(body).hexdigest() != rec["sha256"]:
                            raise ValueError("package checksum differs from the frozen index")
                        pkg.parent.mkdir(parents=True, exist_ok=True); pkg.write_bytes(body); status = "downloaded"
                    with zipfile.ZipFile(io.BytesIO(body)) as z:
                        reports = [n for n in z.namelist() if "/reports/" in n and n.lower().endswith((".xhtml", ".html")) and "META-INF" not in n]
                        if len(reports) != 1:
                            raise ValueError("expected one report member, found %d" % len(reports))
                        folder.mkdir(parents=True, exist_ok=True)
                        (folder / "original.xhtml").write_bytes(z.read(reports[0]))
                        members = [n for n in z.namelist() if n.lower().endswith(("_pre.xml", "_cal.xml")) and "META-INF" not in n]
                    facts = folder / "published_facts_scoring_only.json"
                    if not facts.exists():
                        facts.write_bytes(fetch(session, HOST + rec["json_url"], MAX_FACTS_BYTES))
                    entries[end] = {"report": report, "package": str(pkg), "package_sha256": rec["sha256"], "package_bytes": len(body), "status": status,
                                    "report_member": reports[0], "original_xhtml_sha256": sha(folder / "original.xhtml"), "facts_url": HOST + rec["json_url"],
                                    "facts_sha256": sha(facts), "linkbase_members": members}
                    print(report, status, len(body) // 1024, "KB", reports[0], flush=True)
                except Exception as error:
                    ok = False; sel["replacements"].append({"lei": lei, "filing": end, "reason": type(error).__name__ + ": " + str(error)[:200]})
                    print("REPLACE", lei, end, error, flush=True); break
            if ok:
                chosen.append(lei); done[lei] = entries; sel["filings"][lei] = list(ends); sel["index_records"][lei] = recs
        sel["companies"] = chosen
    save(ROOT / "selection.json", sel)
    save(ROOT / "sources_manifest.json", {"complete": len(chosen) == N, "prepared_utc": datetime.now(timezone.utc).isoformat(), "download_approved_by_user": "2026-10-26",
                                          "sources": str(SOURCES), "companies": chosen, "filings": done, "facts_file_role": "scoring only; never read by candidate generation",
                                          "runner_sha256": sha(Path(__file__))})
    print("sealed sources ->", SOURCES, len(chosen), "companies", flush=True)


if __name__ == "__main__":
    main()
