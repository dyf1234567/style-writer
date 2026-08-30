# style-writer

A portable Codex skill for building and applying author-inspired Chinese prose style packs. It supports static profiles, SQLite FTS5 retrieval, optional local embeddings through Ollama, work-family routing, and source-overlap auditing.

The workflow is designed for high-level inspiration rather than exact impersonation. Project characters, canon, facts, and voice take priority over every style pack.

## Install

Copy or clone this repository to the Codex skills directory so the entrypoint is located at:

```text
~/.codex/skills/style-writer/SKILL.md
```

Restart or refresh Codex, then invoke it explicitly with `$style-writer` or describe a matching style-analysis or style-guided writing task.

## Storage model

The skill intentionally does not contain author corpora, author packs, or generated indexes.

- `AUTHOR_STYLE_HOME`: portable author-pack root
- `STYLE_INDEX_HOME`: rebuildable SQLite index root
- `STYLE_VECTOR_OLLAMA_URL`: optional Ollama endpoint; defaults to `http://127.0.0.1:11434`

See [`references/pack-contract.md`](references/pack-contract.md) for the author-pack schema.

## Typical workflow

```powershell
python scripts/style_engine.py status --author example-author
python scripts/style_engine.py build --author example-author --provider ollama --model bge-m3
python scripts/style_engine.py prepare --author example-author --family fiction --query "雨夜重逢，人物故作轻松"
python scripts/style_engine.py audit-overlap --author example-author --input path/to/chapter.md
```

Use `--provider none` for an FTS5-only index. `prepare` excludes source prose by default and returns compact statistics and abstract controls. Source excerpts require explicit opt-in and must not be copied, continued, or closely paraphrased.

## Data and rights

Only index material you are authorized to use. Keep copyrighted corpora and generated databases outside public repositories. An overlap warning is a manual review signal, not a legal conclusion.

No general open-source license is currently granted by this repository merely because it is publicly visible.
