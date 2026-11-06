# IR-020 独立演示输入（DR-066）

这组文件只用于用户显式打开的独立演示和离线演示命令。
运行时由这两份 XML 重新构建 OWL，不依赖 Yang 工程路径或 tests 目录。
不得把合成补充材料自动应用到用户上传的本体。

| 文件 | 来源/用途 | SHA-256 |
|---|---|---|
| behavior_tree.xml | IR-016 已登记的 xingweishu.xml 行为树示例 | `e676494a3b409bcb3fa7527fe23229d6df399eb7712bf12f714817d7ae5f4e5a` |
| node_models.xml | IR-016 已登记的 xml_models.xml 节点模型示例 | `c1f3c6b46afb9878e030bcef8910a95af89bdff35682d42e5618da972cc08973` |
| supplement.json | IR-020 合成补充材料；感知阈值 0.8，与公开参数预期一致；不代表现场标定 | `ab67ff80b0c4354df9f7488636571021ef083270ea6efdc265d95a6f8a222432` |

行为树 XML 同时含执行树和 TreeNodesModel 声明。
现有导出契约只输出执行树；节点模型作为编译校验输入，不计入结果预览的执行节点。
演示参数、确认和依据均明确标记为合成/模拟数据；真实模式不能以演示替代适配。

两份 XML 保留登记输入的原始字节，包括 node_models.xml 末尾已有空行；不要为文本格式化改变来源摘要。
