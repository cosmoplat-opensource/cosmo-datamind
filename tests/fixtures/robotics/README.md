# 机器人兼容夹具 — DR-147 / IR-016 T01

本目录保存脱敏后的兼容测试输入，不代表已经完成 DataMind 集成或真实机器人适配。
`manifest.json` 逐文件记录来源相对路径及当前脱敏副本的长度和 SHA-256；原始来源不随产品分发。

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
兼容测试直接检查输入摘要、XML 结构往返、消费者接受与拒绝，以及严格生成的缺证据路径，不读取历史运行报告。

`demo::PoseQuat` / `demo::PoseRPY` 为合成测试类型，用于覆盖类型识别与未知布局路径，不定义外部平台的结构、单位、字段顺序或执行接口。端口说明已替换为测试说明。
