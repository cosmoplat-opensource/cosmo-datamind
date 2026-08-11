# DR-011 · 泛化裁决 v2:研究成果反哺(等值/前缀/复合键/PK 感知 + 三级控制环)

- **状态 / Status**: accepted
- **日期 / Date**: 2026-07-19
- **决策者 / Deciders**: Boss(用户) + Writer(Claude)
- **关联 / Refs**: [[DR-002-multimodal-llm-anti-fraud-build]]、[[DR-010-iof-bfo-alignment]];实证来源=论文实验(Burr-micro 54 场景、Burr-Mondial、示例 判别、对抗库);`server.py _adjudicate_ir/_llm_semantic_review`、`quick_build.py`、`test_all.py O 节`。

## 上下文 / Context
论文阶段的基准实验暴露了裁决器的四个泛化盲区:①无后缀键名(列名=表名,Burr-micro basic 族零候选);②复合外键(Mondial 20/69,单列重叠不可见证);③自然键 schema 的父键不叫 *_id 且父表可含与表同名的非键列(声明 PK 未被查询,同名列优先撞错);④共享域巧合(数据为真、语义为假)只有语义评审能拦(判别实验 9 vs 2,滤除集不相交)。

## 决定 / Decision
1. **连接键多候选循环**:后缀词干 → 等值(列名==父表词干/表名)→ 前缀(Country1→Country)→ 与父列同名的键形列;逐一尝试直至验证(≤6),不再首选即弃。
2. **父列候选序 PK 优先**:单列声明 PK → 同名列 → 裸 id → *_id/_code;复合分支优先父二列复合 PK。
3. **复合键二列联合裁决**:单列未验证时,对两端同名列对做元组重叠 ∧ 父侧成对唯一(NULL 行剔除,CHAR(31) 连接)。
4. **三级控制环**:提议 → **LLM 语义评审**(`_llm_semantic_review`,仅凭 schema 批量判语义引用;离线记 skipped 不臆造)→ 数据裁决;semantic 标注随图边/关系详情透传,数据 verified 而语义 fail 者加"建议人审"注记,不改 verified(其语义=经数据见证)。
5. **kind 扩展 ice**(信息内容实体):抽取白名单+提示词+BFO 映射+UI 样式(RQ2 的 ME↔ICE 边界应对)。
6. quick_build 同步等值候选。

## 后果 / Consequences
- (+) 系统级 Burr-Mondial:改造前 R 1.6%(PK 盲区暴露)→ 迭代三轮(PK 感知、前缀、多候选、PK 优先父列)→ **P 71.7/R 67.2/F1 69.4(双向严苛协议)**,追平论文实验挂具(70.4,有向宽松协议);企业侧回归零劣化(quick_build 示例 265/200 不变;test_all 109/109)。
- (+) 论文"future work: multi-column joint adjudication"已在系统落地(修订论据材料)。
- (−) 语义评审增加构建时延(每 60 关系一次 LLM 调用);离线自动降级。
- **事故与规范**:本轮维护脚本曾以裸 `open("w")` 截断 server.py(条件表达式返 None → write 抛异常但文件已清空;空文件恒过 ast 检查掩盖事故)。经 `~/.claude/file-history` v21 完整恢复并重放。**新规范:一切维护性写文件必须"内存改→ast/断言验证→tempfile+os.replace 原子落盘"**(本 DR 后所有脚本已遵循)。

## v2.1 生产级补丁(2026-07-19 同日)
- 语义评审预算随关系数动态(90+120×批数),修 >60 关系多批被外层 130s 截断;
- `ont_relation` 回退分支(relations 结构图谱)补 semantic/note 透传,新建图谱详情面板徽章不再丢失;
- `build_inquire` SSE 新增 `semantic_review` 步(N 通过/M 存疑;引擎不可用如实报"已跳过");
- 完备度输出增 `semanticReviewed/semanticDisputed` 信息位(不入权重,避免与论文口径漂移),看板同步展示。
回归:test_all 109/109;UI JS 语法通过。

## v2.2(2026-07-19):静态扫描与末梢对齐
- pyflakes 全扫 server.py/quick_build.py/test_all.py:**零告警**(首次全文件静态检查);
- quick_build 前缀键候选(Country1→Country)与 v2 裁决器对齐,示例 回归零漂移(265/200 不变);
- UI 图边 `semantic=fail` 红色点线叠加——三级控制环(提议/语义评审/数据裁决)在画布上完整可视。
回归 109/109;UI JS 通过。

## v2.2 E2E 验收(2026-07-19)
真实 LLM 三级环系统内首跑(build_inquire,示例 源,212s):SSE 步骤序列 intake→intent→gather_evidence→llm_extract→**semantic_review(16 通过·0 存疑)**→verify(15 verified/1 candidate)→narrate;semantic 字段全量落盘,completeness semanticReviewed=16。测试图谱已清理。三级控制环全链(后端→SSE→落盘→记分卡→UI)验收通过。

## v2.3 部署验收 + 架构统一 refactor(2026-07-19)
**部署与全功能点检**:干净部署(:8092),51 只读路由+边界全过;浏览器实测 27 页全渲染、586 按钮处理器全解析、零应用 JS 错误(仅 Chrome 扩展噪音);内联 onclick 的 `$` 调用实测不抛。
**架构 refactor(统一,test-gated)**:①`_open_writable(key,need_objects)` 统一 enrich/reground/maturity 三写端点前奏(守卫+载图+存在性),消 3 处重复;②前端 `$`/`esc`/`jsAttr`/`J` 显式 `Object.assign(window,...)`,杜绝内联处理器 const 作用域跨浏览器隐患;③新增 `ARCHITECTURE.md`(分层/数据流/统一约定/安全/边界)。回归:pyflakes 零告警、三写守卫抽验全过、**test_all 109/109**。

### v2.4 图谱选择器统一分组(实操点检发现)
**实操发现**:本体图谱页选择器把「精选本体/场景样例/构建历史」22 项平铺堆叠,12 个 built_* 构建产物与 3 个精选本体混排、无从区分——不符「更完善」。
**修复(统一,非破坏)**:①`/api/graphs` 每条目加 `cat` 字段(curated/scenario/built),built_* 改按 mtime 降序(最新在前);②前端新增 `graphOptions(gs,label)` 统一分组渲染 `<optgroup>`,三个选择器(图谱/建模工作台/完备度)共用,消散乱堆叠。浏览器实证 3 组渲染(精选3/场景7/构建12)。回归 **109/109**。非破坏:`cat` 为新增字段,JSON 契约向后兼容。

### v2.5 写端点边界加固(第8轮·错误路径审计)
**审计**:对全部写/查端点灌空/超长/深嵌套/坏 op 等边界输入,**无一 500 崩溃**(优雅 400/404/405)。唯一 smell:enrich/reground/maturity 三写端点 `body.get("graph","demo")` **静默默认到 示例 主图**——漏传/拼错 graph 会误改生产本体。
**修复**:把"图谱必填"上收进 `_open_writable`(`if not key → 400 需要指定图谱`),三端点改 `body.get("graph")` 去默认。读端点(sparql)/apply/forge 各有自身路径逻辑,保留其默认不动。UI 两处升级按钮本就显式传 graph,无破坏。回归 +3→**119/119**。
