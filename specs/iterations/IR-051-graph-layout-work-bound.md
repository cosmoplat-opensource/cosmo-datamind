# IR-051 · 交互式图谱布局在同屏上限内的计算量

状态：delivered。关联 [DR-190](../decisions/DR-190-graph-layout-work-bound.md) 和[验收映射](../acceptance.json)中的 GRAPH-LAYOUT-01。

## 验收

- [x] 36 组固定种子的合成场景（含自环、平行连线、方框节点、密集分布和已有弯曲记录）及 2 组同屏上限场景中，连线、弯曲记录和标签位置与修复前的实现逐位一致。
- [x] 坐标含非有限值时退回全量扫描，结果仍与修复前一致。
- [x] 同屏上限场景中，节点比较不超过全量的 15%，线段对比较不超过 2%，标签与线段、障碍物的比较分别不超过 1% 和 2%。
- [x] 布局静止后，选中节点、取消选中和按相同语言重绘不重新布线或排布标签，标签位置不变；节点移动或尺寸变化后重新计算。

## 耗时对比

合成场景为随机分布的节点和连线（种子 11），连线跨越整个画布，比力导向布局后的真实图谱更密集。Node 22，同一台开发机，连续 3 次运行：

| 规模 | 修复前布线 | 修复后布线 | 修复前标签 | 修复后标签 |
|---|---|---|---|---|
| 100 节点 / 200 连线 | 202～214 ms | 53～61 ms | 56～61 ms | 22～24 ms |
| 300 节点 / 600 连线 | 1384～1404 ms | 304～314 ms | 362～368 ms | 52～59 ms |

静止后点击选中不再触发上述计算。浏览器中的实际耗时与机器有关，未单独测量。

## 定向验证

使用开发测试依赖、Node 22、SQLite 隔离存储和 Chromium，在仓库根目录用 Bash 复现。修复前的算法原样保存在 `tests/frontend/interactive-ontology-graph-layout-reference.mjs` 中作为对照。

```bash
python -m pytest tests/unit/test_interactive_ontology_graph_layout.py tests/unit/test_interactive_ontology_graph_ui.py
node tests/frontend/interactive-ontology-graph-layout-bench.mjs
python -m pytest tests/frontend/robotics-interactive-ontology-graph-browser.py
python -m pytest tests/meta/test_acceptance_traceability.py tests/meta/test_doc_consistency.py \
  tests/meta/test_repository_materials.py tests/meta/test_public_content.py
```
