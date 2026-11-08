# DR-093 · 名称不透明的库:按数据命名与术语接地;DR-094 取值含义

- **状态 / Status**: accepted(实验分支 exp/catalog,未合入主线)
- **日期 / Date**: 2026-11-07(DR-094:2026-11-08)
- **关联 / Refs**: 实现=`grounding.py`、`workload_concepts.py`(修复轮)、`action_ontology.py`(`skip_if_no_binding`);
  实证=`tests/unit/test_grounding.py`、`tests/unit/test_action_ontology.py`;
  实验=`../08_对比实验/paper_vldb/draft.md` 第 6.2 节、`../08_对比实验/runs/v4/dr093_frozen_version.txt`

## 上下文 / Context

在 60 张表、表名列名均为代码(tb036 / cl0301)且没有数据字典的合并库上,原构建链路的命名只看名称
(词元词典 + 模型按列名起名),67 个对象中 41 个保留代码作名、19 个为「主业务实体记录」一类空泛名;
按构建问题补概念时,模型看到的结构只有代码清单,91 个概念因「列不存在」等原因被数据核验驳回。
另有 7 个制造场景的动作对象被投影进了这个与制造无关的库。

## 决定 / Decision

1. 取值画像(`profile_db`):每列类型、去重数、空值比例、常见取值、数值范围,替代只给列名的结构摘要。
2. 构建问题 evidence 里写明列的子句(「State Special School means cl0069 = 'SSS'」)直接成为「用语 → 列(+ 筛选)」,
   列存在且筛选命中行才保留,命中行数与样例值作为证据(`evidence_groundings`)。
3. 问题与 evidence 里的字面取值在库中反查所在列(`value_groundings`)。
4. 代码名或空泛名的表与列由模型按画像起候选名;名称的依据须引用该表数据中确实出现的取值或用语,
   表代码与纯数字不算依据;通过的名称状态为 candidate 并保留依据,数据字典给出的名称不覆盖(`name_objects`)。
5. 概念未通过数据核验时,带着原因、该表真实结构与已核验的用语重新提议,至多 2 轮;驳回记录写明当初提议的列。
6. 动作注册表里没有任何动作能绑定到本库对象时,构建不投影动作节点(`skip_if_no_binding=True`)。
7. DR-094:evidence 中写明的代码含义(「cl0229 = 'C' stands for running contract」「Male means cl0208 = 'M'」)
   经数据核验(取值在库中出现)后记为取值含义,同时列出该列全部取值,作为使用提醒进入问数上下文。

可用 `DATAMIND_ABLATE=grounding` / `value_semantics` 关闭,用于消融。

## 结果 / Consequences

方法开发只用验证集(合并库正向 61 题)。冻结 1–6 后在测试集(两个方向各 200 题、各 3 次)运行一次:
本体经 EvoOntology 工具使用 25.7 → 36.5(列容忍,+10.8 [+7.1, +14.6]);编译注入 34.4 → 40.3。
DR-094 在验证集上没有提升(24、31 对 26、30),未上测试集:多数问题需要的代码含义不在构建输入中。
