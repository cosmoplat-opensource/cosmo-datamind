# 机器人集成离线演示 — IR-016 / DR-060 / DR-061 / DR-065

这个命令验证“新来源 XML → 新本体 → 发布 OWL → 加载 → 任务生成”的第一阶段业务内核。
无需启动 DataMind 页面、HTTP 服务或配置模型密钥，不连接机器人。
任务采用随仓库提供的六字段结构化输入，尚不验证自然语言经 LLM 提取的效果。

## 运行

在工程根目录执行：

```bash
.venv/bin/python scripts/demo_robotics.py --case all --output-dir .check-results/robotics-demo
```

前提是 Python 3.10+ 和工程依赖已安装，本轮环境使用 rdflib 7.6.0、pySHACL 0.31.0。
若需从空环境准备，可先创建虚拟环境并安装 `requirements-dev.txt`；安装依赖需要网络，演示执行本身离线。
输出目录所在的本地文件系统需要支持硬链接，发布器与生成器以此保证完整文件创建及禁止覆盖。

也可以从任意目录使用绝对路径运行：

```bash
/home/dyh/workspace/Project/cosmo-datamind/.venv/bin/python /home/dyh/workspace/Project/cosmo-datamind/scripts/demo_robotics.py --case all --output-dir /tmp/datamind-robotics-demo
```

三个场景均可独立运行，将 `--case all` 替换为下表中的场景名即可。

| 场景 | 预期实际状态 | PASS 表示什么 |
|---|---|---|
| `demo-success` | `generated` | 本轮构建加明确模拟补充，生成有效的演示候选 |
| `real-missing-evidence` | `needs_information` | 缺业务映射/适配依据被指出，未生成候选 |
| `invalid-contract` | `rejected` | 新发布物的独立副本被改为未知协议，消费加载明确拒绝 |

终端显示本次运行的绝对路径、每个场景的预期/实际状态、PASS/FAIL 及问题摘要。
三个场景均符合预期时退出 0；基础设施错误、错误放行、缺失工件或其他不符合预期的结果退出非零。
参数用法错误退出 2。来源缺失等场景错误会写 FAIL 报告；若输出目录本身不可写，则只能在终端报告错误。
在命令运行后立即执行 `echo $?` 可查看退出码。

## 查看结果

每次执行都会创建唯一的运行子目录，重复运行保留旧结果：

```text
robotics-demo/<本次运行目录>/
  summary.md
  summary.json
  demo-success/
    published.owl
    demo_candidate.xml
    validation_report.json
  real-missing-evidence/
    published.owl
    validation_report.json
  invalid-contract/
    published.owl
    invalid-contract.owl
    validation_report.json
```

先打开 `summary.md`，查看三项结果及文件链接；自动化检查可读取 `summary.json` 的 passed 和 cases。
汇总中的每个工件包含绝对路径和 SHA-256，指向本轮实际存在的文件。
`demo-success/validation_report.json` 包含源 XML、节点模型、发布 OWL、补充资料摘要、补充变更清单、参数来源及符号校验结果。
演示资料将阈值从来源的 0.8 改为 0.72，输出 XML 的公开感知输入应为 0.72，其余固定流程保持原样。
两个负向场景的报告包含原因，目录中应没有任何候选 XML；只核对终端打印的本次目录，不拿旧目录中的候选判断本次成功。

`invalid-contract.owl` 是刻意构造的无效副本，不能用于正常生成；旁边的 published.owl 保留原发布内容。
PASS 表示符合场景预期，不是现场机器人可执行证明；成功候选始终标记 demo 与 execution_readiness=not_verified。

## 输入与补充资料

默认输入位于 `tests/fixtures/robotics/`：

- `sources/xingweishu.xml`、`sources/xml_models.xml`：来源行为树与节点模型。
- `demo_supplement.json`：显式合成的业务对象映射、公开参数、模拟适配依据和结构化任务。

不读取原 yang 归档、预置 agent OWL、相邻 SHACL 文件或外部推理报告。
补充资料只适用于其中明确登记的来源/模型摘要，不能用模拟资料冒充真实证据、修改固定技能或替换整张图。
其处理产生独立的 demo 图；真实缺证据场景使用未补充的独立新构建图。
修改输入需提供相匹配且经过检查的补充资料，不能只修改摘要来掩盖语义不兼容。

可使用 `--source`、`--models`、`--supplement` 指定其他输入路径；省略时使用仓库默认输入。
例如检查缺文件是否如实失败：

```bash
.venv/bin/python scripts/demo_robotics.py --case invalid-contract --source /tmp/nonexistent-robotics-source.xml --output-dir .check-results/robotics-demo-errors
```

该路径不存在时应返回非零，并记录 actual_status=error、passed=false，不能记成“预期契约拒绝通过”。

## 第一阶段验收

离线演示通过后，可运行工程全量检查。本机 Node 已安装但默认不在 PATH，使用：

```bash
env PATH="/home/dyh/.local/opt/node-v22.20.0-linux-x64/bin:$PATH" .venv/bin/python scripts/check.py --suite all --report-dir .check-results/ir016-final
```

最终以审计记录中的实际退出码和跳过原因判断，不把可选上游组件、真实模型检查的跳过写成验证成功。
本阶段交付业务内核与离线契约贯通；平台 UI、模型接入、持久化 runs/manifest 服务及机器人现场执行属于后续范围。
