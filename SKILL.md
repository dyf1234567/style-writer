---
name: style-writer
description: Build, query, and apply portable author-inspired prose style packs for Chinese writing, with optional local hybrid retrieval, family routing, and source-overlap auditing. Use for style analysis, reusable style packs, style-guided drafting or revision; use serial-fiction-studio as the primary workflow when long-form story continuity is the main task.
---

# Style Writer

Use author packs as reusable writing references, not as claims of authorship or exact replicas. Preserve the user's plot, characters, facts, and project voice.

## Route the task

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
- Before releasing substantial prose, run `audit-overlap`; manually inspect every warning.
- The analysis-stage style card is the single source of truth for abstract style. Move a card into a runtime pack only through `import-pack`; never hand-copy prose, names, or plot from the analysis stage into a pack.

## Compact drafting context

Use only the output under `writing_context` from `prepare`. Apply its high-level traits, retrieval metrics, scene controls, and negative constraints. The default context contains no source prose.

When a project voice or character persona conflicts with an author pack, the project wins.
