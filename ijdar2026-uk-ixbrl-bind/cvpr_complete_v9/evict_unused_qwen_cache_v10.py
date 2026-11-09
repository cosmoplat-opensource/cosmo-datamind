"""Evict only two checksum-verified, never-used public Qwen cache shards.

Repository metadata, configs and provenance remain. This is not deletion of
user sources, predictions, trained research heads or original benchmark assets.
The explicit current research model is DeepSeek; these shards are redownloadable.
"""

import hashlib
import json
from datetime import datetime, timezone

from prepare import EXP, save, sha

REVISION = "938d8919941c6e7efd3c7150eff7fe9d12afa631"
ROOT = EXP / "ext/cvpr_public_v9/backbones/qwen" / REVISION
NAMES = ("model-00001-of-00002.safetensors", "model-00002-of-00002.safetensors")
LEDGER = (
    EXP
    / "runs/cvpr_complete_v10_20261014/source_preparation/unused_qwen_cache_eviction.json"
)


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024**2), b""):
            value.update(block)
    return value.hexdigest()


def run():
    provenance_file = ROOT / "provenance.json"
    provenance = json.loads(provenance_file.read_text())
    if (
        provenance["revision"] != REVISION
        or provenance["model_generation_verified"] is not False
        or provenance["repo"] != "mlx-community/Qwen3.5-9B-MLX-4bit"
    ):
        raise RuntimeError(
            "Cache ownership/revision or unused-model provenance differs"
        )
    old_state_file = EXP / "runs/cvpr_complete_v9_20261013/execution_state.json"
    old = json.loads(old_state_file.read_text())
    if old["primary_model"] != "DeepSeek V4.1 Flash (API: deepseek-flash)" or not old[
        "cross_model_replication"
    ].startswith("deferred"):
        raise RuntimeError(
            "Current user model steering does not allow unused-backbone eviction"
        )
    if LEDGER.exists():
        record = json.loads(LEDGER.read_text())
        if record.get("complete") and all(not (ROOT / name).exists() for name in NAMES):
            print("Previously recorded unused Qwen cache eviction complete", flush=True)
            return
    entries = []
    for name in NAMES:
        path = ROOT / name
        expected = provenance["files"][name]
        if (
            not path.is_file()
            or path.is_symlink()
            or path.stat().st_size != expected["bytes"]
            or digest(path) != expected["sha256"]
        ):
            raise RuntimeError("Only exact verified public cache shards may be evicted")
        entries.append(
            {
                "file": str(path),
                "bytes": expected["bytes"],
                "sha256": expected["sha256"],
                "redownload_url": f"https://huggingface.co/{provenance['repo']}/resolve/{REVISION}/{name}",
            }
        )
    record = {
        "complete": False,
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "reason": "Explicit user DeepSeek-only generation/vision steering; unexecuted public Qwen cache reclaimed for required annotation images",
        "files": entries,
        "provenance_file_preserved": str(provenance_file),
        "provenance_sha256": sha(provenance_file),
        "user_model_steering_state_sha256": sha(old_state_file),
        "predictions_original_data_or_trained_research_heads_deleted": False,
        "runner_sha256": sha(__file__),
    }
    save(LEDGER, record)
    for name in NAMES:
        (ROOT / name).unlink()
    record.update(
        complete=True,
        completed_utc=datetime.now(timezone.utc).isoformat(),
        reclaimed_cache_bytes=sum(e["bytes"] for e in entries),
    )
    save(LEDGER, record)
    print(
        "Evicted checksum-verified unused public Qwen cache bytes",
        record["reclaimed_cache_bytes"],
        flush=True,
    )


if __name__ == "__main__":
    run()
