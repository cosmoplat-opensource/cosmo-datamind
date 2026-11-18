# Studio 修订验证与公开材料检查

每次验证先记录目标版本 `git rev-parse HEAD` 和 `git status --short`，未提交改动同时保留差异或输入 SHA-256。以下命令用于复现目标检出版本，不证明其它版本、生产部署或真实机器人执行。

## 环境

Python 3.10+，使用项目虚拟环境及固定开发依赖：`pip install -r requirements-dev.txt -r requirements-ui.txt`。完整单元检查包含详情组件浏览器用例，还需 `python -m playwright install --with-deps chromium` 或已安装的 Chrome。Node 必须在测试子进程 PATH 中；项目已有 Playwright 随包 Node 时可复用。浏览器使用 Playwright Chromium 或 `DATAMIND_BROWSER_EXECUTABLE` 指定的 Chrome。不得加载真实模型凭据、生产数据库或用户运行目录。

```bash
# 仅为当前命令选择已有运行时，不修改系统 PATH。
NODE_BIN="$(.venv/bin/python -c 'from pathlib import Path; import playwright; print(Path(playwright.__file__).parent / "driver")')"
env PATH="$NODE_BIN:$PATH" .venv/bin/python scripts/check.py --suite unit
# 标准检查脚本会建立隔离运行目录，读取已提交的演示种子。
# 完整产品回归含 HTTP、严格验证和各浏览器套件：
env PATH="$NODE_BIN:$PATH" .venv/bin/python scripts/check.py --suite all
# Studio 浏览器定向回归使用临时持久化服务及合成模型响应：
.venv/bin/python tests/frontend/studio-robotics-browser.py
.venv/bin/python scripts/scenario_bench.py
```

退出码非零即检查失败。默认本地报告目录是输出位置，不是公开证据链接；授权读者可按上述步骤重跑。若分享报告，先审查路径、凭据、私有业务数据与截图，关联目标代码版本、输入指纹、命令、环境、退出码和未执行项。历史审计摘要只描述各自当时版本，不代表当前版本通过。

## 覆盖边界

`tests/meta/test_publication_hygiene.py` 检查已跟踪文件及 Word XML 中的个人目录、工具署名、本地工作流与未公开证据链接。它不能代替凭据审查、合作信息公开授权与暂存差异审查。迁移快照中的 Base64 附件也须检查；其副本保留历史结构和记录身份，合成替换的类型、参数与端口说明同步更新摘要引用。测试与演示中的 `demo::` 类型和端口说明用于合成兼容场景，不是外部平台接口或标定证据。

机器人图谱投影、同屏预算、请求超时、取消与失败后停止轮询、关系搜索分别映射至 `specs/acceptance.json` 的 STV-H01～H05。数据本体 Studio 接入尚未实现。

## 来源标识与存量兼容

来源清单中的 `${ROBOTICS_SOURCE_ROOT}` 是脱敏后的来源标识，历史诊断中的 `${HOME}` 代替个人目录；这些占位符不构成应用运行时依赖，不需要为运行程序配置外部源码目录。
受控词表只修改来源元数据，术语定义不变；文件摘要因此变化，新会话绑定新摘要。已有会话按自身保存的词表及摘要校验，固定版本继续按原始载荷摘要读取，不重写用户历史。演示文件及迁移测试快照的摘要随脱敏副本更新，不应将测试副本写入用户存量资产。

## 执行时间与测试隔离

单元检查默认使用不超过 4 个、且不超过 `os.cpu_count()` 的工作进程，完整测试集和三层覆盖率门槛不变。`--workers 1` 可运行单进程对照。pytest-cov 使用原 `pyproject.toml` 的 source、branch 和 omit 配置并汇总各进程覆盖率。

任务测试仅复用真实构建得到的初始本体字节和报告；每个用例仍经原受理及保存服务获得新资产、运行身份和独立数据库，保存会重新检查来源与完整本体语义。测试中的变更、读取、校验、生成、持久化和回滚均保持原实现。直接验证构建流程的用例继续每次真实构建，不复用其待测结果。

默认报告目录包含 `unit.xml` 的逐用例耗时和 `unit.log` 的慢用例排行。对比性能时记录代码版本、依赖、CPU、进程数、测试数量、覆盖率以及总耗时；只比较相同配置的运行，将准备数据优化与并行加速分开说明。逐次运行记录保留在本地或 CI 附件。

验证优化是否损失既有覆盖时，可用 `coverage.CoverageData` 分别读取同一生产代码版本的两份 `.coverage`，按生产文件比较 `lines()` 和 `arcs()` 集合，确认旧运行中覆盖的行与分支在新运行中仍存在。不同检出目录需先按仓库相对路径归一化；仅比较覆盖率百分比不能替代这一检查。
