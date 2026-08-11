# DR-017 引擎设置:运行时 / 模型 / API Key 实时切换(前后端 + UI)

日期:2026-07-25 · 状态:已实现 · 回归:test_all.py 119/119

## 背景

引擎切换只有隐蔽的 /api/ont/runtime + 构成规则页小开关;模型(CLAUDE_MODEL/HERMES_MODEL/HERMES_PROVIDER)是模块级常量,启动后不可变;API Key 无任何管理入口。用户要求:实时切 Agent(claude-code/hermes/openclaw)、实时选模型(opus/glm/gpt…)、可填 API Key 或直接用订阅版 CLI,按业界通行做法实现。

## 决定与实现

1. **实时生效机制**:`agent_runtime._cmd` 改为**调用时读 env**(`os.environ.get("CLAUDE_MODEL") or 常量`,hermes 同);server 每次配置变更 `_RT_CACHE.clear()` —— 切运行时/换模型无需重启,下一次请求即生效(实测:opus→haiku 后连通测试 5.3s 秒回)。
2. **单一配置源**:`workdir/engine_config.json`(**chmod 0600**,含密钥);启动即 `_apply_engine_cfg()` 应用(覆盖 start.sh 缺省)。
3. **API**:`GET/POST /api/engine/config`(部分更新:driver/各运行时模型/hermes provider/keys;driver 校验 available,GET 先 import serve_claw 保证 openclaw 注册);`POST /api/engine/test`(先测后切:真实跑一条最小指令,回真实延迟或真实报错)。
4. **Key 治理(best practice)**:只写不回显 —— GET 一律掩码(`******` + 尾 4 位);空串=清除且**同步弹出进程 env**;保存即注入 env 供各 CLI 子进程继承;白名单 6 变量(OPENAI/ANTHROPIC/ZHIPU/DEEPSEEK/MOONSHOT/DASHSCOPE);不写日志。订阅版(claude/codex/qwen-oauth)无需 Key,UI 注明 oauth 类需终端登录。
5. **UI「引擎设置」页**(运维管理组):运行时卡(可用性/当前标记/模型 datalist 可选可自填/hermes provider 下拉/设为当前/保存模型/**测试连通**含真实延迟与报错)+ API Keys 表(掩码/保存/清除);agy 如实标注"地域受限未接入"。

## 已知问题(已修)

- hermes CLI 把 429/503 打 stdout 且 rc=0 → 测试假"连通";用 `_looks_like_error` 复判。
- 清 Key 只删配置不弹 env → GET 掩码仍显示;显式 `os.environ.pop`。
- openclaw 运行时注册在 serve_claw import 时 —— available() 结果取决于 import 顺序,配置端点需主动触发。

## 增补(同日):Claude Code 模型清单实测扩充

用户指出 Opus 已到 5.0 —— **实测证实**(逐个跑 `claude -p --model X`):`claude-opus-5` 可用且为最新;别名 `opus` 现解析到 claude-opus-5、`sonnet`→claude-sonnet-5、`haiku`→claude-haiku-4-5(别名自动跟踪最新,推荐)。全量可用清单:claude-opus-5 / claude-opus-4-8 / claude-sonnet-5 / claude-fable-5 / claude-haiku-4-5-20251001 + 三个别名;`opus-5` 这种写法无效(已排除)。当前默认已切 claude-opus-5(连通 8.8s)。教训:**模型清单不靠记忆靠实测** —— 逐个真跑一条最小指令验证后才进选项。

## 边界

- Key 为本机单用户明文存储(0600);多用户/生产需接密钥管理(KMS/Vault)。
- hermes 的 oauth 类 provider(codex/qwen)登录仍需终端交互,UI 只能提示。
