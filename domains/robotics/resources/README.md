# UR v2 发布规则 — DR-060 / DR-065

`ur_pick_place_v2.ttl` 是冻结夹具 `tests/fixtures/robotics/owl/agent_real.owl` 中
`https://example.org/ur-skill#validationShapes` 字符串的原始 UTF-8 字节。
SHA-256：`b57e61b049b0a521cf1812987a24522178412867e6d358571a8a0658c6021ae2`。
对应协议：`ur-pick-place-owl-v2`。

这份规则与构建侧的 `ur_skill_shapes.ttl` 不同；不可互换，也不能修改摘要绕过兼容检查。
为保持已审规则指纹，原始 TTL 不添加注释或重新排版，关联 DR 在本文件记录。
发布器显式调用时读取这份随包资源并验证摘要；消费加载器只检查 OWL 内嵌规则，不读取本文件或来源夹具。
构建与消费共同使用 profiles/ur_pick_place.py 的条件定义，消费时额外施加原 agent 的条件绑定约束。
