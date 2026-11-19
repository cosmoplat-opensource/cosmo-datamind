# 交互式本体图谱修订验证与公开材料检查

以下命令用于复现指定检出版本，不证明其它版本、生产部署或真实机器人执行。

2026-11-19 机器人范围与命名修订基于 `3aa11ecdc41f3b1b0f9689689fa9b3624c9cf8d3` 加本次暂存差异。相关文件 lint（ruff 0.12.0）与类型检查（mypy 1.17.1）通过；相关单元、HTTP、组件及规约检查 165 项通过（35.683 秒），交互式图谱浏览器 9 项通过（36.242 秒），机器人设计浏览器 29 项通过（176.663 秒），共 203 项通过、无跳过。Python 3.10.12、pytest 9.1.1、pytest-xdist 3.8.0、Playwright 1.62.0，使用随包 Node 与 Chromium。

此前验收记录精简及交付状态修订只调整规约、文档与规约检查，未修改产品实现。`tests/meta/` 81 项通过（9.088 秒），无跳过，相关 lint 与类型检查通过；需求编号、验收标准、决策引用及测试映射保持不变。下述复现脚本会执行更新后的规约检查；前述 203 项是本次格式修订前的机器人功能回归记录，本次未重复执行浏览器功能套件。

当前修订的交付状态为 delivered。本次未完成全量回归及全仓覆盖率门禁，不声明全仓通过。尚未提交、推送或部署。原全量回归数字只对应历史快照。逐次日志保留本地，读者可用目标版本及暂存差异按以下命令复现。

## 环境

Python 3.10+，使用项目虚拟环境及固定开发依赖：`pip install -r requirements-dev.txt -r requirements-ui.txt`。完整单元检查包含详情组件浏览器用例，还需 `python -m playwright install --with-deps chromium` 或已安装的 Chrome。Node 必须在测试子进程 PATH 中；项目已有 Playwright 随包 Node 时可复用。浏览器使用 Playwright Chromium 或 `DATAMIND_BROWSER_EXECUTABLE` 指定的 Chrome。不得加载真实模型凭据、生产数据库或用户运行目录。

## 修改后的相关回归

本次范围只运行下列相关检查，不以定向测试的覆盖率代表全仓覆盖率。命令使用隔离目录、已提交演示种子及合成模型响应，不连接真实模型。

```bash
.venv/bin/python - <<'PY'
from pathlib import Path
import os
import sys
import tempfile
import playwright
from scripts.check import isolated_env, run_check

reports = Path(".check-results/interactive-graph-targeted").resolve()
reports.mkdir(parents=True, exist_ok=True)
py = sys.executable
files = [
    "bp_robotics.py", "bp_robotics_review.py",
    "ontology_assets/interactive_ontology_graph_view.py", "scripts/check.py",
    "tests/unit/test_interactive_ontology_graph_projection.py",
    "tests/unit/test_interactive_ontology_graph_http.py",
    "tests/unit/test_interactive_ontology_graph_ui.py",
    "tests/unit/test_interactive_ontology_graph_network.py",
    "tests/frontend/robotics-interactive-ontology-graph-browser.py",
]
targets = files[4:8] + [
    "tests/unit/test_check_parallel.py",
    "tests/unit/test_ontology_detail_ui.py",
    "tests/unit/test_robotics_graph_views.py",
    "tests/unit/test_robotics_modeling_evidence.py::test_frozen_vocabulary_identity_survives_both_source_metadata_aliases",
    "tests/unit/test_robotics_http_review_fixes.py::test_deleted_asset_version_detail_requires_explicit_history_view",
    "tests/meta/",
]
commands = [
    ("lint", [py, "-m", "ruff", "check", *files]),
    ("types", [py, "-m", "mypy", *files, "--no-error-summary"]),
    ("related", [py, "-m", "pytest", *targets, "-ra", "--durations=12",
                 "--junitxml=" + str(reports / "related.xml")]),
    ("graph-browser", [py, "tests/frontend/robotics-interactive-ontology-graph-browser.py",
                       "--output", str(reports / "graph-browser")]),
    ("design-browser", [py, "tests/frontend/robotics-design-browser.py",
                        "--output", str(reports / "design-browser")]),
]
with tempfile.TemporaryDirectory(prefix="datamind-graph-review-") as raw:
    env = isolated_env(Path(raw))
    env["PATH"] = str(Path(playwright.__file__).parent / "driver") + os.pathsep + env.get("PATH", "")
    env["MYPY_CACHE_DIR"] = str(reports / ".mypy_cache")
    results = []
    for name, command in commands:
        if not run_check(name, command, env, reports, results):
            raise SystemExit(1)
PY
```

## 完整回归复现

完整检查包含全仓单元、分层覆盖率、HTTP 集成和浏览器套件，可按以下命令复现。

```bash
NODE_BIN="$(.venv/bin/python -c 'from pathlib import Path; import playwright; print(Path(playwright.__file__).parent / "driver")')"
env PATH="$NODE_BIN:$PATH" .venv/bin/python scripts/check.py --suite all
```

退出码非零即检查失败。默认本地报告目录是输出位置，不是公开证据链接；授权读者可按上述步骤重跑。若分享报告，先审查路径、凭据、私有业务数据与截图，关联目标代码版本、输入指纹、命令、环境、退出码和未执行项。历史审计摘要只描述各自当时版本，不代表当前版本通过。

## 覆盖边界

`tests/meta/test_publication_hygiene.py` 检查已跟踪文件及 Word XML 中的个人目录、工具署名、本地工作流与未公开证据链接。它不能代替凭据审查、合作信息公开授权与暂存差异审查。迁移快照中的 Base64 附件也须检查；其副本保留历史结构和记录身份，合成替换的类型、参数与端口说明同步更新摘要引用。测试与演示中的 `demo::` 类型和端口说明用于合成兼容场景，不是外部平台接口或标定证据。

机器人图谱投影、同屏预算、请求超时、取消与失败后停止轮询、关系搜索分别映射至 `specs/acceptance.json` 的 STV-H01～H05。交互式图谱仅适用于机器人本体，数据本体保留原有展示与交互。

## 来源标识与存量兼容

`${INTERACTIVE_ONTOLOGY_GRAPH_SOURCE_ROOT}` 仅表示图谱参考实现的来源位置，不是运行时配置。来源清单中的 `${ROBOTICS_SOURCE_ROOT}` 是脱敏后的来源标识，历史诊断中的 `${HOME}` 代替个人目录；这些占位符不构成应用运行时依赖，不需要为运行程序配置外部源码目录。
受控词表只修改来源元数据，术语定义不变；文件摘要因此变化，新会话绑定新摘要。已有会话按自身保存的词表及摘要校验，固定版本继续按原始载荷摘要读取，不重写用户历史。演示文件及迁移测试快照的摘要随脱敏副本更新，不应将测试副本写入用户存量资产。

## 执行时间与测试隔离

单元检查默认使用不超过 4 个、且不超过 `os.cpu_count()` 的工作进程，完整测试集和三层覆盖率门槛不变。`--workers 1` 可运行单进程对照。pytest-cov 使用原 `pyproject.toml` 的 source、branch 和 omit 配置并汇总各进程覆盖率。

任务测试仅复用真实构建得到的初始本体字节和报告；每个用例仍经原受理及保存服务获得新资产、运行身份和独立数据库，保存会重新检查来源与完整本体语义。测试中的变更、读取、校验、生成、持久化和回滚均保持原实现。直接验证构建流程的用例继续每次真实构建，不复用其待测结果。

默认报告目录包含 `unit.xml` 的逐用例耗时和 `unit.log` 的慢用例排行。对比性能时记录代码版本、依赖、CPU、进程数、测试数量、覆盖率以及总耗时；只比较相同配置的运行，将准备数据优化与并行加速分开说明。逐次运行记录保留在本地或 CI 附件。

验证优化是否损失既有覆盖时，可用 `coverage.CoverageData` 分别读取同一生产代码版本的两份 `.coverage`，按生产文件比较 `lines()` 和 `arcs()` 集合，确认旧运行中覆盖的行与分支在新运行中仍存在。不同检出目录需先按仓库相对路径归一化；仅比较覆盖率百分比不能替代这一检查。

## 需求与迭代追溯

条目级 `iteration` 保留需求到 IR 的显式映射；需求 ID 不编码 IR，顶层范围也不能定位单条需求。本次恢复原已登记的 102 条映射，例如 `RKG-06` → `IR-019`；未修改既有状态、验收标准和测试映射。相关 `tests/meta/` 82 项通过（9.244 秒，无跳过），lint 与类型检查通过。可按前述隔离环境执行 `python -m pytest tests/meta/ -ra` 复现；本次未运行全量回归或浏览器功能套件。
