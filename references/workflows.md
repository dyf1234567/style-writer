# Workflows

Run commands from the Skill directory.

## Inspect

```powershell
python scripts/style_engine.py status --author jiangnan
```

## Build or refresh

```powershell
python scripts/style_engine.py build --author jiangnan --provider ollama --model bge-m3
```

When the pack, corpus, or index use non-default locations, pass every root explicitly for the first build:

```powershell
python scripts/style_engine.py build --author jiangnan --authors-root <author-pack-root> --corpus-root <corpus-root> --index-root <index-root> --provider ollama --model bge-m3 --endpoint http://127.0.0.1:11434
```

The generated SQLite index is machine-local and rebuildable. Keep it outside the Skill and out of Git.

Use `--provider none` for FTS5-only portability. Exact duplicate files and duplicate chunks are removed. Files matching the pack's exclusions are skipped unless `--include-lore` is explicitly supplied.

After changing only family patterns or lore exclusions, refresh metadata without
recomputing embeddings:

```powershell
python scripts/style_engine.py reclassify --author jiangnan
```

## Prepare a scene

```powershell
python scripts/style_engine.py prepare --author jiangnan --family longzu --query "雨夜重逢，人物故作轻松"
```

If the index or embedding service is absent, `prepare` degrades to FTS5 or static-profile mode and reports the active mode.

`prepare` returns statistics and metadata without source prose by default. Add
`--include-excerpts` only when the user explicitly asks to expose source passages.

## Audit generated prose

```powershell
python scripts/style_engine.py audit-overlap --author jiangnan --input path/to/chapter.md
```

A warning is a review queue, not proof of copying. Rewrite flagged wording while preserving scene facts.

只有每个 probe 都取到候选且没有重合警告，才返回 `clean`。部分或全部
probe 无候选时 `ok: false`、`requires_manual_review: true`；没有重合警告
则为 `inconclusive`，已有警告则仍为 `review`。`clean` 仅描述候选比对结果，
不是全语料穷举或原创性证明。

升级后请重新运行 `build`：新版分块会保留短文及不足最小目标长度的末尾片段，
旧索引不会自动补齐。向量批次数量或维度异常会阻止构建；查询维度与索引不符
会通过 `vector_error` 说明原因并降级到词法检索，需使用同一个模型重建索引。
