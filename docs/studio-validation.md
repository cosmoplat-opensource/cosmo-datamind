# Studio 修订验证与公开材料检查

每次验证先记录目标版本 `git rev-parse HEAD` 和 `git status --short`，未提交改动同时保留差异或输入 SHA-256。以下命令用于复现目标检出版本，不证明其它版本、生产部署或真实机器人执行。

## 环境

Python 3.10+，使用项目虚拟环境及 `requirements.txt` / `requirements-dev.txt`。Node 必须在测试子进程 PATH 中；项目已有 Playwright 随包 Node 时可复用。浏览器使用 Playwright Chromium 或 `DATAMIND_BROWSER_EXECUTABLE` 指定的 Chrome。不得加载真实模型凭据、生产数据库或用户运行目录。

```bash
# 仅为当前命令选择已有运行时，不修改系统 PATH。
NODE_BIN="$(.venv/bin/python -c 'from pathlib import Path; import playwright; print(Path(playwright.__file__).parent / "driver")')"
env PATH="$NODE_BIN:$PATH" .venv/bin/python scripts/check.py --suite unit
# 标准检查脚本会建立隔离运行目录，读取已提交的演示种子。
# 完整产品回归含 HTTP、严格验证和各浏览器套件：
env PATH="$NODE_BIN:$PATH" .venv/bin/python scripts/check.py --suite all
# Studio 浏览器定向回归使用临时持久化服务及合成模型响应：
.venv/bin/python tests/frontend/studio-robotics-browser.py
.venv/bin/python scripts/scenario_bench.py
```

退出码非零即检查失败。默认本地报告目录是输出位置，不是公开证据链接；授权读者可按上述步骤重跑。若分享报告，先审查路径、凭据、私有业务数据与截图，关联目标代码版本、输入指纹、命令、环境、退出码和未执行项。历史审计摘要只描述各自当时版本，不代表当前版本通过。

## 覆盖边界

`tests/meta/test_publication_hygiene.py` 检查已跟踪文件及 Word XML 中的个人目录、工具署名、本地工作流与未公开证据链接。它不能代替凭据审查、合作信息公开授权与暂存差异审查。迁移快照中的 Base64 附件也须检查；其副本保留历史结构和记录身份，合成替换的类型、参数与端口说明同步更新摘要引用。测试与演示中的 `demo::` 类型和端口说明用于合成兼容场景，不是外部平台接口或标定证据。

机器人图谱投影、同屏预算、请求超时、取消与失败后停止轮询、关系搜索分别映射至 `specs/acceptance.json` 的 STV-H01～H05。数据本体 Studio 接入尚未实现。

## 来源标识与存量兼容

来源清单中的 `${ROBOTICS_SOURCE_ROOT}` 是脱敏后的来源标识，历史诊断中的 `${HOME}` 代替个人目录；这些占位符不构成应用运行时依赖，不需要为运行程序配置外部源码目录。
受控词表只修改来源元数据，术语定义不变；文件摘要因此变化，新会话绑定新摘要。已有会话按自身保存的词表及摘要校验，固定版本继续按原始载荷摘要读取，不重写用户历史。演示文件及迁移测试快照的摘要随脱敏副本更新，不应将测试副本写入用户存量资产。
