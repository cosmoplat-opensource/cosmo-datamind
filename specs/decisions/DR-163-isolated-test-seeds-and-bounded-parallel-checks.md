# DR-163 · 独立测试数据与有界并行验证

状态：accepted。

## 验证目的

完整测试集继续覆盖真实构建、语义校验、固定版本读取、持久化、回滚、幂等及错误路径。保留现有断言、可选组件跳过条件和整体 74%、核心 87%、路由 61% 的覆盖率门槛。生产代码与校验实现不变。

## 决策

任务测试的公共准备数据由本轮真实构建生成，每个测试通过原受理和保存服务创建独立数据库、资产身份、运行记录及字节文件；只复用真实构建产出的本体字节及其报告，保存时仍重新执行完整语义校验；不复制数据库中的资产和运行身份，仅复用构建完成的初始条件，不缓存测试操作中的校验结果。不改动直接验证构建与发布行为的原有测试入口。变体在独立图副本上构造，已有目录使用原构建路径，禁止覆盖现存数据。失败的种子生成不得留作成功缓存。

现有本地并行命令构造采用固定版本的 pytest-xdist，保留不超过 4 个工作进程及单进程对照的独立回归。每个进程保持独立临时环境，pytest-cov 汇总全部进程的覆盖率，失败仍使检查非零退出。新增依赖仅用于开发验证；pytest-xdist 3.8.0 使用 MIT 许可证并支持 Python 3.9 及以上，与当前 Python 3.10/3.11 环境兼容。现有 PyYAML 的 CI 配置检查配套固定 types-PyYAML 6.0.12.20261113（Apache-2.0，Python 3.10+），保留 YAML 结构解析和类型检查，不新增忽略规则。

保留全套用例，记录 JUnit 逐项耗时与最慢用例，以完整运行实际结果判断优化效果。原始日志和性能取样保留在本地检查目录或 CI 附件，不逐轮提交开发过程报告。当前 CI 以 master 的分组结构为准：`unit_tests` 使用 8 个矩阵分组，各自安装 Python 测试依赖、Node、Playwright 和 Chromium，执行分组并上传覆盖率数据；每组 CI 预算 70 分钟，脚本内分组进程预算 55 分钟。`unit` 依赖全部分组成功，失败时在下载和汇总前退出；按分组总数下载完整覆盖率输入，执行 lint、类型检查及三层覆盖率门槛，CI 预算 15 分钟。普通完整单元入口与 master 一致使用 coverage 执行全套 pytest，脚本内预算两小时；汇总入口只合并覆盖率，不重新运行全套测试。

## 验收

需求 PERF-T01～T04 与测试映射登记于 `specs/acceptance.json`：副本隔离、真实校验保留、覆盖率汇总及并行失败传播。实际耗时必须说明命令、进程数、代码版本、运行环境、通过/跳过数量及覆盖率，不以用例数量估算性能。

参考：[pytest-xdist 分发](https://pytest-xdist.readthedocs.io/en/latest/distribution.html)、[pytest-cov 汇总](https://pytest-cov.readthedocs.io/en/latest/xdist.html)。

## CI 契约修订（2026-11-19）

合并后旧检查仍在汇总任务中查找 Chromium 安装步骤。依赖检查改为核对实际执行测试的 `unit_tests`，并保留汇总任务的 Python 依赖检查；同时检查分组范围、失败后继续收集各组结果、覆盖率上传、完整输入下载及失败拦截。
分组与汇总入口的组件测试通过进程边界记录调用，验证分组失败返回非零、汇总失败停止后续门禁、成功汇总保留三层覆盖率检查且不重跑测试；既有真实并行子进程及分支覆盖率回归不变。CI 配置、分组算法、检查脚本及覆盖率门槛均不修改。

本次 CI 契约检查的交付状态为 delivered，PERF-T04 同步更新；相关回归 92 项通过，无失败、无跳过，相关 lint、类型检查通过。验证基于 `153c0a1c963aa33bd4a1ef25394f758b64f29611` 加本次暂存差异，Python 3.10.12、pytest 9.1.1，Node 使用 Playwright 随包运行时；本次未重新执行全量回归，也未验证远端 GitHub Actions 实际运行。

复现时将 Node 加入当前命令 PATH，执行：

```bash
NODE_BIN="$(.venv/bin/python -c 'from pathlib import Path; import playwright; print(Path(playwright.__file__).parent / "driver")')"
env PATH="$NODE_BIN:$PATH" .venv/bin/python -m pytest tests/unit/test_check_parallel.py tests/unit/test_check_isolation.py tests/meta/ -ra
.venv/bin/python -m ruff check tests/unit/test_check_parallel.py
.venv/bin/python -m mypy tests/unit/test_check_parallel.py --no-error-summary
```
