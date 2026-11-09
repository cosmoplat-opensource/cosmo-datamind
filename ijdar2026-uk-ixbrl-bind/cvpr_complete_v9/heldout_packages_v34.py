"""Approved re-download (2026-10-26) of the six ESEF report packages of the two held-out companies,
needed for the v34 presentation/calculation-linkbase prior.  Sources, URLs and sha256 come from the
frozen 2026-10-20 held-out schema manifest (heldout_schema_v1); each package is verified against that
checksum before it is kept.  Packages are stored whole at heldout_v1/packages/<report>/package.zip;
only the _pre.xml / _cal.xml declaration members are later read (native_presentation_v34.parse_linkbases).
No instance or report member is opened here or downstream.
"""

import json
import zipfile
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

import requests
from heldout_priors_v1 import HELD
from native_schema_catalog_v10 import MAX_PACKAGE_BYTES, fetch
from prepare import save, sha

DEST = HELD / "packages"


def main():
    schema = json.loads((HELD / "schema/manifest.json").read_text())
    DEST.mkdir(exist_ok=True)
    out = []
    with requests.Session() as session:
        session.headers["User-Agent"] = "ixbrl-bind-benchmark/1.0 (academic; linkbase declaration members only)"
        for p in schema["packages"]:
            report, url, expected = p["report"], p["package_url"], p["package_sha256"]
            target = DEST / report / "package.zip"
            if target.exists() and sha(target) == expected:
                body = target.read_bytes(); status = "already present"
            else:
                body = fetch(session, url, MAX_PACKAGE_BYTES)
                got = sha256(body).hexdigest()
                if got != expected:
                    raise ValueError("checksum mismatch for %s: %s != %s" % (report, got, expected))
                target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(body); status = "downloaded"
            with zipfile.ZipFile(target) as z:
                members = [n for n in z.namelist() if n.lower().endswith(("_pre.xml", "_cal.xml")) and "META-INF" not in n]
            out.append({"report": report, "package_url": url, "package_sha256": expected, "package_bytes": len(body), "status": status,
                        "linkbase_members": members})
            print(report, status, len(body), "bytes", members, flush=True)
    save(DEST / "manifest.json", {"complete": True, "prepared_utc": datetime.now(timezone.utc).isoformat(), "download_approved_by_user": "2026-10-26",
                                  "checksums_from": str(HELD / "schema/manifest.json"), "packages": out, "members_to_be_read": "_pre.xml and _cal.xml only",
                                  "fact_or_report_instance_members_parsed": False, "runner_sha256": sha(Path(__file__))})
    print("held-out packages ->", DEST, flush=True)


if __name__ == "__main__":
    main()
