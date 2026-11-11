# IR-024 T08 RED — HTTP、前端控制器与真实浏览器

- 日期：2026-11-10。
- 授权：用户在 T07 完成后要求“继续下一步”，本次完成 T08 测试，不进入 T09 实现。
- 依据：[DR-070](../../specs/decisions/DR-070-robotics-dual-build.md)、[IR-024](../../specs/iterations/IR-024-robotics-dual-build.md)。
- 新增接口细化：[HTTP 与页面测试接口 v1](../../specs/contracts/robotics-design-http-ui-v1.md)。
- 状态：RED，测试待人审；既有实现、已审阅测试和夹具保持不变。

## 覆盖内容

| 分组 | 项数 | 检查内容 |
|---|---:|---|
| HTTP | 56 | 有模型准入与幂等、分阶段操作、请求字段／大小、修订／摘要／阶段／配置／忙碌／队列冲突、运行事件和下载、只读历史、功能开关、旧库读取不迁移、损坏工件、候选与旧 UR 应用资格 |
| 控制器 | 28 | 建模要求提交、会话恢复、结构化确认、重复点击、请求编号、输入保留、迟到响应、轮询单飞与停止、历史读取、显式重试、快照副本，以及浏览器套件必须接入全量检查 |
| 真实浏览器／宿主 | 14 | 同页离线与模型流程、配置错误、空描述等待、阶段确认、完整 JSON/OWL、历史差异、返回编辑、刷新、服务重启、模型失败与重试、启动中断恢复、候选图谱与应用过滤、HTML 文本转义 |

三个浏览器组对照通过：真实页面／服务可用；旧离线构建真实完成；平台 OpenAI 兼容运行时通过本地 HTTP 合成模型完成真实设计核心的一轮持久化。
最后一个对照手动领取运行，仅证明模型 HTTP 夹具及核心链路可用；另一个独立 RED 用例专门要求宿主自动领取，不能用手动执行代替其验收。
浏览器未拦截或伪造应用 HTTP 成功响应；数据库、XML、语义验证、OWL、工件及页面均使用实际实现。
测试模型回复明确为合成材料，不代表真实模型建模质量。

## 执行结果

- 新 HTTP／控制器：84 项，11 通过、73 预期失败、0 跳过、0 收集／夹具错误。
- 真实浏览器／宿主：14 项，3 通过、11 预期失败、0 跳过、0 收集／夹具错误。
- 合计新增 98 项：14 通过、84 失败。
- 既有 T03／T05／T07 定向基线：223 项全部通过，0 跳过，用时 44.55 秒。
- 全仓 ruff、mypy 通过；验收引用与文档本地链接另行检查。
- 已核对 T07 记录中的生产文件和全部已审阅测试／夹具摘要，无变更。

HTTP 主要停在尚缺的设计路由 404、模型 capabilities.build_ready=false，以及设计候选详情仍使用旧运行限制。
控制器主要停在缺少 robotics-design.js，既有构建控制器还未发送 description 或阻止错误配置提交。
浏览器主要停在缺少建模输入／设计区、模型上传仍返回 503、宿主未恢复设计运行，以及图谱仍显示不兼容的行为树入口。
这些失败均保留直接断言，不使用 skip/xfail；完整流程里位于首次失败之后的断言尚未执行，必须在 T09 实现后逐条跑通。

编写测试期间发现平台全局 JSON 防护返回既有字符串 error，Host 拒绝使用 400、Origin 拒绝使用 403。
新测试据实沿用这些全局契约，未要求修改全局防护；设计路由自身仍要求结构化 error.code。
这属于本次未审阅 RED 测试的校准，未修改任何已确认测试。

本阶段刻意保留 RED，未运行约半小时且必然包含这些新增失败的全量套件，也未降低覆盖率门槛。
T09 GREEN 需要接入新浏览器检查并执行完整回归。

## 复现与证据

```bash
env PATH="/home/dyh/.local/opt/node-v22.20.0-linux-x64/bin:$PATH" \
  .venv/bin/python -m pytest tests/unit/test_robotics_design_http.py \
  tests/unit/test_robotics_design_ui.py --tb=short
.venv/bin/python tests/frontend/robotics-design-browser.py \
  --output .check-results/IR-024-t08-red/browser-final
```

- [HTTP／控制器结果](../../.check-results/IR-024-t08-red/targeted-final.log)
- [浏览器／宿主结果](../../.check-results/IR-024-t08-red/browser-final.log)
- [浏览器 JUnit](../../.check-results/IR-024-t08-red/browser-final/results.xml)，同目录各用例保存服务器日志、请求响应记录及最终截图。
- [既有基线结果](../../.check-results/IR-024-t08-red/baseline.log)
- [机器记录与冻结摘要](IR-024-t08-red-results.json)

## 下一阶段

按 [sdd-workflow](../../.agents/skills/sdd-workflow/SKILL.md) 第⑤环节“人 review 测试是否覆盖到位”，本次提交具体测试与 RED 证据供评审。
评审通过后进入 T09，实现公开接口、宿主调度、同页设计工作区、下载与候选分流，并使这些测试转绿。
真实模型质量验证仍属于 T10；本轮未调用用户模型端点、未重启用户服务、未迁移用户数据库。
