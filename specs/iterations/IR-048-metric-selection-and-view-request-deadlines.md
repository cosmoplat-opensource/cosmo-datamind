# IR-048 · 页面响应归属与请求时限

状态：delivered。关联 [DR-187](../decisions/DR-187-metric-selection-and-view-request-deadlines.md) 与[验收映射](../acceptance.json)中的 `METRIC-SELECTION-RESPONSE-01`、`UI-REQUEST-DEADLINE-01` 和 `METADATA-COVERAGE-RESPONSE-01`。

## 验收

- [x] 同一图谱内，旧指标写入返回后刷新列表，当前选中的指标和未提交的编辑内容继续显示。
- [x] 指标读写及固定版本图谱读取的等待时限为 30 秒。读取可显式重试或重新打开，写入超时提示结果待确认且不会自动重发。
- [x] 元数据覆盖只显示当前图谱最近一次读取的统计。切换或刷新时清除旧统计，读取失败或超时后可刷新恢复。

## 验证

包含三项修复的最终集成版本通过全量回归 27 个阶段。业务名称浏览器 64 项、状态切换浏览器 20 项、机器人构建浏览器 21 项及交互式图谱浏览器 15 项通过。单元与规约 4218 项、集成 536 项、十轮变序 10 项、场景基准 57 项及本地 Gunicorn 上传回读通过。`ui/index.html` 为 2446 行，满足少于 2500 行的门槛。

验证环境为 Linux、Python 3.10、Playwright 1.62、Chromium 156、Node 24 和 JDK 17。可选上游引擎在单元与集成中分别有 5 项和 6 项按条件跳过。Linux WebKit 构建页和真实模型连接按项目条件跳过。

## 复现

按[项目测试说明](../../README.md#测试)准备环境。指标用例暂缓旧指标的写入，切换指标并编辑意见后再放行。元数据覆盖用例在新图谱统计显示后返回旧请求的成功或失败响应。超时用例推进浏览器时钟，核对页面提示、读取恢复和写入次数。固定版本图谱用例覆盖关系图与交互式图谱。

```sh
python -m pytest -q tests/frontend/business_name_browser.py tests/frontend/state_transition_browser.py
python -m pytest -q tests/frontend/robotics-build-browser.py -k fixed_graph_read_timeout
python scripts/check.py --suite all
bench_dir=$(mktemp -d)
DATAMIND_WORKDIR="$bench_dir" python scripts/scenario_bench.py
```

Gunicorn 上传回读可在独立工作目录中按 [CI 工作流](../../.github/workflows/ci.yml) 的 `deployment_smoke` 步骤复现。
