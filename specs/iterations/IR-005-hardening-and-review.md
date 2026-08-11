# IR-005 · 安全加固 + 工业化 + 复审收敛(持续)

- **状态**: delivered(持续迭代)
- **关联**: [[DR-006-security-model]]、[[DR-003-runtime-neutral-naming]]、[[DR-005-frontend-design-system]]、[[DR-007-url-hash-routing]];`AUDIT.md` 第 1–47 轮

## 目标 / Goal
通过多轮「事实核对 + 独立评审 agent + 读码复核」收敛缺陷,达工业级:去 emoji、隐藏引擎库名、URL 寻址、补安全洞、修资源泄漏/竞态、补可达性。

## 交付 / Deliverables
- [x] **安全**:修高危路径穿越 LFI/写穿越(`_bad_gkey`);SPARQL 禁 FROM 外链;Turtle 兜底转义补 C0;复核确认文件服务/命令/CSRF/上传攻击面收敛(见 [[DR-006]])。
- [x] **工业化视觉**:全站去彩色 emoji → accent bar + 单色 SVG + 状态点(见 [[DR-005]])。
- [x] **对外中性引擎名**:UI 与执行记录不暴露 hermes/claude-code/openclaw(见 [[DR-003]])。
- [x] **URL 寻址**:26 子页 `#<page>` + 深链(见 [[DR-007]])。
- [x] **健壮性**:SSE 序列化崩溃防护、缓存上传指纹、按图谱选源生效、中止复位;数据可视化代际守卫防串卡、G6/ECharts 实例 dispose 防泄漏;新页键盘可达性(a11yScan);对话面板动态高度。
- [x] **内容一致**:数字与真库/IR 三方核对;派生/复合指标血缘展示业务口径;数据质量加扫描基数;知识库/README 去引擎名后语义自洽。
- [x] **深度问数时序口径**:洞察 `narrative_llm` 采样改「首3+末7」(原 `rows[:6]` 只看最早、漏最新期骤降、与图表矛盾);数据预览 `dqTable` 改首尾采样;即时问数透明展示聚合口径 `agg_note`(比率取月均近似)——洞察/图表/预览三者以「末尾为最新期」统一。

## 任务 / Tasks
1. 每轮:静态整校(计数/onclick/API 引用)+ 派评审 agent(深度问数/新模块/编辑导出子系统)+ 读码复核后才修。
2. 每修复补 `test_all.py` 断言锁定(路径穿越、SPARQL FROM、只读拒写、默认参数真实性等)。

## 验收 / Acceptance
- `test_all.py` 93 断言全通过;控制台零错误;浏览器实测各修复项。
- `AUDIT.md` 逐轮记录根因、修复、验证(47 轮);安全洞有回归断言锁定。
- 复核判定「无需改」项如实记录(避免过度改动:SPARQL 软超时、coverage 22 模块口径等)。
