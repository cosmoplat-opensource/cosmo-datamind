# Cosmo DataMind 代码清理、前后端与部署复核

日期：2026-10-12。工作目录：`cosmo-datamind/`。决定记录：[DR-094](../../specs/decisions/DR-094-behavior-preserving-code-cleanup.md)。

此次清理删除了已确认没有运行引用的代码，未修改业务算法、正常接口响应、页面结构或操作流程。
另外修复一处部署错误分支：环境文件格式错误或数据库文件不存在时，重启请求在停服前被拒绝。
已加载修订源码，实际服务在 `http://127.0.0.1:8092` 正常运行。
现有检查未发现清理导致的功能回归。

## 1. 范围与检查方法

开始时保存了 91 个自有源码文件的内容快照和 SHA-256，共 34,537 行：77 个 Python、8 个 JavaScript、1 个 HTML、2 个 CSS、3 个 Shell 文件。
清单和快照分别在 [baseline_files.json](../../.check-results/cleanup-20261012/baseline_files.json) 与 [before/](../../.check-results/cleanup-20261012/before/)。
第三方前端库、虚拟环境、生成数据和密钥配置未按自有代码清理。

检查采用源码结构与引用扫描、关键执行路径逐行阅读、清理差异逐项审查、自动回归和实际浏览器操作。
77 个 Python 文件均完成 AST 解析，并整理 363 处文件、网络、进程等操作位置供复核；位置清单不是 363 项独立的通过结论。
重点阅读范围包括只读 SQL、来源库上下文、SQL 与本体约束、上传及失败恢复、原子持久化、模型调用与超时、任务状态、动态路由、事件绑定、图谱布局、响应式样式，以及服务启动、停止和重启。
此次没有将“静态扫描完成”写成“全部源码已人工逐字验证”，也不将测试通过解释为不存在任何缺陷。

保留工作开始前已有的 DR-093 研究能力及其他未提交修改。
清理参照本轮开始时的文件快照，而非用 Git 的旧版本覆盖当前工作。

## 2. 逐项清理

| 文件 | 删除内容 | 保持功能的依据 |
|---|---|---|
| `server.py` | `_ground_verb`、`_skill_overridden`、`_skill_body` | 均无运行调用；现有标准关系映射及技能处理已有有效实现 |
| `relation_triage.py` | `_parent_identifier` | 无运行调用；未修改现行关系分流条件 |
| `ui/index.html` | `KINDS`、`EDIT_KINDS`、`LC`、`LOAD_STEPS` | 当前界面未读取这些值；使用中的 `KIND`、颜色及标签函数保留 |
| `ui/index.html` | 空 `sqlDevInit()` 和调用 | 实际 SQL 工作台初始化仍由 `sdInit()` 执行 |
| `ui/index.html` | `.dq-chip` | 当前静态和动态页面均无该类的使用 |
| `ui/modules/qa-ontology.js` | 未使用的 `selected` 计算 | 计算结果未被消费；名称、来源和可查询范围的生成保留 |
| `scripts/service.py` | 重复的 `URLError` 导入和异常分支 | `URLError` 属于已捕获的 `OSError`；异常处理范围保持一致 |

五个生产文件合计净减少 26 行，包含下述部署修复新增的检查。
该数字仅描述本轮增删，不代表性能提升。
完整清理差异保存在 [本轮证据目录](../../.check-results/cleanup-20261012/)，每个文件的前后指纹见 [changed_files.json](../../.check-results/cleanup-20261012/changed_files.json)。

公开辅助函数、Flask 装饰器入口、HTML 内联事件函数和独立 MCP 进程使用的实现保留。
这些代码可能没有普通内部调用，不能仅凭引用数量删除。
未作批量重命名、格式化或跨模块重构。
文档同步修正当前页面与路由计数，增加本次复核入口，并去掉整套单元测试“秒级”的不准确说明；本机最后一次单元运行约 169 秒。

## 3. 部署问题与修复

原来 `restart` 先停止当前 Gunicorn，再读取新配置。
新配置引号不闭合或数据库文件缺失时，服务已经停下，启动却失败。
两个负向用例在修改前确实失败，记录为 [restart_before_fix.log](../../.check-results/cleanup-20261012/restart_before_fix.log)。

现在先解析环境文件、归一目标配置并检查数据库文件，检查通过后才执行原有的停止、启动顺序。
新增三项测试覆盖两种错误和正常重启；需求登记在 `acceptance.json` 的 DEP-01。
正常配置的目标数据库、端口和停止、启动顺序未改变。

实际部署中再次提交两种错误重启请求，均以退出码 1 拒绝。
测试前后 PID 均为 **38291**，数据库健康检查仍通过。
证据：[deployed-restart-preflight.json](../../.check-results/cleanup-20261012/deployed-restart-preflight.json)。
此修复只覆盖上述已验证的基本配置错误，不承诺任意启动失败都能自动恢复旧进程。

已逐项检查 `start.sh`、`wsgi.py`、`gunicorn.conf.py`、服务管理器、Nginx 模板及 CI 配置。
本机实际运行的是回环地址上的单 worker Gunicorn/gthread；Nginx、TLS 和公网身份认证属于模板检查，未作为本机已部署能力。
服务版本摘要为 `c095dc2a252af328e641740df4a45ffd6ac4964395bf75b2bd206a4a9e2dc69f`，状态检查显示源码未变化、数据库就绪。
该摘要按现有服务管理器规则覆盖根目录应用 Python 和 UI 资源；管理脚本自身的修改另由文件指纹和测试记录核对。

## 4. 功能一致性与数据保护

- 清理前后注册的 **137 个路由**一致。
- **34 组响应样本**在相同哈希种子和固定比较时钟下完全一致。固定时钟仅用于隔离时间字段对比较的影响，没有进入运行系统。
- 删除四个私有定义后，`server.py` 与 `relation_triage.py` 的剩余 AST 一致。
- 排除脚本和样式正文后，HTML 页面结构及事件属性一致；有效事件函数另通过浏览器检查。
- 未控制时间及哈希顺序时出现的差异记录在 `contract_differences.json`，未删除或伪装成一致。

对应记录：[equivalence.json](../../.check-results/cleanup-20261012/equivalence.json)。
响应样本是有界比较，不能代表所有输入和外部环境。

示例数据库、本体文件及人工编辑记录共 **42 个文件**的 SHA-256 前后完全一致。
证据：[protected_data_after.json](../../.check-results/cleanup-20261012/protected_data_after.json)。
真实上传验收只创建一个唯一命名的临时 CSV，并通过界面删除自己的测试资产；原有上传资料保留。
未清理用户本体、编辑记录、模型配置、实验结果或历史数据。

## 5. 回归与实际 UI

| 验证 | 最终结果 | 证据 |
|---|---|---|
| Lint、类型、单元、覆盖率 | 全部通过；1252 项 pytest；确定性模块覆盖率 89.0% | [final-unit/](../../.check-results/cleanup-20261012-final-unit/) |
| 合成端到端场景 | 45/45 | [scenarios.log](../../.check-results/cleanup-20261012/scenarios.log) |
| 隔离 HTTP 集成 | 551 通过、0 失败、8 条件跳过 | [integration.log](../../.check-results/cleanup-20261012-final/integration.log) |
| 十轮变序回归 | 10/10 | [strict10.json](../../.check-results/cleanup-20261012-final/strict10.json) |
| 浏览器页面走查 | 64 通过、0 失败 | [test_ui.log](../../.check-results/cleanup-20261012-final/test_ui.log) |
| 浏览器操作 | 50 通过、0 失败、3 条件跳过 | [test_ui_ops.log](../../.check-results/cleanup-20261012-final/test_ui_ops.log) |
| 构建页面 | 10 个用例；1440/768/390 三种宽度通过 | [build-browser/](../../.check-results/cleanup-20261012-final/build-browser/) |
| 实际部署 HTTP | 58/58；检查前后数据表数一致 | [deployed-http.json](../../.check-results/cleanup-20261012/deployed-http.json) |
| 实际上传及失败恢复 | 9/9；无 API 替身；无页面脚本错误 | [live-upload/report.json](../../.check-results/cleanup-20261012/live-upload/report.json) |
| 实际部署页面与布局 | 27 页可达；1162 个内联事件属性中的 130 个调用函数均存在；无页面脚本错误 | [live-views/report.json](../../.check-results/cleanup-20261012/live-views/report.json) |

清理前完整回归保存在 [baseline/](../../.check-results/cleanup-20261012-baseline/)。
清理后的首次综合运行有一项单测失败：测试用已删除的 `KINDS` 常量定位 `KIND` 标签区域。
随后改用仍存在的 `KCOL` 作为区域边界，保留全部原有标签断言，并在清理前页面快照上验证该测试。
首次失败日志保留在 [final/unit.log](../../.check-results/cleanup-20261012-final/unit.log)；修订后的完整单元结果在表中 `final-unit/`。
首次综合运行的 HTTP 与 UI 已通过；其后应用前后端未再修改，只调整测试定位和部署管理器，因此没有重复整套 GUI 测试。

HTTP 的八项跳过来自未提供的可选上游本体引擎；UI 的三项跳过来自隔离测试未配置的 OpenAI 兼容端点。
它们保留为条件跳过，不计作通过。
本机实际界面展示的是当前用户配置，浏览器检查没有修改或测试其付费接口。

实际部署截图包括 27 页桌面首屏，以及构建、问数、图谱、评审、目录、SQL 六页的平板和手机视图，共 **39 张**，已逐张目视检查。
检查内容包括可见文字、按钮与卡片边界、图谱节点文字、宽表内部滚动和页面横向溢出。
在 768/390 两种宽度的十二个页面组合中，未发现页面横向溢出。
宽表在自身容器内滚动，不表示所有列能在手机一屏显示；窄屏导航仍采用原有纵向布局。
首屏截图与这些用例不等于覆盖全部弹窗、滚动区域和交互状态。

图谱最初截图拍摄过早，画布看似空白。
补充检查等待全部节点有有限坐标、节点数与后端一致，并确认视口内存在节点后再截图。
三种宽度均完成 **108 节点、60 关系**布局，保留当前可读缩放方式，未修改图谱实现。
有效截图文件名含 `-ready`；先前截图保留，避免覆盖检查过程。

代表性截图：[桌面构建](../../.check-results/cleanup-20261012/live-views/build-1440-ready.png)、[桌面图谱](../../.check-results/cleanup-20261012/live-views/graph-1440-ready.png)、[手机图谱](../../.check-results/cleanup-20261012/live-views/graph-390-ready.png)、[手机评审](../../.check-results/cleanup-20261012/live-views/review-390-ready.png)、[上传失败后保留数据](../../.check-results/cleanup-20261012/live-upload/upload-error-preserved.png)。

## 6. 验证边界

本轮没有调用付费模型、重跑研究基准、连接外部业务数据库或开展长时间压力测试。
隔离回归覆盖确定性执行和降级路径；实际部署检查覆盖本机 HTTP、只读页面与专用上传用例。
上游引擎实连、公网反向代理和不同模型的生成行为仍需在相应环境中验证。
此次结果支持已检查路径未出现功能回归，不支持全输入、全环境的绝对保证，也不支持问数准确率提升的结论。
修改未提交或推送，工作开始前的其他未提交内容仍保留。
