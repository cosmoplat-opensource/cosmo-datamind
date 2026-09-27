# 演示数据集(demo bundle)

本目录与 `workdir/` 下随仓分发的一组数据,目的是让克隆仓库的人**不必自备数据库、不必调用大模型**
就能把深度问数、本体构建、根因诊断与动作层完整跑一遍。

全部内容均为**合成或脱敏后的示例数据**,不含客户真实数据、真实企业名、内网地址与任何凭据。
本企业统一称「示范××」,往来方(客户、供应商、承运商)统一称「示例+行业+字母」(如「示例汽车B」「示例化工C」),
均为虚构名称;同一往来方的全称与简称共用一个词干,按往来方聚合的结果与原始数据一致。

## 一、数据源

| 文件 | 内容 |
|---|---|
| `examples/demo_metrics.db` | 只读数据底座。108 张表、186,833 行合成制造运营数据(维表/事实表/汇总表),覆盖销售、生产、质量、设备、能耗等业务域 |
| `examples/sample_db.sql` | 最小示例库建表脚本(6 张表)。只想看构建流程、不想拉 11MB 数据库时用这个 |

启动时用环境变量指向数据库:

```bash
DATAMIND_DB=$PWD/examples/demo_metrics.db ./start.sh
```

不设 `DATAMIND_DB` 时程序找仓库根的 `demo_metrics.db`(默认不存在,相关端点会标注"数据库不可用")。

## 二、已构建的本体

`workdir/built_*.json` 是在上述数据库上跑出来的本体版本,可直接在界面里打开、无需重新构建:

| 文件 | 规模 | 说明 |
|---|---|---|
| `built_2d74f0.json` | 108 对象 / 265 关系 | 全库构建结果,规模最大的一版 |
| `built_ba33bb.json` | 19 对象 / 24 关系 | 业务域切片 |
| `built_9c3fd1.json` | 19 对象 / 16 关系 | 业务域切片 |
| `built_8c3354.json` | 16 对象 / 13 关系 | 业务域切片 |
| `built_1f2ee6.json` | 9 对象 / 10 关系 | 小规模,适合逐条看证据链 |
| `built_bd2fec.json` | 10 对象 / 9 关系 | 小规模 |
| `built_qsdemo.json` | 6 对象 / 6 关系 | 问数演示专用 |
| `built_04f7b1.json`、`built_564f1f.json` | 1 对象 | 空构建的边界样例 |
| `built_198e50.json` | 115 对象 / 267 关系 | 示例主库的离线兜底构建结果，可用于展示无外部大模型时的完整图谱 |
| `built_a3d790.json` | 108 对象 / 119 关系 | 示例主库的 LLM 辅助构建结果，保留候选关系与证据判定 |
| `built_be59bc.json` | 109 对象 / 124 关系 | 示例主库的 LLM 辅助构建结果，适合展示语义复核和待确认项 |
| `built_motrix.json` | 142 对象 / 284 关系 | 工业世界模型仿真的工厂本体，用于大图可视化与交互演示 |

`workdir/demo_ir.json` 是演示场景的本体中间表示(IR),`workdir/app_ontology_ir.json` 是应用本体。
这些构建结果本身可直接加载；构建时曾读取的本机上传文件不属于演示包，也不是回放现有结果的必要条件。

`workdir/edits_demo.json` 与 `workdir/edits_built_1f2ee6.json` 是随包提供的两份人工别名示例，
分别演示“产量/日产量/客户”和“产能/产量”等业务称谓如何参与本体检索。其他编辑栈仍作为运行期文件处理。

## 三、运行状态与历史

| 文件 | 内容 | 用途 |
|---|---|---|
| `workdir/ont_chats.json` | 57 个会话、约 114 轮对话 | 打开界面即有历史问答可回看,不必先自己提问 |
| `workdir/action_log.json` | 605 条动作记录 | 动作层与审计留痕的展示数据 |
| `workdir/qa_feedback.json` | 154 条问答反馈 | 意图与使用度回流、反馈驱动改进的输入 |
| `workdir/ont_usage.json` | 11 个本体的使用度统计 | 使用度回流展示 |
| `workdir/ont_rules.json` | 2 条业务规则 | 规则约束与确定性推理引擎的示例输入 |
| `workdir/eval_results.json` | 一次评测结果 | 反幻觉评测台的历史结果 |
| `workdir/skill_compare.json` | 一次技能对比 | 技能选择对比的历史结果 |
| `workdir/qa_skills.json`、`workdir/action_types.json` | 问数技能、动作类型 | 种子配置 |

## 四、技能

`skills_seed/ontology-semi-auto/SKILL.md` 是内置的本体半自动构建技能正文。按 DR-050,
技能正文是构建流程的**真实输入**(会进入提示词),不是展示用的说明文字;修改它会改变构建行为。
没有上游本体引擎时,构建流程从这里读取技能,不会出现技能列表为空。

## 五、不随仓分发的内容

以下是运行期产生或含敏感信息的文件,`.gitignore` 已排除,克隆后由使用者自行产生:

- `workdir/.conn_key`、`workdir/conn_secrets.json` —— 凭据主密钥与加密后的连接凭据
- `workdir/engine_config.json`、`workdir/build_connections.json` —— 本机引擎与数据源配置
- `workdir/uploads.db`、`workdir/uploads_*`、`workdir/chat_uploads/` —— 用户上传文件与派生库
- `workdir/*.log` —— 运行日志
- 除上述两份演示别名外的 `workdir/edits_*.json`、`workdir/*.discarded` —— 编辑栈与改名留底
- `workdir/viz_boards.json` —— 本机看板布局

## 六、克隆后跑测试会看到什么

```bash
python3 -m pytest tests/                       # 单元层,应全绿
DATAMIND_DB=$PWD/examples/demo_metrics.db ./start.sh
DATAMIND_URL=http://localhost:8092 python3 test_all.py   # 集成层
```

当前版本最近一次完整验证为：单元测试 **394 通过**；配齐上游本体引擎的集成测试
**559 通过 / 0 失败 / 0 跳过**。没有上游引擎时，套件会先探测依赖状态，并将仅由该引擎
提供的检查项逐条标为条件跳过；本仓内置的本体构建技能和演示数据库不依赖上游引擎。
具体检查点及依赖关系见 `ARCHITECTURE.md` §5.1。

## 七、关于 diff

`ont_chats.json`、`action_log.json`、`qa_feedback.json`、`ont_usage.json` 会被程序在运行中
读-改-写。跑过演示后 `git status` 会显示这些文件有改动,属正常现象。若不希望它们出现在
工作区状态里:

```bash
git update-index --skip-worktree workdir/ont_chats.json workdir/action_log.json \
    workdir/qa_feedback.json workdir/ont_usage.json
```

恢复跟踪用 `--no-skip-worktree`。
