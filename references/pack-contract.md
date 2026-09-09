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

- `AUTHOR_STYLE_HOME`: author pack root; each pack is a child directory.
- `STYLE_INDEX_HOME`: SQLite index root.
- `STYLE_VECTOR_OLLAMA_URL`: optional Ollama endpoint; defaults to `http://127.0.0.1:11434`.

Copying the Skill and pack to another machine preserves static-profile mode. FTS5 requires rebuilding the local index. Vector mode additionally requires an embedding endpoint and the configured model.
