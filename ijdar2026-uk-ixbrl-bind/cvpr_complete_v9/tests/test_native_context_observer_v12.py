import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import native_context_observe_v12 as observer


def test_three_native_views_per_target_share_no_teacher_metadata(monkeypatch):
    monkeypatch.setattr(observer, "image_part", lambda path, digest: {"type": "image_url", "image_url": {"url": path}})
    tasks = []
    for ident in ("a", "b"):
        tasks.append({"task_id": ident, "image": "page.png", "image_sha256": "page",
                      "target_box": [.7, .5, .72, .51], "crop_image": ident+".png", "crop_image_sha256": ident,
                      "native_ocr_strings": ["42"], "crop_ocr_hypotheses": [],
                      "public_row_text": "Cash", "public_top_text": "Balance sheet",
                      "field_candidates": [{"field_id": "common", "name": "Public catalog field"}],
                      "row_context_image": ident+"row.png", "row_context_sha256": "row",
                      "row_context_box": [0, .4, 1, .6], "column_context_image": ident+"col.png",
                      "column_context_sha256": "col", "column_context_box": [.6, 0, .8, .6],
                      "source_year": 2025, "canonical_value": "PRIVATE_ANSWER", "field_name": "PRIVATE_FIELD"})
    content = observer.request(tasks)[0]["content"]
    assert len([c for c in content if c["type"] == "image_url"]) == 7
    assert "PRIVATE" not in content[0]["text"]
    payload = json.loads(content[0]["text"][len(observer.PROMPT):])
    assert [t["crop_image_index"] for t in payload["targets"]] == [1, 4]
    assert [t["row_context_image_index"] for t in payload["targets"]] == [2, 5]
    assert [t["column_context_image_index"] for t in payload["targets"]] == [3, 6]
    assert all("source_year" not in t for t in payload["targets"])
