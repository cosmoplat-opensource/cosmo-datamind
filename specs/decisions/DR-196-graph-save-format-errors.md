# DR-196 · 保存与导出时的 RDF/XML 表示限制

状态：accepted。
实现交付状态：delivered。

## 上下文

[DR-191](DR-191-graph-copy-version.md) 把编辑副本保存为 RDF/XML 版本。RDF/XML 把属性写成 XML 元素名。属性 IRI 的最后一段以数字开头，或 IRI 以 `/`、`#` 结尾时，无法写成元素名。此前保存和 OWL 导出共用一条提示："请选择其他导出格式"。保存时并没有格式可选，提示也没有指出是哪个属性导致的。

## 决定

- 序列化失败后逐个检查副本中的属性，列出 RDF/XML 无法表示的属性 IRI。最多列出 5 个，超出部分给出总数。只有写入失败时才做这项检查。
- 保存失败时说明新版本以 RDF/XML 存储，列出这些 IRI，并提示修改后再保存，不建议其他格式。没有生成新版本，编辑器保留当前副本，可以继续修改后重试。
- OWL 导出失败时列出同样的 IRI，提示可改用 Turtle 或 JSON-LD 导出，或修改这些 IRI。
- 找不到具体属性时，保存和导出各自使用不带 IRI 列表的说明。Turtle、JSON-LD 导出和编辑视图不受影响。
- 新建或改名时暂不拦截这类 IRI，以免改变已有编辑操作的校验结果。

## 关联

[IR-057 迭代与复现](../iterations/IR-057-graph-save-format-errors.md) ／ [验收映射](../acceptance.json)中的 GRAPH-FORMAT-01。
