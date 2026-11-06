# 行为树生成基础版使用说明（IR-020 / DR-066）

这版将已有本体构建接到任务解析、按需补齐和行为树生成。
用户只需关注当前版本；高级知识维护仍保留在版本详情的次级入口，基础流程无需进入。

## 通过页面使用

1. 用 `./start.sh` 启动平台；机器人功能沿用 `.env` 中的 `DATAMIND_ROBOTICS_ENABLED=true` 开关。
2. 在“引擎设置”配置 OpenAI 兼容 API 地址、默认模型和密钥。可在任务模型中单独指定“机器人任务解析”；留空则使用默认模型。
3. 在本体构建中上传行为树 XML 和节点模型 XML，选择现有构建模板。成功后点击“生成行为树”。也可从本体库已有机器人本体进入。
4. 输入任务，例如“把零部件A放到工位B的载具B01、放置位B01”。名称应来自当前本体；系统不会把模型猜测当作选择。
5. 若需要澄清，补充目标或选择对象；不存在的对象可以明确新建。
6. 出现“补齐生成信息”时，只填写当前任务缺少的内容。关联可选已有对象或就地新建；参数使用实际设备数据，系统不猜默认物理值。
7. 核对当前任务已有知识。适配项需要实际依据、有效期，以及明确的适用范围确认；附件可选。填写或上传本身不等于确认。
8. 点击“保存并继续”。系统保存新的候选 OWL，保留原版本，并继续同一任务；仍缺信息时显示下一步。
9. 生成成功后查看只读行为树、展开需要的节点属性或验证报告，下载行为树 XML。页面预览和下载来自同一次实际运行。

如果服务已经在运行旧代码，先用 `.venv/bin/python scripts/service.py restart` 重启，再刷新页面。
本次实现没有自动重启用户服务。

机器人与问数使用各自的提示词和业务流程；这里的模型设置决定调用哪一个模型服务，并不是编辑提示词。
机器人模型只提取六字段任务，参数来自保存的 OWL，XML 由确定性编译器生成并校验。
当前支持固定抓移放模板，结果是离线候选，不代表设备端已经验证或执行。

## 返回、导入与演示

- 保存当前任务页面地址可再次回看。刷新或正常重启后可恢复已保存的对话、版本和结果；“新任务”创建独立会话。
- “下载当前 OWL”用于导出；本体库的“导入机器人 OWL”仅接受符合本平台消费契约的文件，不是任意第三方 OWL。
- “打开独立演示”使用随代码登记的 XML 和明确标记的合成补充材料，新建独立演示资产。它不会给当前真实本体自动补数据。页面演示的自然语言解析仍使用配置的模型。
- 未配置模型、请求失败、参数不合法或适配不足时，页面显示错误或缺项，不会把上一次成功 XML 当作本次结果。
- 默认入口简化后，IR-016～018 的构建、存储、运行、OWL 下载能力继续沿用；IR-019 高级维护接口和显式页面入口仍保留。

## 无需在线模型的离线演示

在工程根目录运行以下任一命令；输出目录必须不存在，重复运行请换路径。

```bash
.venv/bin/python scripts/demo_robotics_task.py --case generated --output /tmp/datamind-task-generated
.venv/bin/python scripts/demo_robotics_task.py --case clarification --output /tmp/datamind-task-clarification
.venv/bin/python scripts/demo_robotics_task.py --case missing-knowledge --output /tmp/datamind-task-missing
.venv/bin/python scripts/demo_robotics_task.py --case model-error --output /tmp/datamind-task-error
.venv/bin/python scripts/demo_robotics_task.py --case complete-information --output /tmp/datamind-task-completion
```

| 案例 | 预期结果 |
|---|---|
| generated | 独立演示本体生成候选 XML |
| clarification | 缺明确目标，输出澄清信息，没有 XML |
| missing-knowledge | 原始构建缺映射/确认等知识，输出待补项 |
| model-error | 模拟模型调用失败，留下错误记录，没有 XML |
| complete-information | 从缺知识构建开始，经真实补齐服务保存新 OWL，再编译并下载候选 XML |

输出包括 `summary.json`、`ontology.owl`、`dialogue.json`、`validation.json` 和可恢复的 `storage/`。
只有成功生成的案例包含 `candidate.xml`。
查看 `summary.json` 的 `passed`、`status`；模型标记为 `scripted_test_double`，输入标记为合成资料，执行状态为 `not_verified`。
这些命令测试真实构建、存储和编译流程，只替换外部模型响应；不能证明在线模型理解能力或现场适配。

## 自动检查与真实模型人工冒烟

```bash
.venv/bin/python scripts/check.py --suite all
```

统一检查包含真实浏览器的构建→补齐→保存→生成→下载流程，并回归原数据页面、机器人构建和高级维护。
环境需要 Node.js 和已安装的 Chrome/Chromium；可通过 `DATAMIND_BROWSER_EXECUTABLE` 指定浏览器程序。
检查使用隔离目录和本地脚本化模型服务，不使用用户运行目录或线上模型密钥。

真实模型人工检查：配置实际模型后，依次尝试完整任务、缺目标任务、多轮更换目标和不支持的任务。
确认完整任务进入正确的缺项/生成步骤；缺目标先澄清；更换目标后旧下载消失并重新检查；不支持的任务不给 XML。
再用实际资料补齐一次，检查下载 XML 中的对象、目标和公开参数符合输入，刷新后仍能回看同次结果。
这一人工检查结果需单独记录，不把自动化测试中的模型替身记成真实模型验收。
