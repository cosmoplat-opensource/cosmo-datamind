# Local ontology standard assets

本目录保存半自动构建可以离线读取的、固定版本的机器可读标准资产。运行时只读这些
本地文件，不会因为 OWL `imports`、XSD `import/include` 或用户输入访问网络。

- `bfo/`：BFO 2020 OWL，CC BY 4.0。
- `iof/`：IOF Core 与 Annotation Vocabulary RDF/XML，MIT。
- `ufo/`：gUFO 1.0.0 Turtle，CC BY 4.0。
- `isa95/`：MESA International B2MML XSD。B2MML 是 ISA-95/IEC 62264 的公开
  XML 实现；随文件保留 MESA 许可和署名。这里没有复制受版权保护的 ISA/IEC 标准正文。

来源版本、许可和文件模式以 `manifest.json` 为准。`standard_assets.py` 会在目录 API
和构建时解析这些文件、计算内容指纹并报告术语/类型数量；文件缺失或不可解析时不得
伪装成“已加载”。

## Apache Ossie(原 Open Semantic Interchange)

`ossie/` 存放两份官方 machine-readable schema 的固定 revision 快照,供 `osi_export.py` 对齐导出形状、
供 `scripts/validate_ossie_export.py` 做离线校验。来源、revision 与许可见 `ossie/SOURCE.md`。
与其它标准资产一致:不跟随远程 `$ref` 联网,缺失时明确报未校验而不是假装通过。
