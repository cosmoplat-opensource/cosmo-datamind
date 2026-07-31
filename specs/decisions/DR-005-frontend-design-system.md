# DR-005 · 前端对齐 iiot-platform 设计系统 + 工业级视觉

- **状态 / Status**: accepted
- **日期 / Date**: 2026-07-07
- **决策者 / Deciders**: Boss(用户) + Writer(Claude)
- **关联 / Refs**: `ui/index.html`(单文件 vanilla-JS SPA);memory `iiot-platform-design-tokens`;平台 DR-005(复用 design-system)

## 上下文 / Context
产品需对齐 `iip.iiot-platform.com/bigdata` 的品类、观感与交互,且面向工业级交付——不能有 emoji 等不专业符号。
技术上选单文件 SPA(无构建、断网可用、本地内置 ECharts/G6)。

## 决定 / Decision
- **设计 token**:主色 `#4A5FF3`、次蓝 `#409EFF`、页底 `#F7F8FC`、描边 `#E4E7ED`、圆角 4px、字体 Helvetica Neue/PingFang SC/Microsoft YaHei;标签 pill 9999px(灰/靛蓝/琥珀三态)。对齐 design-system/iiot-platform。
- **导航**:平台同款品类分组多级折叠(11 组 26 页);顶栏品牌 + 引擎徽章(中性名)+ 用户卡。
- **工业级视觉**:**全站禁用彩色 emoji**;章节标题用 3px 靛蓝 accent bar,品牌/欢迎用单色 SVG 图标,状态用纯色圆点(绿=就绪/琥珀=需驱动/灰=空)+ 单色 ✓/✕;保留 → ↔ ▾ ✕ 等专业字形。
- **技术栈**:单文件 `ui/index.html`(HTML+CSS+vanilla JS),图表 `vendor/echarts.min.js`,本体图谱 `vendor/g6.min.js`,**本地内置不依赖外网 CDN**;建模工作台画布用 ECharts,本体图谱用 G6(dagre/力导向/环形/辐射四布局)。
- **可视化组件**:数据可视化(ECharts 折/柱/面积/饼/表/指标卡 + 数据看板)、本体图谱(G6 四色 + verb 边)、本体构建内联预览(复用 g6render)。
- **可达性/响应式**:可点 div 经 `a11yScan` 补 tabindex/role=button + 全局 Enter/Space 键盘委托;`@media(max-width:820px)` 移动端栅格 `1fr !important` 覆盖内联栅格。

## 后果 / Consequences
- (+) 观感与 iiot-platform 一致、工业级整洁;断网可用、零构建。
- (+) 键盘可操作、移动端可用。
- (−) 单文件 `ui/index.html` 体量大(~145KB),靠约定(esc()/jsAttr()/统一 `mk()`/`g6render()` 助手)维持可维护性;实例泄漏靠显式 dispose/destroy + 代际守卫防护。
