# style-writer

一个可移植的中文写作风格 Skill：从参考作品中提炼抽象风格卡，创建作者包，
并通过 SQLite FTS5 与可选的本地 Ollama 向量检索提供场景写作参考。
支持静态包、作品分组和连续原文重合审计。目标是高层特征启发，不是一比一复刻或冒充作者；
当前项目的人物、设定、事实和既有声音始终优先。
风格卡与作者包只提炼方法，不带入原作情节、人名或原文（Method only — never plot, names, or prose）。

- `analysis/`：风格识别，量化与精读后生成 `style-card.yaml`。
- `scripts/style_engine.py`：通过 `import-pack` 导入作者包，构建索引、检索风格上下文和审计重合。

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

未加引号的 `null`、`Null`、`NULL`、`~` 解析为空值并跳过；带引号的 `"null"` 是字符串，
不能用于数值字段。数值字段拒绝布尔值和非有限数。字符串列表内含冒号加空格时请加引号，
例如 `"避免: 连续感叹号"`；映射或数值不能自动转换为约束文本。

导入还将显式空值的字段路径保存为 `explicit_null_fields`，供 `status` 和 `prepare` 顶层元数据查看。
只记录“本次卡片显式填写为空”，不推断未知、不适用或测量失败等原因；不进入 `writing_context`。
缺省字段、空字符串和带引号的 `"null"` 不记为显式空值，重新导入时记录随新卡片更新。

### 损坏作者包恢复

`--force` 保留旧配置，旧包损坏或 slug/schema 不符时会停止并给出提示。
优先修复原 JSON。只有确认放弃旧运行配置时才执行：

```powershell
python scripts/style_engine.py import-pack --author example-style --card style-card.yaml --force --discard-existing-config
```

恢复参数必须与 `--force` 同现；会重置显示名、语料环境变量、作品分组、默认/未匹配分组和排除规则，
显式显示名或语料环境变量参数仍优先。卡片和红线检查通过后才写同目录临时文件并原子替换旧包。
检查失败或写入/替换失败时保留旧文件；权限问题不会被当成可忽略的损坏配置。

After importing, `status`, `build`, `prepare`, and `audit-overlap` work on the new author as usual. Packs created by the bridge carry no `families`; add them manually to `pack.json` if the corpus has subdirectories worth routing.

## 迁移说明

- 仅当索引仍由 `ddac4fe` 之前的分块代码生成时，建议重新 `build` 以补齐可能遗漏的短文和尾部片段。
  已使用新版重建的索引，无需因本次导入修复或文档更新再次重建。
- `import-pack --force` 保留旧运行配置；不要把 `--discard-existing-config` 当成常规更新参数。
- 新构建的索引保存 `chunker_version`。`status`、`query`、`prepare` 返回 `index_compatibility` 和 `warnings`：
  `current` 为当前标记，`unknown` 为缺少标记，`mismatch` 为不匹配，`no-index` 为尚无索引。
  缺少标记只表示无法确认，并不证明尾部遗漏；包括此前已用新分块代码重建但未写标记的索引。
  检测不会重写索引或自动重建，`reclassify` 也不会补标记。确认需要重建后再运行 `build`。
  索引流程见 [工作流](references/workflows.md)，YAML 支持范围见 [作者包约定](references/pack-contract.md)。

## Data and rights

Only index material you are authorized to use. Keep copyrighted corpora and generated databases outside public repositories. An overlap warning is a manual review signal, not a legal conclusion.

The code, scripts, templates, and documentation in this repository are released under the [MIT License](LICENSE). That license covers the Software only — it does **not** grant any right to a literary corpus, an author style pack (`pack.json` / `style-card.yaml`), its retrieval database, or prose generated with them; those remain with their respective owners and need separate authorization to distribute.
