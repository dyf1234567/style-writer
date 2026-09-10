# Author pack contract

An author pack is a small portable directory. It contains metadata and abstracted style guidance, never the source corpus or vector database.

A pack has two producers: hand-writing this contract's fields, or bridging a filled
analysis style card with `import-pack` (see the repository README). The card in
`analysis/` is the single source of truth for abstract style; regenerate the pack
with `--force` after editing a card instead of maintaining both by hand.

## Files

`pack.json` is required. `profile.md` is optional human-readable guidance.

Required `pack.json` fields:

```json
{
  "schema_version": 1,
  "slug": "example-author",
  "display_name": "Example Author-inspired",
  "positioning": "High-level traits only; no exact imitation",
  "corpus": {"env": "EXAMPLE_CORPUS_ROOT"},
  "families": [{"id": "fiction", "label": "Fiction", "patterns": ["小说"]}],
  "default_family": "fiction",
  "unmatched_family": "other_fiction",
  "exclude_patterns": ["设定集", "人物小传"],
  "traits": ["..."],
  "negative_constraints": ["..."]
}
```

Paths are resolved in this order: explicit CLI argument, pack-specific environment variable, shared environment root. Do not save a machine-specific absolute corpus path in a transferable pack.

`default_family` is the query fallback. `unmatched_family` classifies corpus files
that match no family pattern; keep these separate so an unknown work cannot pollute
the default writing family.

## External roots

重新导入：`import-pack --force` 更新卡片派生的风格字段，但保留已有
`display_name`、`corpus`、`families`、`default_family`、`unmatched_family` 和
`exclude_patterns`。显式 `--display-name` / `--corpus-env` 优先于旧配置。
原文比对使用保留或显式覆盖后的语料环境变量；语料为空或汉字数不足
`--overlap-run` 时返回 `source_overlap.status: skipped` 和警告，不算已检查。

卡片 YAML 是受限子集。内联列表支持引号内逗号和井号；单引号用 `''`
表示引号，双引号支持 JSON 转义。未闭合引号、引号后垃圾、嵌套流式集合
会报错，不应按完整 YAML 规范假定支持。

未加引号的 `null` / `Null` / `NULL` / `~` 是空值；带引号的版本仍是字符串。
可选元数据 `explicit_null_fields` 保存本次有效卡片中值为显式空值的路径（列表位置如
`plotlines.lines[0].weight`），不包含原始值、原文或本机文件路径。它记录空值来源位置而非原因，
不等于 `unmeasured` 或 `known_gaps`。省略字段、空字符串、带引号的空值字样不计入。
导入结果、`status` 和 `prepare` 顶层返回该列表，不放入写作上下文。
旧包无此字段按空列表展示，表示没有可用记录而不是已证明无缺项；`--force` 按新卡片重新生成列表，
不保留过时记录。无新证据时不修改现有作者包来追补此字段。
数值字段仅接受有限数值（模板空字符串和空值可跳过），不接受数值字符串或布尔值。
`imagery.taboo`、`imagery.semantic_domains`、`syntax.lexical_fingerprint` 必须为字符串列表，
块状和内联写法均检查类型。`避免:连续感叹号` 是标量，`避免: 连续感叹号` 是映射；
要把后者作为文字，请加引号。映射键后冒号须跟空白或行尾，`pov:人物甲` 不是合法映射成员。

旧包损坏或 schema/slug 不符时，普通 `--force` 不覆盖文件。修复 JSON 或在明确放弃旧配置后
使用 `--force --discard-existing-config`，后者重置上述六项运行配置并警告；显式参数仍优先。
不要自动选择恢复参数。所有卡片与红线检查通过后才进行同目录临时写入与原子替换。

- `AUTHOR_STYLE_HOME`: author pack root; each pack is a child directory.
- `STYLE_INDEX_HOME`: SQLite index root.
- `STYLE_VECTOR_OLLAMA_URL`: optional Ollama endpoint; defaults to `http://127.0.0.1:11434`.

Copying the Skill and pack to another machine preserves static-profile mode. FTS5 requires rebuilding the local index. Vector mode additionally requires an embedding endpoint and the configured model.
