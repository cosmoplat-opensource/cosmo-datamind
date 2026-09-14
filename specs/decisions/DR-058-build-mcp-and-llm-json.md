# DR-058 · 构建能力对外 MCP 化与 LLM 回复解析单一事实源

- 状态:accepted / delivered
- 日期:2026-09-11
- 关联:DR-016、DR-029、DR-050、DR-053、DR-056

## 背景

1. 半自动构建只能从 UI(SSE)驱动:`/api/build/inquire` 是唯一编排入口。动作/指标/概念已有
   MCP 出口(DR-016/055),构建没有——外部 Agent(Claude Code / hermes / 其它客户端)无法
   发起构建,只能看构建结果。
2. LLM 回复解析在多处各自 `re.search(r"\{[\s\S]*\}")` + `json.loads`:贪婪匹配遇到回复前后
   带花括号的文字(模型常见:先说明、再附示例)会取错范围;尾逗号等模型高频小瑕疵直接解析
   失败。而调用方对解析失败只有一条路:换下一引擎或整体回退数据驱动——一次可修复的失败被
   当成引擎故障,浪费一次几分钟的长推理。
3. 提议形状(对象必有 name、关系两端非空)此前靠数据裁决阶段逐条跳过;坏元素被剔除时用户
   只看到计数变少,不知道发生了什么。

## 决定

### 1. LLM 回复 JSON 抽取收成单一事实源 `llm_json.py`

- `extract`:字符串感知的花括号配平扫描(字符串字面量内的花括号与转义引号不参与配平),
  自每个 `{` 起逐个候选尝试;先原样解析,失败后最小修复(删尾逗号、NaN/Infinity→null)再试;
  返回第一个可解析的 dict,取不到返回 None——调用方「换引擎/回退」语义不变。
- `validate_proposal`:本体提议的形状契约。坏元素剔除并计数,统计回报给用户,不把剔除伪装
  成「模型没提」;容器不是列表按空处理,不逐字符迭代。
- `extract_proposal`:抽取+校验一步;有效对象为空按 None,与既有「解析结果不含对象 → 尝试
  下一引擎」语义一致。
- 接入构建链路三处解析点(提议抽取/语义复审/意图解析)。deep-QA 等其余解析点后续迁移,
  不在本 DR 范围。

### 2. 构建 MCP server(`mcp_build_server.py`)

八个工具:三个列表(数据源/技能/构建产物)+ `start_build`(唯一写工具:发起构建,立即返回
作业 id)+ 两个作业跟踪(`get_build`/`wait_build`,单次等待至多 120s)+ 验收复跑(`get_quality`)
+ 人审队列(`review_queue`,只读)。

- 作业模型:wrapper 侧登记,后台线程消费 SSE。**必须有 done 事件才算完成**(DR-056 同规):
  连接中断或半程流一律标 error,不得冒充成功。事件留存最近 200 条,作业登记留存 40 个;
  作业随 MCP server 进程存活,重启后 job id 如实报「未找到」,不假装可查。
- `start_build` 非幂等(重复发起产生新图谱),annotations 如实声明;`source` 归一校验后才拼入
  查询,q 必填限长;构建耗时说明写入工具描述,引导 Agent 用 wait_build 轮询。
- 治理边界与动作层(DR-016)同一哲学,刻意不暴露三类工具:把关系置为 verified/asserted、
  把指标置为 certified、删除图谱。这些是数据裁决与人工评审的专属入口;外部 Agent 接入即
  自动继承「只能发起构建与阅读结论,不能改写结论状态」的规范。

### 3. 修复:全新 workdir 上数据驱动构建必崩(端到端实测发现)

两个子进程调用点(`/api/build/run` 与 `build_inquire` 的 LLM 超时回退)都把
`ACTION_TYPES_F` 无条件传给 quick_build,而 quick_build 对该文件直接 `open()`——
全新 workdir 没有这个文件,FileNotFoundError 让整轮构建无产物。服务端
`load_action_types()` 本就按「缺档/坏档=空列表」容错,quick_build 现改为同口径:
读档失败按无已登记动作处理,构建继续。

## 后果

- 外部 Agent 可以驱动半自动构建,但构建治理(证据裁决/人审/口径确认)全部留在 DataMind
  服务端,与 UI 同一条流水线、同一套验收。
- llm_json 对合法 JSON 的行为与旧路径逐值一致(同一个 `json.loads`);对瑕疵回复多一次修复
  机会;彻底失败时与旧路径相同返回 None → 换引擎。
- MCP 工具回文面向模型阅读:参数名、就绪状态、验收文案(通过/待复核/不通过)与「状态只能
  由人授予」的边界每次随结果原文传达,减少 Agent 误用。

## 验证

- `tests/unit/test_llm_json.py`:配平扫描(字符串内花括号/转义引号)、前缀噪声后取真载荷、
  多块取第一有效、尾逗号与非有限值修复、形状剔除计数、超大批量钳制、空对象判 None;
- `tests/unit/test_mcp_build_server.py`:协议协商/通知不应答/畸形输入不断服、工具声明齐备、
  治理边界(写工具仅 start_build;状态改写与删除类工具不存在)、作业生命周期(SSE 无 done
  即 error、error 事件、running 进度渲染)、渲染语义(数据源就绪/验收文案/人审边界)、
  上游不可达如实报错、日志控制字符消毒;
- `tests/unit/test_quick_build.py`:action_types 缺档按空处理,构建照常产出;
- 端到端(隔离临时 workdir):MCP 单会话 start_build → wait_build → 构建完成(LLM 离线
  兜底路径,6 对象/6 关系/verified 6/验收待复核);get_quality、review_queue、
  list_built_graphs 对真实产物复验通过;
- ruff F/B 零告警;mypy 通过;单元覆盖率保持 ≥81% 基线(TOTAL 86.8%)。
