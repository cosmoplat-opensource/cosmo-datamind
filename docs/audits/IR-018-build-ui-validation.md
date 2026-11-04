# IR-018 · 机器人本体构建 API/UI 验证记录

## T01 规划与代码盘点

日期：2026-11-04。用户在了解 IR-017 演示流程与验收内容后回复“可以进行下一步了”。
IR-017 已关闭为 delivered；本次建立 [IR-018](../../specs/iterations/IR-018-robotics-build-api-and-ui.md) 的 Goal、Deliverables、Tasks 与 RBU-01～10 草案。
状态：待本轮范围/验收确认，尚无 RED/GREEN 实施或新 UI 联动结果。

### 已核对的代码依据

| 接入点 | 实际发现 | 对计划的影响 |
|---|---|---|
| `domain_runs/store.py:create_run / _snapshot / finish`；`ontology_assets/storage.py:RUN_SCHEMA` | 运行要求已有资产版本，generated 与候选工件绑定 | 构建前没有输出版本，必须新增来源输入型运行及独立输出引用；不能调用现有生成入口来假装构建 |
| `domains/robotics/importer.py:build_graph` | 只支持 ur_pick_place，从明确 XML 文件读取并记摘要 | 配方和 profile 在前端明确限定，上传按字节快照进入构建 |
| `domains/robotics/source_validation.py` | DTD/实体拒绝、节点/端口/模型检查已存在，部分定位为普通异常文本 | 在保留 ValueError 兼容的前提下增加可供 API/UI 使用的结构化问题 |
| `domains/robotics/publisher.py`；`ontology_assets/store.py` | OWL 协议封装与资产发布状态是不同操作 | 本轮保存结构检查后的 candidate，不自动授予正式发布或任务适配 |
| `domain_runs/offline_demo.py` | 自动加入演示资料/发布、启动中断子进程是离线场景安排 | 正式 API 不调用演示脚本；计算进程只运行受支持的构建服务 |
| `server.py` 的 _csrf_guard、_json_object_guard、MAX_CONTENT_LENGTH、build_upload、graphs、JOBS | 新蓝图可复用应用级保护；旧上传及旧作业有独立存储行为 | 新上传/构建独立边界，旧返回形状及行为保留；HTTP 只返回逻辑标识 |
| `gunicorn.conf.py` / `wsgi.py` | 单 worker/gthread，但有 worker 回收 | 唯一执行器所有权与启动恢复需显式管理，不能把“单 worker”当作永不发生进程交接 |
| `ui/index.html` 的 #build、#library、#jobs、routeHash；`ui/modules/` | 原生 JS 和 hash 深链；旧本体库依赖 /api/ont/forged，问数按钮为数据领域设计 | 增量新增模块、配方与机器人详情，旧条目只读引用并走原深链，机器人不继承问数操作 |
| `tests/frontend/build-browser.py` | 即便传 --url，核心构建 API 仍有路由拦截夹具 | 新增真实后端浏览器测试，旧浏览器通过结果不能代替本轮联动 |
| `scripts/check.py` | 隔离工作目录，统一 lint/types/unit/coverage/HTTP/UI 检查 | 本轮新联动必须接入 --suite all，继续使用隔离服务和原质量门槛 |

### 需要通过本轮规约固定的行为

1. 来源输入型运行在输出版本尚不存在时即可受理，成功绑定输出版本；旧版本输入型生成及原 manifest 保持兼容。
2. 候选的结构兼容通过不等同于人工发布、业务证据齐全或机器人执行成功。
3. 首版有界队列、唯一宿主、计算子进程超时及重启语义，在 T02/T04 转为可执行测试。
4. 本体构建、本体库和作业中心的新操作必须在同一 IR 中有真实后端浏览器验收，不止静态页面或接口测试。
5. 统一目录对旧数据图谱使用明确的只读引用，不凭空分配版本或在 GET 中迁移用户数据。

### 计划批次与证据状态

| 批次 | 内容 | 当前状态 |
|---|---|---|
| T02/T03 | 构建服务、来源型运行、迁移、候选提交和问题定位 | 测试与实现均未编写 |
| T04/T05 | HTTP、队列、超时、恢复、重试、查询/下载与开关 | 测试与实现均未编写 |
| T06/T07 | UI 行为与真实浏览器联动 | 测试与实现均未编写 |
| T08 | 统一回归、操作说明、实际下载/摘要/截图和交付审查 | 待后续任务 |

acceptance.json 本轮条目仅在 planned_requirements 中登记拟覆盖文件，不引用尚不存在的 pytest 函数。
本次只验证文档结构、映射及已交付代码/测试摘要，没有重新跑未变更实现的完整回归。
按 [SDD skill](../../.agents/skills/sdd-workflow/SKILL.md) 第③步“人填 Goal（这一轮范围）和 Acceptance（什么叫做完）”，本 IR 的新增范围与验收草案需确认后进入 T02。

### T01 文档检查结果

- `.venv/bin/python -m pytest tests/meta -q`：21 项通过，退出码 0。
- `git diff --check`：通过。
- RBU-01～10 与 IR 验收原文逐项一致，编号无重复；已有 requirements 及其可执行测试引用与本次修改前完全一致。
- 新测试文件只列于 planned_test_files，未把未编写的测试登记为已执行覆盖。
- Proposal 与规格索引已同步本地阶段状态；这些文件此前已有未提交内容，沿用原工作区状态，不将其历史改动混入本次 T01 提交。
- 本次未修改生产实现或已有测试；未重新执行完整回归，未产生新后端/UI 的通过证据。

## T02 · 范围确认与首批失败测试（待覆盖审查）

日期：2026-11-04。
用户在了解请求保护、构建调度、RunStore 更新机制以及本轮 UI/可视化边界后回复“已确认，进行下一步”。
据此将 Goal / Acceptance 标记为本轮已确认，新增以下测试；此消息不提前替代本批测试覆盖 review。

### 本次测试覆盖什么

| 文件 | 项数 | 主要验证内容 |
|---|---:|---|
| `tests/unit/test_domain_build_runs.py` | 35 | 构建前无本体版本也能排队；来源角色/摘要/参数固定；缺失、重复、非法输入及来源损坏拒绝；事件、显式恢复和重试；候选/报告/manifest 原子提交；文件写入、摘要回读和最终事务提交故障回滚；并发完成只允许一个赢家；构建与原生成完成契约相互隔离 |
| `tests/unit/test_robotics_build_pipeline.py` | 19 | 实际调用 XML 构建内核和消费验证；按原 XML 独立核对 25 个调用、节点模型和端口；不调用 demo 补证据或自动正式发布；纯计算不提交持久数据；10 类非法来源返回真实定位；原 ValueError 捕获兼容；候选显式发布后真实任务仍缺证据，不生成 XML |
| `tests/unit/test_domain_build_migration.py` | 7 | 从真实冻结的 schema 2 还原旧库；迁移保留旧行、文件、manifest、发布状态和外键；升级最终 commit 失败回滚；并发打开安全；未知 schema 拒绝；新进程只读查询不触发运行恢复 |

辅助文件：`tests/build_pipeline_support.py`；旧库夹具及出处见 `tests/fixtures/domain_storage/README.md`。
成功输出的核对不只看 built 标签，还会验证实际 OWL 能被消费侧加载、图中节点/端口与源 XML 相符、版本与工件字节/摘要一致、报告与 manifest 引用正确。
新增负向场景包括“拿另一份来源成功构建出的合法 OWL/报告提交到当前运行”，必须因来源不匹配而拒绝。
事务提交故障仅在候选版本、终态和三类工件已写入同一事务后触发，防止在初始化阶段提前抛错而未测试真正的部分提交风险。

### 本次还没有测试什么

- HTTP 上传大小/返回码、request_id 幂等、容量限制、超时终止、宿主独占与 worker 交接：T04。
- 页面表单、目录/详情/下载、刷新与真实后端浏览器重启联动：T06。
- 本批用显式调用模拟运行生命周期，不宣称后台执行器或 UI 已经存在。
- 未连接 LLM、ROS 或真实机器人；候选构建成功不代表任务证据齐全或现场执行成功。

### 实际结果

执行：

```bash
.venv/bin/python -m pytest tests/unit/test_domain_build_runs.py tests/unit/test_robotics_build_pipeline.py tests/unit/test_domain_build_migration.py -v --tb=short --junitxml=.check-results/ir018-t02/red.xml
```

- 收集 **61 项**；**4 项通过、57 项失败、0 收集错误、0 跳过**；退出码 **1**。
- 主要失败原因：create_build_run / finish_build 及 compute_build 入口尚未实现；来源异常尚无结构化 issues；schema 仍为 2，尚未升级。
- 4 项通过是既有 generated 必须附带候选的限制、冻结夹具完整性以及 RunStore/AssetStore 拒绝未知 schema；不表示新构建功能已经完成。
- 多数新增用例当前在缺少入口处失败，后续行为断言要等 T03 实现后才能执行，不能把 RED 数量当作全部行为已被验证。
- 旧能力针对性回归 **74 项全部通过**：运行记录 20、工件 22、存储完整性 7、机器人语义 9、发布契约 16。
- 元测试 **21 项通过**；新增 4 个 Python 文件 ruff、mypy（follow-imports=silent）通过。
- RBU-01/02/03/04/09 已映射到真实函数，但只是本批覆盖；其他条目保持 scope-confirmed-tests-planned，未虚报整条验收通过。
- 153 个本次记录摘要的已有生产/测试文件均未修改；新增测试与夹具摘要见 [机器可读记录](IR-018-t02-red-results.json)。
- 未执行全量 `--suite all`，本次处于预期失败的 RED 阶段，不宣称全量通过；必要回归在 GREEN/最终交付继续执行。

原始日志位于 `.check-results/ir018-t02/`，其摘要已登记在 JSON 审计记录中。
本次提交只含新增测试/夹具和本轮规格/审计；Proposal 与索引已同步本地阶段状态，保留其原有未提交内容。

### 下一步

按 [SDD skill](../../.agents/skills/sdd-workflow/SKILL.md) 第⑤步的明确要求“人 review 测试是否覆盖到位”，本批等待用户审查覆盖内容。
确认后执行 T03：只修改实现使这 61 项测试转绿，不修改已审测试或重生成旧库夹具来绕过失败。
