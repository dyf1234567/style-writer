# style-writer

## 一句话创建作者包和索引

在 Codex 中发送以下请求，替换其中的占位符；不要直接在终端执行：

```text
$style-writer 分析“<语料目录>”中的作品，创建作者包“<作者标识>”，保存到“<作者包根目录>”；使用本地 Ollama 的 bge-m3 建立混合索引，保存到“<索引根目录>”。完成后做一次实际检索验收，不覆盖同名作者包或已有索引。
```

已有作者包，只需首次建库：

```text
$style-writer 为已有作者包“<作者标识>”建立混合索引。作者包根目录为“<作者包根目录>”，语料为“<语料目录>”，索引保存到“<索引根目录>”。使用本地 Ollama 的 bge-m3，完成后验证检索；若已有同名索引，先说明再继续。
```

作者标识使用小写字母、数字、短横线或下划线，如 `example-author`。
已有目录配置时可以省略根目录；缺失或冲突时 Codex 会询问。
完整流程与验收要求见 [新作者初始化](references/new-author.md)。
这是自然语言任务入口，不是单个全自动程序：分析和风格卡仍由宿主阅读、判断、填写。
不想使用向量时可明确要求“只建 FTS5 索引”；只要静态作者包时无需建库。

索引按作者标识保存为 `<索引根目录>/<作者标识>.sqlite3`，同一作者可供多个新小说项目复用。
新小说正文不会自动加入作者语料库；新增另一作者才需要单独建库。
当前 `build` 为全量重建，没有内置增量缓存或断点续建；`prepare` 不会自动建库。

## 本次升级注意事项

- 升级后重新运行 `build`，补齐旧索引遗漏的短文和末尾片段。
- `import-pack --force` 保留已有语料、作品分组和排除规则；显式参数可以覆盖显示名和语料环境变量。
- 重合审计只有每个片段都有候选且无警告时才返回 `clean`；这不是全语料穷举或原创性证明。
- 空语料或汉字不足时，导入结果明确标记原文比对未执行。向量维度不匹配时会报告降级原因。
- YAML 引号与列表支持范围见 [作者包约定](references/pack-contract.md)，索引与审计流程见 [工作流](references/workflows.md)。

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

`prepare` reports `mode` plus a `retrieval_note` explaining any degradation: `static` means the pack profile alone (no index built), `no-match` means the index was searched and nothing was retrieved, and a `--family` that selects no passages is an error rather than an empty result. `audit-overlap` returns `verdict: clean | review | inconclusive`; it exits non-zero unless it actually compared candidates, so a missing index or an empty scope cannot be mistaken for a clean bill of health.

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
- any value containing a source quote mark (`“ ” ‘ ’ 「 」 『 』`) is treated as a
  source-excerpt leak and rejected — corner-bracket typesetting is what used to slip through;
- `plotlines` weights must sum to 1.0 (±0.06);
- with a corpus available (`--corpus-root <dir|file>`, otherwise the pack's corpus
  environment variable), any pack field sharing **10 or more consecutive Chinese characters**
  with the source is rejected. The error names the field, never the matched text:
  `python scripts/style_engine.py import-pack --author example-style --card style-card.yaml --corpus-root ~/corpus/example`.
  When no corpus is reachable the import still succeeds but says so in `warnings` — a check
  that did not run is not a check that passed.

Cards use a restricted YAML subset (mappings, scalars, lists, inline comments — no multiline scalars or anchors), parsed without third-party dependencies.

After importing, `status`, `build`, `prepare`, and `audit-overlap` work on the new author as usual. Packs created by the bridge carry no `families`; add them manually to `pack.json` if the corpus has subdirectories worth routing.

## Data and rights

Only index material you are authorized to use. Keep copyrighted corpora and generated databases outside public repositories. An overlap warning is a manual review signal, not a legal conclusion.

The code, scripts, templates, and documentation in this repository are released under the [MIT License](LICENSE). That license covers the Software only — it does **not** grant any right to a literary corpus, an author style pack (`pack.json` / `style-card.yaml`), its retrieval database, or prose generated with them; those remain with their respective owners and need separate authorization to distribute.
