"""A writable synthetic build with distinct physical identities and business labels."""
import json

import pytest
import server


@pytest.fixture
def named_graph(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "WORK", str(tmp_path))
    key = "built_discovery"

    def save(objects=(), metrics=(), links=()):
        ir = {"scenario": {"name": "名称验收"}, "objects": list(objects), "links": list(links),
              "metric_layers": {"atomic": list(metrics)}}
        (tmp_path / (key + ".json")).write_text(json.dumps(ir, ensure_ascii=False), encoding="utf-8")
        return key

    return save
