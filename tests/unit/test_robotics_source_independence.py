"""T06 RED: both compilation entries operate without source XML — DR-060 / DR-062 / DR-065."""
import builtins
import hashlib
from pathlib import Path

from tests.robotics_generation_support import context, task
from tests.robotics_support import SOURCE, MODELS, api, config, trees


def test_direct_compile_and_task_generation_do_not_read_source_xml(tmp_path, monkeypatch):
    engine = api("generation")
    loaded = context()
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (SOURCE, MODELS)}
    open_file, path_open = builtins.open, Path.open

    def check(path, mode):
        if isinstance(path, (str, Path)) and Path(path).suffix.lower() == ".xml" and not any(c in mode for c in "wax"):
            raise AssertionError(f"Generation attempted to read XML: {path}")

    def guarded_open(file, mode="r", *args, **kwargs):
        check(file, mode)
        return open_file(file, mode, *args, **kwargs)

    def guarded_path_open(self, mode="r", *args, **kwargs):
        check(self, mode)
        return path_open(self, mode, *args, **kwargs)

    with monkeypatch.context() as guard:
        guard.setattr(builtins, "open", guarded_open)
        guard.setattr(Path, "open", guarded_path_open)
        semantics = api("semantics")
        template = semantics.xml_from_graph(loaded["graph"])
        candidate = api("compiler").instantiate(template, config(), baseline=template,
            models_root=semantics.node_models_from_graph(loaded["graph"]),
            config_paths=semantics.configuration_paths(loaded["graph"]))
        result = engine.generate_task(loaded, task(), mode="demo", output_dir=tmp_path)
    assert trees(candidate) == trees(template)
    assert result["status"] == "generated" and Path(result["candidate_path"]).is_file()
    assert before == {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in before}
