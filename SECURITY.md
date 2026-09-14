# 安全说明

## 威胁模型与定位

COSMO DataMind 的设计定位是**单机、单用户的本地分析工作台**,默认只监听 `127.0.0.1`。
它**不含用户体系、不含鉴权、不含多租户隔离**。请据此评估你的部署方式。

> **不要在未加鉴权的情况下把本服务暴露到公网或共享网络。**
> 若确需远程访问,请置于反向代理之后,由代理承担认证、授权与限流。
> 生产入口为 `gunicorn -c gunicorn.conf.py wsgi:application`；参考反代模板见
> `deploy/nginx/cosmo-datamind.conf`。`python3 server.py` / `start.sh` 仅用于本地开发。

## 已内置的防护

| 面向 | 措施 |
|---|---|
| SQL 执行 | 只读:连接串 `mode=ro` + 语句级 `sql_is_readonly` 校验 + `_single_statement` 拒堆叠语句,三重防写 |
| SQL 标识符 | 查询:凡把表名/列名拼进 SQL 的位置先过 `_safe_ident` 白名单,必须容纳任意字符处(如中文指标名做列别名)走 `_quote_ident` 成对转义;建表:上传/接入数据的表名与列名先经白名单正则消毒再进 DDL |
| 跨站请求 | 全局 CSRF 守卫比较 scheme/host/port，拒绝 null/畸形源；Host 白名单同时保护 GET，阻止 DNS rebinding；TLS 反代源须显式配置 |
| 路径穿越 | 所有落盘/删除/读取 sink 统一经 `_confined`(归一化后须仍在基目录内)与 `_safe_fname`;图谱键额外禁止 `..` |
| 命令执行 | 仅调用固定白名单命令,全部以参数列表形式传入,不经 shell;用户/LLM 提供的参数先过 `_safe_argv`(去 shell 元字符、限长、不以 `-` 开头) |
| SSRF | API 数据源只允许 http(s)，拒绝链路本地、未指定、多播与保留段；每次重定向和连接均验证地址，连接固定已验证 IP 并保留 HTTPS 主机校验；不使用环境代理；`DATAMIND_BLOCK_INTERNAL_FETCH=1` 进一步拒绝非公网地址 |
| SPARQL | 禁用 `SERVICE` 与 `FROM <外部 URI>`,阻断联邦查询外呼 |
| 凭据 | 模型密钥落盘 `0600`;外部库口令**静态加密**(Fernet,主密钥 `workdir/.conn_key` 单独 `0600` 存放),DSN 内嵌的 `user:pass` 在登记时即摘出,回显一律掩码;均已 gitignore |
| 信息泄露 | 诊断走 `logging` 而非 `print`,默认不带内部路径与异常消息(需要时 `DATAMIND_LOG_LEVEL=DEBUG`);`/vendor/*.map` 一律 404,不提供 source map |
| 日志伪造 | `_LogSanitizer` 装在 root handler 上,抹掉消息体里的换行与控制字符——一条记录不会被拆成两条,ESC 序列也无法操纵运维终端;MCP server 的 stderr 走同规则的 `_log()` |

## 已知边界(非缺陷,是有意的取舍)

反向代理终止 TLS 时，设置 `DATAMIND_PUBLIC_ORIGIN=https://你的域名`。
额外可信主机用 `DATAMIND_TRUSTED_HOSTS` 声明（逗号分隔的确切主机，不含端口或通配符）。
服务不因任意 `X-Forwarded-*` 请求头而放宽授权；监听 `0.0.0.0` 也不等于允许任意 Host。

- **无 `Origin` 的写请求不拦截**。`curl`、脚本、API 客户端不带 `Origin`,不在 CSRF 威胁模型内。
  这意味着本机上的任意进程都可以调用写接口——与"单用户本地工具"的定位一致。
- **SPARQL 查询有软超时**,极端复杂的查询仍可能占用较多 CPU。
- **上传的数据落入本地 SQLite**,不做病毒扫描与内容审查。
- **外部数据库的 SELECT 函数仍可能有副作用**。词法守卫拒绝堆叠语句、SELECT INTO/OUTFILE/DUMPFILE；外部账号仍须限制写入、文件与危险函数权限。
  不同数据库对注释和转义解释不同，歧义形式被保守拒绝；这不是完整 SQL 方言解析器。
- **SQL 工作台 / 看板取数 / SPARQL 接受使用者书写的查询**,这是产品能力而非注入缺陷;
  边界由"只读 + 单语句 + 只读连接"三条界定,而不是靠过滤关键字。
- **默认允许访问内网与回环地址**。本工具的用途就是连内网库与内网 API,一律封死
  只会让人把开关打开(安全表演)。危害最大且从无正当用法的目标(非 http 协议、
  云元数据端点)已默认拦死;需要更严时用 `DATAMIND_BLOCK_INTERNAL_FETCH=1`。
- **凭据加密的主密钥与密文同机存放**。这挡住"随手复制一个 json / 打包备份 / 误提交"
  这条最常见的泄露路径,但挡不住已取得本机文件读权限的攻击者——那已超出单机工具的威胁模型。
  未安装 `cryptography` 时降级为明文落盘(权限仍 `0600`)并在日志中告警,不静默。

## 密钥管理

密钥一律通过环境变量提供,**环境变量优先于配置文件**:

```bash
cp .env.example .env      # .env 已被 gitignore
export ANTHROPIC_API_KEY=...
```

界面上填写的密钥会写入 `workdir/engine_config.json`(权限 `0600`),仅作本地开发便利;
生产部署请只用环境变量,并确保该文件不存在。

## 漏洞报告

本地代码、依赖与回归审计见 [`docs/audits/2026-09-07.md`](docs/audits/2026-09-07.md)。
其中模拟网络检查、真实浏览器检查与未覆盖部署边界分别记录，不把通过测试表述为“所有漏洞已排除”。

发现安全问题请**不要**提交公开 issue。请通过仓库主页列出的联系方式私下报告,
并附上复现步骤与影响范围。我们会在确认后修复并在发布说明中致谢。
