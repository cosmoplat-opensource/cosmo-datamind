"""Versioned strict native OCR for TAT-DQA train/dev, preserving old artifacts."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path

from prepare import OUT, save, sha
from public_benchmarks import ROOT as ORIGINAL_ROOT
from strict_native_ocr import process

ROOT = OUT / "tat_native_ocr_v2_public"


def link_unchanged(target, source):
    target = Path(target)
    source = Path(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.symlink_to(source, target_is_directory=source.is_dir())
    if source.is_file() and sha(target) != sha(source):
        raise RuntimeError("Original source projection changed")


def refresh(split):
    if split not in ("train", "dev"):
        raise ValueError(
            "Held-out test assets are not opened during development repair"
        )
    old_root = ORIGINAL_ROOT / "tatdqa" / split
    root = ROOT / "tatdqa" / split
    original_manifest_file = old_root / "common_perception/manifest.json"
    original = json.loads(original_manifest_file.read_text())
    if (
        original["failures"]
        or len(original["documents"]) != original["expected_documents"]
    ):
        raise RuntimeError("Complete original native rendering required")
    link_unchanged(root / "public", old_root / "public")
    link_unchanged(root / "docs", old_root / "docs")
    link_unchanged(root / "protocol.json", old_root / "protocol.json")
    records = []
    changes = []
    failures = []

    def document(old):
        uid = old["document_id"]
        pages = []
        changed_pages = []
        for page in old["pages"]:
            image = old_root / page["image"]
            if (
                sha(image) != page["image_sha256"]
                or sha(old_root / page["ocr"]) != page["ocr_sha256"]
            ):
                raise RuntimeError("Original native image/OCR changed")
            link_unchanged(root / page["image"], image)
            target = root / page["ocr"]
            raw_file = target.with_suffix(".tsv")
            if target.exists():
                observation = json.loads(target.read_text())
                if (
                    observation["image_sha256"] != page["image_sha256"]
                    or not raw_file.exists()
                ):
                    raise RuntimeError("Cached strict OCR provenance mismatch")
            else:
                observation = process(image, raw_file)
                save(target, observation)
            previous = json.loads((old_root / page["ocr"]).read_text())
            if observation["words"] != previous["words"]:
                changed_pages.append(
                    {
                        "image": page["image"],
                        "old_words": len(previous["words"]),
                        "new_words": len(observation["words"]),
                        "old_ocr_sha256": page["ocr_sha256"],
                        "new_ocr_sha256": sha(target),
                        "raw_tsv_sha256": sha(raw_file),
                        "old_merged_words": sum(
                            "\t" in w["text"] or "\n" in w["text"]
                            for w in previous["words"]
                        ),
                    }
                )
            pages.append(dict(page, ocr_sha256=sha(target)))
        record = dict(old, pages=pages)
        save(root / "common_perception" / uid / "manifest.json", record)
        return record, changed_pages

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(document, old): old["document_id"]
            for old in original["documents"]
        }
        for future in as_completed(futures):
            try:
                record, changed = future.result()
                records.append(record)
                changes.extend(changed)
            except Exception as exc:
                failures.append(
                    {
                        "document_id": futures[future],
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:300],
                    }
                )
            save(
                root / "ocr_refresh_progress.json",
                {
                    "documents": len(records),
                    "expected_documents": original["expected_documents"],
                    "changed_pages": len(changes),
                    "failures": failures,
                },
            )
            if (len(records) + len(failures)) % 100 == 0:
                print(
                    "Strict TAT OCR",
                    split,
                    len(records),
                    "/",
                    original["expected_documents"],
                    "changed pages",
                    len(changes),
                    flush=True,
                )
    manifest = dict(
        original,
        documents=sorted(records, key=lambda r: r["document_id"]),
        failures=failures,
        code_sha256=sha(__file__),
    )
    save(root / "common_perception/manifest.json", manifest)
    save(
        root / "ocr_refresh_protocol.json",
        {
            "split": split,
            "documents": len(records),
            "pages": sum(len(r["pages"]) for r in records),
            "changed_pages": changes,
            "all_original_pages_reprocessed": True,
            "private_QA_reference_files_read": False,
            "originals_not_modified": True,
            "literal_quote_handling": "csv.QUOTE_NONE",
            "raw_TSV_preserved": True,
            "original_manifest_sha256": sha(original_manifest_file),
            "corrected_manifest_sha256": sha(root / "common_perception/manifest.json"),
            "strict_parser_source_sha256": sha(
                Path(__file__).with_name("strict_native_ocr.py")
            ),
            "runner_sha256": sha(__file__),
            "human_confirmations": 0,
        },
    )
    if failures:
        raise RuntimeError("Complete shared native OCR required; failures retained")
    print(
        "Strict TAT OCR complete:",
        split,
        len(records),
        "documents;",
        len(changes),
        "changed pages",
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("split", choices=("train", "dev", "all"))
    args = parser.parse_args()
    for split in ("dev", "train") if args.split == "all" else (args.split,):
        refresh(split)
