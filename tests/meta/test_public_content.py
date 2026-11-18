# -*- coding: utf-8 -*-
"""公开仓库的内容边界:不含个人本机路径、外部单位或个人的名称、AI 编程工具的工作区,文档里的证据链接要能在仓库里打开。

仓库公开发布,开发过程中的本机路径(含用户名)、合作方名称一旦合入就随历史公开。这里在提交前拦下。
禁用的名称不以明文写在这里,只存小写名称的长度与 SHA-256,逐段比对。
"""
import hashlib
import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]
BINARY = re.compile(r"\.(png|jpe?g|gif|ico|docx|xlsx|pptx|pdf|db|sqlite|zip|gz|woff2?|ttf|owl\.gz)$", re.I)


def _tracked_text():
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    for name in out:
        if BINARY.search(name) or name.startswith("ui/vendor/"):
            continue
        path = ROOT / name
        try:
            yield name, path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue


def test_no_personal_home_directories():
    """/home/<用户>/、/Users/<用户>/ 这类路径带出开发者的用户名和本机目录结构。"""
    pat = re.compile(r"/(?:home|Users)/[a-z][a-z0-9_.-]*/")
    hits = {name: len(pat.findall(text)) for name, text in _tracked_text() if pat.search(text)}
    assert not hits, hits


# 外部单位、合作方与个人的名称(小写),只存类别、长度与摘要。类别:word 在英文词元内比对,cjk 在连续汉字内比对
_NAME_DIGESTS = (
    ("cjk", 2, "c25551533072cf3d52d2848f34df26f7225753f0e4e493e0596cfb455d0d271e"),
    ("word", 4, "83590d8cbc109a1cd6797ae0966301eb009c30b83811ec21f16e0fe24dc9faf6"),
    ("word", 7, "30e363d3e8c59f2c1319f8d73d48e3ad26db5e087951a4d7ab809c6f5401aea8"),
    ("cjk", 3, "b61c7d641d8875bbd2d3e3c541cc756ad7575f05dd6382b9bbc93b1b77422778"),
    ("cjk", 3, "b7576f5c92a16572a4b26a19a1c5e37994e4b75df2ed80a0c2685bf81d777d8e"),
    ("word", 9, "4d748ebd71af03a7c325a5ecef86e3768b71597ba3c08dc26624bfdbd84a0a34"),
    ("word", 10, "99fe2c1289dd5b584d80a97c05062711e9d27aa31306870ddc894b7251104093"),
    ("word", 19, "26acc380090afaf80ea3ffaad512faa469c8fba1a351cf08e7f35fa9e5c21f99"),
)
_RUNS = {"word": re.compile(r"[a-z0-9_]+"), "cjk": re.compile(r"[\u4e00-\u9fff]+")}


def _found(text):
    low, out = text.lower(), set()
    for kind, rx in _RUNS.items():
        wanted = {}
        for k, n, digest in _NAME_DIGESTS:
            if k == kind:
                wanted.setdefault(n, set()).add(digest)
        for run in set(rx.findall(low)):
            for n, digests in wanted.items():
                for i in range(len(run) - n + 1):
                    h = hashlib.sha256(run[i:i + n].encode("utf-8")).hexdigest()
                    if h in digests:
                        out.add(h[:8])
    return out


def test_name_check_finds_names_inside_longer_words():
    """自检:名称夹在更长的词或句子里也能找到(用一个不在禁用表里的样例验证比对方式本身)。"""
    global _NAME_DIGESTS
    saved = _NAME_DIGESTS
    try:
        _NAME_DIGESTS = (("word", 5, hashlib.sha256(b"probe").hexdigest()),
                         ("cjk", 2, hashlib.sha256("样例".encode("utf-8")).hexdigest()))
        assert len(_found("see x_probe_y and 这是样例文本")) == 2 and not _found("prob 样")
    finally:
        _NAME_DIGESTS = saved


def test_no_external_party_names():
    hits = {name: sorted(f) for name, text in _tracked_text() if (f := _found(text))}
    assert not hits, hits


def test_no_ai_tool_workspaces_or_tool_authorship_notes():
    names = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    assert not [n for n in names if n.startswith((".agents/", ".codex/", ".claude/", ".cursor/"))]


def test_evidence_links_point_to_committed_files():
    """文档里的 Markdown 链接不能指向没有提交的本地检查结果目录(读者打不开,结论无从核对)。"""
    link = re.compile(r"\]\(([^)\s]*\.check-results[^)\s]*)\)")
    hits = {name: link.findall(text)[:3] for name, text in _tracked_text() if name.endswith(".md") and link.search(text)}
    assert not hits, hits
