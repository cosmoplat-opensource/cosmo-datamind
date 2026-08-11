# DR-039 · 反幻觉评测台(把反幻觉主张变成数字)

- **状态 / Status**: accepted
- **日期 / Date**: 2026-07-31
- **决策者 / Deciders**: Boss(用户) + Writer(Claude)
- **关联 / Refs**: [[DR-002-multimodal-llm-anti-hallucination-build]]、[[DR-035-unified-adjudication-core]]、[[IR-009-eval-and-definition-quality]];实证=`hallucination_eval.py`、`benchmark/adversarial_fk.py`、`tests/unit/test_hallucination_eval.py`

## 上下文 / Context

DAO 的核心卖点是「LLM 提议、数据裁决」——即便 LLM 幻觉造边,数据裁决这道防线也会拦住。
但此前这是**规范主张**,不是**被测数字**:没有任何组件量化「裁决器 verify 真边、拒假边的能力」。
论文复审(M5)亦指出:干净库上 LLM 恰未幻觉,防线收益从未被激活量化。

## 决定 / Decision

建**确定性、离线**的反幻觉评测台,把防线能力变成可回归指标:

1. **带标签基准**(`benchmark/adversarial_fk.py`):合成库 + 一批候选关系,每条带真值标签,
   覆盖真 FK 与植入假边——代理键碰撞、方向反、共享维度键偶合、以及 DR-038 探针
   (高基数部分重叠真 FK / 低基数同名巧合假边)。数据用取模确定性构造,无随机。
2. **评测口径与生产完全一致**:`hallucination_eval` 走**同一** dao_core 裁决核
   (重叠 + 父键唯一 + `name_ok` 命名校验 + `should_reverse` 方向抑制),
   测的正是 quick_build 实际用的那道防线,不另造一套。
3. **指标**:混淆矩阵 + 精确率/召回/F1 + **幻觉泄漏率**(假边被判 verified 的比例,越低越强)。
4. **门槛可注入**:`evaluate(theta=, min_distinct=, adaptive=)` 支持门槛对比实验,供 DR-038 用同一台称量。

## 后果 / Consequences

- (+) 反幻觉从主张变数字:干净子集基线 **泄漏率 0 · 召回 1**(真 FK 全 verify、假边全拒)。
- (+) 评测台即刻产出价值:**当场抓出我自己基准的标注错误**——把真的状态码 FK(`orders.status→ref_status`,
  名词根同 status)误标为假;裁决器正确 verify 之,台子据此揪出错标。这证明台子在独立判真伪。
- (+) 揭示**数据裁决的固有极限**:`orders.status→ref_status`(真)与 `orders.level→storage_bins.level`(假)
  是「孪生案例」——同为低基数(distinct=3)、100% 含入、父键唯一、命名校验放行,
  **裁决器实际使用的信号逐项相同却一真一假**;唯一有差异的 `name_score`(真 1 / 假 2)
  还**指向相反方向**:假边因两侧列名全等反而得分更高。
  这类泄漏非门槛可调,需外部语义(词典/LLM)或本就该送人审。台子把这条边界量化摆明。
- (−) LLM 幻觉率(提议端)仍需引擎在线才能测;本台先测**裁决端**防线(离线可跑),
  提议端幻觉率待接引擎或 mock 后补(IR-009 后续)。
