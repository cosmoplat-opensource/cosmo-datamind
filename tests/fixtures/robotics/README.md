# 机器人兼容夹具 — DR-065 / IR-016 T01

本目录冻结原始输入，不代表已经完成 DataMind 集成或真实机器人适配。
`manifest.json` 逐文件记录原项目相对路径、长度和 SHA-256，源目录为用户指定的 yang_implementation/归档。

| 路径 | 用途 |
|---|---|
| sources/xingweishu.xml、xml_models.xml | 本轮新构建的输入，固定 UR 模板及节点模型 |
| sources/ur_skill_shapes.ttl | 原构建侧规则基线，不是任意上传规则的许可 |
| owl/authoring.owl | 构建侧存量本体，缺少当前 agent 发布契约 |
| owl/agent_real.owl | agent 可加载的真实本体，仍缺少部分任务证据 |
| owl/agent_demo.owl | 独立演示本体，含模拟资料，仅供兼容对照 |
| tasks/legacy_request.json | 原构建侧结构化任务样例 |
| tasks/public_parameters.json | 六个公开配置的历史样例，不是标定证明 |

测试从仓库相对位置读这些文件，输出和变体写入临时目录，不修改夹具。
agent_demo.owl 不能替代新构建产物，以伪造构建到生成的链路通过。
基线复测及原代码摘要见 docs/audits/IR-016-baseline-results.json，复测工具仅用于显式提供的原归档，不是运行时依赖。
