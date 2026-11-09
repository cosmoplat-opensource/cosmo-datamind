"""Reclaim reconstructible patch caches after verified word features are frozen."""

from datetime import datetime, timezone
import json
from pathlib import Path

import numpy as np

from fintab_scope_prepare_v10 import DEST
from prepare import save, sha


def run():
    folder = DEST / "features"
    manifest_file = folder / "manifest.json"
    manifest = json.loads(manifest_file.read_text())
    if not manifest["complete"] or sha(folder / "index.json") != manifest["index_sha256"]:
        raise ValueError("Complete frozen downstream features required before cache eviction")
    records = json.loads((folder / "index.json").read_text())
    images = {r["image_sha256"] for r in records}
    for record in records:
        if sha(record["file"]) != record["sha256"]:
            raise ValueError("Derived word features missing or changed")
    file = DEST / "patch_cache_eviction.json"
    if file.exists():
        existing = json.loads(file.read_text())
        if existing["complete"]:
            print("Reconstructible FinTabNet patch cache already evicted")
            return
        raise ValueError("Interrupted cache eviction requires inventory review")
    inventory = []
    for path in sorted((folder / "patches").glob("*.npy")):
        if path.stem not in images or path.is_symlink() or np.load(path, mmap_mode="r").shape != (197, 768):
            raise ValueError("Unexpected patch cache file")
        inventory.append({"file": str(path), "bytes": path.stat().st_size, "sha256": sha(path)})
    record = {"complete": False, "files": inventory, "feature_manifest_sha256": sha(manifest_file),
              "reason": "Unused reconstructible table patches; downstream native word vectors already frozen and verified",
              "source_images_annotations_encoders_models_and_predictions_preserved": True,
              "started_utc": datetime.now(timezone.utc).isoformat()}
    save(file, record)
    for row in inventory:
        path = Path(row["file"])
        if sha(path) != row["sha256"]:
            raise ValueError("Patch cache changed during eviction")
        path.unlink()
    record.update(complete=True, reclaimed_bytes=sum(r["bytes"] for r in inventory),
                  completed_utc=datetime.now(timezone.utc).isoformat())
    save(file, record)
    print("Reclaimed reconstructible patch cache bytes", record["reclaimed_bytes"], flush=True)


if __name__ == "__main__":
    run()
