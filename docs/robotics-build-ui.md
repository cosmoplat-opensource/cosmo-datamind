# 机器人本体构建页面（IR-018 / IR-024）

IR-024 增加同入口模型辅助设计，当前操作顺序、路径判定、总 JSON 下载和应用限制见 [双路径构建指南](robotics-dual-build.md)。
本页下面的构建步骤与旧工件说明适用于未配置模型的离线路径；配置了模型时按双路径指南操作。

IR-018 已于 2026-11-04 经用户确认交付；页面、后端与真实浏览器联动均通过验收。
设计依据：DR-060、DR-063、DR-064、DR-065。

## 使用现有平台启动脚本

正常使用可以直接通过工程根目录的 `./start.sh` 启动，不必改为手动运行 Gunicorn。
在 `.env` 中设置：

```dotenv
DATAMIND_ROBOTICS_ENABLED=1
```

首次启动执行 `./start.sh`；服务已运行时，修改配置或升级代码后执行：

```bash
./stop.sh
./start.sh
./status.sh
```

打开启动输出的服务地址，默认 `http://127.0.0.1:8092/#build`。
start.sh 通过 scripts/service.py 加载 .env 并启动同一套 Gunicorn 服务，机器人模块随服务一起加载。
.env 中的值优先于启动命令继承的同名环境变量；配置按字面量读取，不执行 `$PWD` 等 shell 展开。
资产默认保存到 DATAMIND_WORKDIR 下的 domain_assets；如需另设 DATAMIND_DOMAIN_STORAGE_ROOT，填写实际绝对路径。
来源 XML 通过页面上传即可，数据库和存储子目录由程序管理，不需要手工放入数据库或 XML 文件。
这一路径使用实际业务工作目录；下方的临时目录启动仅用于隔离试用。

## 启动独立试用环境

在工程根目录执行下面命令，创建独立临时工作区与资产存储，不写入当前业务目录。
命令在前台运行，浏览器访问 `http://127.0.0.1:8093/#build`。

```bash
robotics_ui_root=$(mktemp -d /tmp/datamind-robotics-ui.XXXXXX)
printf '本次试用目录：%s\n' "$robotics_ui_root"
env DATAMIND_WORKDIR="$robotics_ui_root/work" \
    DATAMIND_DOMAIN_STORAGE_ROOT="$robotics_ui_root/assets" \
    DATAMIND_DB="$PWD/examples/demo_metrics.db" \
    DATAMIND_ENGINE_DIR="$robotics_ui_root/no-engine" \
    DATAMIND_OUTPUTS_DIR="$robotics_ui_root/no-outputs" \
    DATAMIND_ROBOTICS_ENABLED=1 DATAMIND_HOST=127.0.0.1 DATAMIND_PORT=8093 \
    .venv/bin/python -m gunicorn -c gunicorn.conf.py wsgi:application
```

端口被占用时换用空闲端口，同时调整浏览器地址。
要验证重启后回查，按 Ctrl+C 停止服务，再只执行 `env ... gunicorn ...` 部分，沿用同一个 robotics_ui_root。
不要重新执行 mktemp 创建另一个目录；新终端则先把 robotics_ui_root 设为上一次打印的绝对路径。
上述端口和目录仅供独立复核，正式部署配置见 [API 指南](robotics-build-api.md)。

## 从上传到下载

1. 打开“本体构建”，在“本体构建配方”选择“行为树 XML → 机器人本体”。默认仍为数据本体。
2. 填写“机器人本体名称”，保留“构建模板”中当前唯一支持的 `ur_pick_place`。
3. “主行为树 XML”选择仓库中的 `tests/fixtures/robotics/sources/xingweishu.xml`。
4. “节点模型 XML”选择 `tests/fixtures/robotics/sources/xml_models.xml`。两者按角色接收，文件同名也不会互相覆盖。
5. 点击“构建机器人本体”，或聚焦按钮后按 Enter。页面显示运行编号、排队/构建状态、实际阶段事件；受理不是构建成功。
6. 成功后点击“查看本体候选”，进入固定版本详情。检查待评审状态、结构检查、技能/参数数量、来源 ID 和 SHA-256。
7. 点击“下载 OWL”“下载报告”“下载 manifest”。文件损坏或缺失时页面显示错误，不会将错误 JSON 保存成成功 OWL。
8. 刷新当前页面，或重启同一存储根的服务后重新打开该地址，仍可回查同一版本和下载文件。刷新不会新建运行。

合法夹具的候选详情应显示 **13 个原子技能、3 个复合技能、25 个技能调用**。
这些数字来自实际构建报告；没有加入演示脚本的业务补充材料。
“结构检查通过”不表示机器人可执行：候选仍待评审，任务适配未评估，执行未验证。
IR-018 当时未提供的后续能力已陆续接入；图谱与任务入口见 [图谱指南](robotics-graph-views.md) 和 [任务指南](robotics-task-application.md)。
模型设计候选是否能进入任务仍取决于固定版本的应用兼容性。

## 三个状态分别说明什么

| 状态 | 回答的问题 | 当前阶段 |
|---|---|---|
| 结构检查 | 本体是否通过当前结构与消费契约检查？ | 合法构建结果显示“通过”，不代表机器人可执行 |
| 任务适配 | 针对一个具体任务，技能、对象、参数与业务证据是否足够？ | 此页面尚未输入具体任务，因此“未评估” |
| 执行 | 生成结果是否在执行程序、仿真或真实机器人环境中得到验证？ | 本轮未进行执行验证，因此“未验证” |

例如“将零件 A 从工位 1 搬到工位 2”：任务适配要检查抓取/移动/放置技能、零件与工位定义以及所需参数；执行验证则要确认相关节点和参数在目标运行环境中的实际效果。
后两项并非构建错误；它们说明验证范围。当前版本不提供执行验证入口，也不自动将这两个状态改为通过。

## 失败、历史与版本

- 构建失败时显示问题代码、说明、来源角色/ID 及节点或端口位置；可下载报告和 manifest，没有成功候选 OWL。
- “重试构建”使用原来固定的来源，产生一个带 retry_of 的新运行，不覆盖原失败记录。若要修正 XML，重新选择文件并提交新的构建。
- HTTP 拒绝或网络错误会保留当前表单与所选文件。在当前页面内，输入未变的重发沿用请求编号；重新选择文件或改名后使用新编号。
- 离开构建页面停止页面轮询，后台任务继续。浏览器刷新前尚未收到受理结果时，可先到作业中心查看，避免把不确定受理当作未受理。
- “本体库”按领域展示目录。机器人资产打开固定版本，旧数据图谱保留原图谱入口并标注“未登记版本”；原数据本体管理区仍可使用。
- “作业中心”分组显示机器人本体构建历史与旧作业；机器人历史每页 20 项，可前后翻页。
- 运行地址为 `#build?recipe=robotics&run=<run_id>`；固定版本地址为 `#library?asset=<ontology_id>&version=<version_id>`。旧版本地址不会随新版本产生而改变。
- 指定记录不存在时显示错误并保留原地址，不跳到演示数据或首页。

## 功能开关

开关位于服务端配置，不在 UI 中。
`DATAMIND_ROBOTICS_ENABLED=1` 开启，`0` 关闭；修改后重启服务。
关闭时配方选择器不显示机器人选项，后端拒绝新的构建和重试；当前 schema 的已有历史与候选仍能只读查询。
关闭不会删除已保存记录，也不影响原数据构建和旧图谱、聊天入口。

## 自动复核

环境需已有 Node、Playwright 和 Chrome/Chromium；本仓库当前机器的 Node 可按下列 PATH 使用。

```bash
# 前端状态与检查接线：合成 HTTP，检查并发、重发、错误、游标与清理。
env PATH="/home/dyh/.local/opt/node-v22.20.0-linux-x64/bin:$PATH" \
    .venv/bin/python -m pytest tests/unit/test_robotics_build_ui.py -q

# 真实浏览器：自动创建和清理隔离的实际 Gunicorn 服务及存储。
.venv/bin/python tests/frontend/robotics-build-browser.py \
    --output .check-results/robotics-browser

# 包括旧功能和新浏览器联动的统一检查。
env PATH="/home/dyh/.local/opt/node-v22.20.0-linux-x64/bin:$PATH" \
    .venv/bin/python scripts/check.py --suite all
```

浏览器程序不连接用户已有服务，也不拦截核心 API 返回成功替身。
每场景保存 `evidence.json`、`server.log`、截图及实际下载文件；可用 `--case <函数名片段>` 单独复核。
其中恶意诊断文字测试使用明确标注的人工诊断，验证显示安全；实际模型冲突另有真实内核测试，不能混为一谈。
