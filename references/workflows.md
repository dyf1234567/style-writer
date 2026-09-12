# Workflows

Run commands from the Skill directory.

从新作者语料开始，或为已有作者包首次建库，先读 [新作者初始化](new-author.md)。
以下是独立命令参考；不要因为写作时缺少索引就自动执行 `build`。

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

审计的 `warnings` 仅包含重合警告。`retrieval_diagnostics` 现为 `schema_version: 2` 的对象（旧版为逐 probe 列表，程序消费者须适配）。
其中 `warnings`、`vector_errors`、`index_compatibilities` 分别保存去重后的检索警告、向量错误和兼容信息，不包含候选原文。
`probes` 保存逐次 `probe`、`mode`，以及对应数组的零起始引用 `warning_ids`、`vector_error_id`、`index_compatibility_id`；没有对应信息时省略引用。
检索状态变化保留各次引用，检索中途返回无索引或空范围时也保留已收集诊断。
报告审计结果时须检查此字段，不能隐藏向量降级或版本不确定性；诊断本身不自动改变重合结论。

只有每个 probe 都取到候选且没有重合警告，才返回 `clean`。部分或全部
probe 无候选时 `ok: false`、`requires_manual_review: true`；没有重合警告
则为 `inconclusive`，已有警告则仍为 `review`。`clean` 仅描述候选比对结果，
不是全语料穷举或原创性证明。

仅当索引仍由 `ddac4fe` 前的分块代码生成时，建议重新运行 `build`：新版分块会保留短文及不足最小目标长度的末尾片段，
旧索引不会自动补齐。向量批次数量或维度异常会阻止构建；查询维度与索引不符
会通过 `vector_error` 说明原因并降级到词法检索，需使用同一个模型重建索引。

## 分块器版本检测

`build` 写入独立于数据库 schema 的 `chunker_version`。后续改变语料归一化或分块边界时需递增此标记。
`status`、`query`、`prepare` 返回 `index_compatibility`（当前/记录版本及状态）与顶层 `warnings`。
状态为 `current`、`unknown`（缺标记）、`mismatch`（不匹配）、`no-index`。
这只是分块器兼容提示，不代表语料或模型未变化，也不代替实际检索验收。

未知或不匹配时，继续保留现有检索模式与结果，同时说明限制，不自动重建或停止写作。
尤其是缺少标记：不能断言“短文遗漏”，此前用新版代码构建的库也可能没有标记。
经用户确认后可用 `build` 重建；`reclassify` 只更新分组，不会重分块或伪造新版本标记。
