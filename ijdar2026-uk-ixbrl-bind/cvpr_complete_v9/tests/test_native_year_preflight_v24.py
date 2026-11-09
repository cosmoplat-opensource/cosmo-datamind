import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from native_year_preflight_v24 import preserve_previous
from prepare import sha


def previous_preflight(folder):
    source = folder / "source_snapshot/native_year_fit_v24.py"
    source.parent.mkdir(parents=True)
    source.write_text("# Archival unit-test fixture; no experiment result.\n")
    value = {"source_files_sha256": {source.name: sha(source)}, "complete": True}
    output = folder / "engineering_preflight.json"
    output.write_text(json.dumps(value))
    return source, output


def test_revised_engineering_code_preserves_the_previous_record_and_exact_snapshot(tmp_path):
    source, output = previous_preflight(tmp_path)
    digest = sha(output)
    assert preserve_previous(tmp_path) == digest
    archive = tmp_path / "engineering_preflights" / digest
    assert json.loads((archive / output.name).read_text()) == json.loads(output.read_text())
    assert (archive / "source_snapshot" / source.name).read_bytes() == source.read_bytes()
    assert sha(output) == digest


def test_corrupted_previous_snapshot_is_rejected_before_any_archive_is_written(tmp_path):
    source, _ = previous_preflight(tmp_path)
    source.write_text("# Changed after the recorded snapshot hash.\n")
    with pytest.raises(ValueError, match="snapshot changed"):
        preserve_previous(tmp_path)
    assert not (tmp_path / "engineering_preflights").exists()
