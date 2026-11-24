# DR-177 · 离线固定版本图谱只规范化一次

状态：accepted。
实现交付状态：delivered。

## 背景

原先打开离线机器人的固定版本交互式图谱时，服务先组装完整 JSON 导出包，在共享 RDF 锁内规范化一次本体。图谱只取走其中的 OWL 字节，随后才检查规模，并在生成视图时再次规范化。普通请求因此重复计算，超过图谱输入上限的请求也会占用共享锁执行第一次规范化。

## 决定

图谱读取和文件导出共用固定版本的工件完整性校验。图谱只读取经过校验的 OWL 字节，在请求自己的 RDF 图上检查三元组数量和带类型的命名主体数量。任一输入超限即返回 HTTP 409（`artifact_integrity`）。通过检查后，在共享锁外规范化一次，再生成视图。完整 JSON 导出继续提供规范化后的事实、来源快照与报告。

图谱规模上限见 [DR-162](DR-162-interactive-graph-limits-and-publication-boundary.md)。验收与复现见 [IR-043](../iterations/IR-043-offline-graph-canonicalization.md)。
