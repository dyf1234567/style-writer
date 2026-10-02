---
name: style-writer
description: Build, query, and apply portable author-inspired prose style packs for Chinese writing, with optional local hybrid retrieval, family routing, and source-overlap auditing. Use for style analysis, reusable style packs, style-guided drafting or revision; use serial-fiction-studio as the primary workflow when long-form story continuity is the main task.
---

# Style Writer

Use author packs as reusable writing references, not as claims of authorship or exact replicas. Preserve the user's plot, characters, facts, and project voice.

## Route the task

- 用户要求“新增作者包并建立索引”“从作品开始配置新作者”或“为已有作者包首次建库”时，先读 [新作者初始化](references/new-author.md)，串联分析、导入、依赖检查、构建和检索验收。这是宿主执行流程，不是新增 CLI 命令；仅请求分析或静态包时，不扩展为向量构建。
- To turn a reference work into a style profile (author analysis), read [analysis/README.md](analysis/README.md): measure the corpus, fill the report (emotion writing and reward rhythm included since card v2), converge to a `style-card.yaml`, then `import-pack` it into a runtime pack.
- For ordinary style-guided writing or revision, resolve the author pack and run `scripts/style_engine.py prepare` before drafting.
- For building or refreshing a corpus index, read [references/workflows.md](references/workflows.md).
- For creating or moving author packs, read [references/pack-contract.md](references/pack-contract.md).
- When continuing a long novel, let `serial-fiction-studio` own continuity and use this Skill only to provide compact style context.

## Invariants

- Treat style, source lore, and project memory as separate stores. Style retrieval never enables source lore implicitly.
- Prefer one work family or period for a scene. Do not average incompatible eras merely because they share an author.
- Default `prepare` output to non-verbatim retrieval metrics. Include source excerpts only after the user explicitly requests them; never silently place retrieved prose in a writing prompt.
- A static profile is a valid portable fallback. FTS5 adds lexical retrieval; embeddings add semantic retrieval but are optional machine-local dependencies.
- Keep corpora and indexes outside the Skill. The Skill must remain small and copyable.
- Before releasing substantial prose, run `audit-overlap`; manually inspect every warning. Only `verdict: clean` means the run compared every probe; `inconclusive` (nothing retrieved, no index, empty scope) is not a pass. Contiguous overlap is what it detects — reworded copying is not.
- The analysis-stage style card is the single source of truth for abstract style. Move a card into a runtime pack only through `import-pack`; never hand-copy prose, names, or plot from the analysis stage into a pack.

## 风格卡确认

- 本次写作没有使用用户选定的已有作者文风包时，若需要新编写项目风格卡，先展示候选卡并等待用户明确确认。候选卡应概括叙述口吻、句式节奏、意象、对白特点、禁用项，以及待定内容；不把未测量的数值当作已确认结论。
- 未确认的卡片可以保存在外部工作目录并标为草案，但不得导入或覆盖正式包、绑定为项目正式风格，或据此开始正式写作。创建作者包/索引的任务授权不等于确认候选卡；用户要求调整时，展示修订版再确认。确认过的卡不因换章或换会话重复询问，实质改变风格时才确认变更。
- 用户选定并使用已有作者文风包时，直接沿用该包，不额外要求确认或自动创建新卡。仅发现本机存在包不代表已选用；新分析并创建作者包也不属于复用已有包。即使复用已有包，另行新增或实质改写的项目风格卡仍须确认。
- 这是宿主对话流程，不是 CLI 已强制执行的校验；独立运行 `import-pack` 不会自动获得用户确认。将用户确认的版本与范围记在项目既有记录中，不新增确认数据库。

## Compact drafting context

Use only the output under `writing_context` from `prepare`. Apply its high-level traits, retrieval metrics, scene controls, and negative constraints. The default context contains no source prose.

Inspect top-level `warnings` and `index_compatibility` before reporting retrieval readiness; an unknown chunker version is uncertainty, not proof of missing passages. Explain actionable warnings without automatically rebuilding. `explicit_null_fields` is provenance metadata for empty card fields, not writing instructions or proof of measurement failure.

When a project voice or character persona conflicts with an author pack, the project wins.
