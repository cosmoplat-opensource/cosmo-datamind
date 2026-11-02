"""T08: importing robotics must preserve existing data/chat state — DR-065 / ROB-12."""
import os
from pathlib import Path
import subprocess
import sys


def test_robotics_imports_do_not_mutate_datamind_data_or_chat_storage(tmp_path):
    root = Path(__file__).resolve().parents[2]
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    (workdir / "ont_chats.json").write_text('{"existing": [{"role":"user", "content":"历史消息"}]}')
    (workdir / "edits_demo.json").write_text('{"existing": "ontology edit"}')
    database = tmp_path / "existing.db"
    database.write_bytes(b"unchanged database sentinel")
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    env = dict(os.environ, PYTHONPATH=str(root), DATAMIND_WORKDIR=str(workdir), DATAMIND_DB=str(database))
    run = subprocess.run([sys.executable, "-c",
        "import domains.robotics.generation, domains.robotics.consumer, domains.robotics.publisher"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=20)
    assert run.returncode == 0, run.stderr
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before
