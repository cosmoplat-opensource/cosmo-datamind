# IR-031 · 机器人交互式本体图谱与构建可视化

状态：delivered。原交付日期：2026-11-17。机器人范围与命名修订日期：2026-11-19。

## 目标与范围

落实当前机器人范围：提取交互式本体图谱组件，完成机器人“类别 → 关系／属性 → 实例”的连续可视化，并统一已保存本体的浏览体验。
保留交互式本体图谱的类别圆圈、动态布局、拖拽联动、双击展开实例和详情交互。
现有建模输入、阶段确认、历史修订和固定版本成为可视化的数据来源与操作边界。

机器人设计会话、模型辅助构建的候选版本和既有离线机器人本体共用只读画布。数据本体主图、构建预览、问数高亮、证据及评审沿用原有实现。
构建衔接与详情入口见 [IR-024](IR-024-robotics-dual-build.md) 和 [IR-027](IR-027-ontology-detail-experience.md)。

## 交付

- 设计会话按已保存的修订展示类别、关系与属性；实例来自完成构建的固定版本。视图保留完整 IRI、并集表达式及显式实例归属。
- 多个画布独立管理布局、选择和缩放，离开页面时释放资源。双击类别每批展示 50 个显式实例，空类别显示说明。
- 历史修订只读，画布交互不触发模型或本体写入。保留应用检查、详情修复和任务生成入口。

## 验收

- [x] **STV-R01**：机器人设计按已保存修订依次显示类别、关系与属性、实例，等待和失败不虚构结果，返回修改阶段清除失效内容。
- [x] **STV-R02**：同一来源的增量更新保留有效布局、缩放、选择与展开状态，新增修改删除可辨识，历史与迟到响应不能覆盖当前设计。
- [x] **STV-R03**：交互式本体图谱风格类别圆圈和关系标签可读，拖拽产生力导向联动，固定释放、搜索、缩放及键盘详情入口可用。
- [x] **STV-R04**：双击类别可展开收起全部可访问的显式归属实例，多类型去重，无实例明确说明，大类别分批显示准确数量且可继续访问。
- [x] **STV-R05**：固定版本新旧机器人本体使用同一浏览体验，保留类别关系、实例事实、属性及并集含义，并按版本摘要验证来源。
- [x] **STV-R06**：画布只读操作不触发模型或本体写入，多画布并存与销毁不串状态，错误不会显示另一资产作为成功结果。

验收标准及对应测试见 [STV-R01～STV-R06](../acceptance.json)。交互与规模约束见 [DR-161](../decisions/DR-161-interactive-ontology-graph-visualization.md) 和 [DR-162](../decisions/DR-162-interactive-graph-limits-and-publication-boundary.md)。[原交付截图](../../docs/img/robotics-interactive-ontology-graph-workspace.png) 展示该阶段的界面。

## 验证范围与复现

2026-11-17 的原交付记录覆盖 17 个全量检查阶段。后续范围与命名修订对应开发快照 `88380ef`，接口名称及兼容规则见 [DR-165](../decisions/DR-165-interactive-ontology-graph-naming-and-view-contract.md)。该修订记录了 165 项单元、HTTP、组件及规约检查，9 项图谱浏览器用例和 29 项设计浏览器用例。后一次验证仅覆盖相关回归。

在[项目测试环境](../../README.md#测试)中，以下入口使用合成图谱、隔离服务和 Chromium，覆盖固定版本读取、1440px 与 390px 布局、真实拖拽、分页及画布释放。

```sh
python -m pytest -q tests/unit/test_interactive_ontology_graph*.py
python tests/frontend/robotics-interactive-ontology-graph-browser.py
python tests/frontend/robotics-design-browser.py
```
