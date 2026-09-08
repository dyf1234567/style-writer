# style-writer

A portable Codex skill for building and applying author-inspired Chinese prose style packs. It supports static profiles, SQLite FTS5 retrieval, optional local embeddings through Ollama, work-family routing, and source-overlap auditing.

The workflow is designed for high-level inspiration rather than exact impersonation. Project characters, canon, facts, and voice take priority over every style pack.

The repository spans both sides of the style pipeline:

- `analysis/` — style **recognition**: read a reference work and converge it into an abstract `style-card.yaml` (11-section report incl. emotion writing & reward rhythm + `measure.py` quantification with v4 lexeme stats; formerly the standalone novel-style-kit project). Method only — never plot, names, or prose.
- `scripts/style_engine.py` — style **execution**: index a corpus, retrieve per-scene writing context, audit drafts for source overlap.

`import-pack` is the bridge between them: a filled style card becomes a runtime `pack.json`. See [From card to pack](#from-card-to-pack).

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

## Analysis: from a work to a style card

Run the recognition pipeline in `analysis/` (details in [`analysis/README.md`](analysis/README.md)):

```powershell
python analysis/scripts/measure.py <work.txt|work.epub> -o metrics.json
# fill analysis/analysis-report.template.md, converge to style-card.yaml,
# pass the card's red-line self-check, then bridge it:
```

## From card to pack

`import-pack` converts a filled style card into a `pack.json` under `AUTHOR_STYLE_HOME`. It is the only sanctioned path from the analysis stage into runtime packs:

```powershell
python scripts/style_engine.py import-pack --author example-style --card style-card.yaml
```

Mapping: abstract `voice/timeline/scene/syntax/dialogue_style/imagery` fields and quantified statistics become `traits` (card v2 adds `emotion_writing` methods and lexeme-density numbers here too); plotline weights, serial-rhythm numbers and `reward_rhythm` intervals become `scene_controls`; card `imagery.taboo` entries extend `negative_constraints`. Unfilled template defaults (`0`, `""`, `[]`) are skipped; an all-empty card is rejected.

The bridge enforces red lines programmatically:

- `meta.target_work` must not leak into any pack field;
- any value containing curly Chinese quotes (`“ ”`) is treated as a source-excerpt leak and rejected;
- `plotlines` weights must sum to 1.0 (±0.06).

Cards use a restricted YAML subset (mappings, scalars, lists, inline comments — no multiline scalars or anchors), parsed without third-party dependencies.

After importing, `status`, `build`, `prepare`, and `audit-overlap` work on the new author as usual. Packs created by the bridge carry no `families`; add them manually to `pack.json` if the corpus has subdirectories worth routing.

## Data and rights

Only index material you are authorized to use. Keep copyrighted corpora and generated databases outside public repositories. An overlap warning is a manual review signal, not a legal conclusion.

The code, scripts, templates, and documentation in this repository are released under the [MIT License](LICENSE). That license covers the Software only — it does **not** grant any right to a literary corpus, an author style pack (`pack.json` / `style-card.yaml`), its retrieval database, or prose generated with them; those remain with their respective owners and need separate authorization to distribute.
