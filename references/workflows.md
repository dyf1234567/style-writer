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
