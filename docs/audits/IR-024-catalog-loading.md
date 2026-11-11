# IR-024 本体库加载修复 — DR-070

2026-11-11 用户反馈已完成实例构建，但本体库显示“此领域暂无登记条目”。
实际 extract_abox 已完成且版本已保存；现有库有 5 个登记资产、30 个版本。
目录会逐个进行历史机器人版本完整消费校验，实际请求超过 20 秒；前端同时将请求未完成的初始空数组显示成空目录。

前端区分 loading/catalogLoaded，只有请求完成且确实无条目才显示空目录，错误仍显示真实错误。
后端先应用领域筛选，再读取相应资产；目录只为当前展示版本计算真实应用资格。
历史版本仍保留在 versions 中，固定版本详情与 compatibility 接口在显式访问时独立校验，不使用未经校验的 supported 标签。
不删除资产、历史版本或来源，不重新生成本体；前端资源版本更新为 v12。

96 项既有构建 UI／设计 HTTP 检查通过，所有已审阅测试摘要保持不变；ruff、mypy、diff 检查通过。
额外 Node 延迟响应诊断确认请求未结束时 loading=true，完成空响应后才允许空目录状态。
只读 Flask 基准读取原有 5 个登记资产约 2.5 秒；确认无 queued/running 后以 Gunicorn SIGHUP 平滑重载服务。
在线真实 Chromium 验证目录正常展示新候选，无页面异常，无 POST 请求；接口约 2.7 秒返回 24 个登记与旧数据引用条目。
本次作用范围验证如上，未重跑整套后端回归，未调用真实模型。

- [机器记录](IR-024-catalog-loading-results.json)
- [定向检查](../../.check-results/IR-024-catalog/tests.log)
- [加载状态诊断](../../.check-results/IR-024-catalog/loading.cjs)
- [在线浏览器诊断](../../.check-results/IR-024-catalog/live_check.py)
- [在线结果](../../.check-results/IR-024-catalog/live-result.json)
