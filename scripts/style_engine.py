#!/usr/bin/env python3
"""Portable author-style profiles with optional FTS5 + embedding retrieval."""

from __future__ import annotations

import argparse
import array
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable, Iterator, Sequence

SCHEMA_VERSION = 1
TEXT_SUFFIXES = {".txt", ".md", ".text"}
DEFAULT_MODEL = "bge-m3"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


def _json_print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _default_author_root() -> Path:
    configured = os.getenv("AUTHOR_STYLE_HOME")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".style-writer" / "authors"


def _default_index_root() -> Path:
    configured = os.getenv("STYLE_INDEX_HOME")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".style-writer" / "indexes"


def resolve_pack(author: str, authors_root: str | Path | None = None) -> tuple[Path, dict]:
    root = Path(authors_root).expanduser() if authors_root else _default_author_root()
    pack_dir = root / author
    pack_file = pack_dir / "pack.json"
    if not pack_file.exists():
        raise FileNotFoundError(f"author pack not found: {pack_file}")
    pack = json.loads(pack_file.read_text(encoding="utf-8"))
    if int(pack.get("schema_version", 0)) != SCHEMA_VERSION:
        raise ValueError(f"unsupported pack schema: {pack.get('schema_version')}")
    if pack.get("slug") != author:
        raise ValueError(f"pack slug mismatch: expected {author!r}")
    return pack_dir, pack


def resolve_corpus(pack: dict, explicit: str | Path | None = None) -> Path:
    if explicit:
        root = Path(explicit).expanduser()
    else:
        env_name = str(pack.get("corpus", {}).get("env", "")).strip()
        value = os.getenv(env_name) if env_name else None
        if not value:
            raise FileNotFoundError(
                f"corpus path is not configured; set {env_name or 'the pack corpus env'} or pass --corpus-root"
            )
        root = Path(value).expanduser()
    if not root.is_dir():
        raise FileNotFoundError(f"corpus directory not found: {root}")
    return root.resolve()


def resolve_index(author: str, index_root: str | Path | None = None) -> Path:
    root = Path(index_root).expanduser() if index_root else _default_index_root()
    return root / f"{author}.sqlite3"


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "big5"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u3000", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def iter_chunks(text: str, target: int = 900, maximum: int = 1400, minimum: int = 120) -> Iterator[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(paragraphs) <= 1:
        paragraphs = [p.strip() for p in text.splitlines() if p.strip()]
    buf = ""
    for para in paragraphs:
        if len(para) > maximum:
            parts = [p for p in re.split(r"(?<=[。！？!?])", para) if p]
        else:
            parts = [para]
        for part in parts:
            candidate = f"{buf}\n{part}".strip() if buf else part
            if buf and len(candidate) > maximum:
                if len(buf) >= minimum:
                    yield buf
                buf = part
            else:
                buf = candidate
            if len(buf) >= target:
                yield buf
                buf = ""
    if len(buf) >= minimum:
        yield buf


def lexical_tokens(text: str) -> list[str]:
    lowered = text.lower()
    latin = re.findall(r"[a-z0-9_]{2,}", lowered)
    runs = re.findall(r"[\u3400-\u9fff]+", lowered)
    chinese: list[str] = []
    for run in runs:
        chinese.extend(run[i : i + 2] for i in range(max(0, len(run) - 1)))
        if len(run) == 1:
            chinese.append(run)
    return latin + chinese


def fts_query(text: str, limit: int = 40) -> str:
    seen: set[str] = set()
    terms = []
    for token in lexical_tokens(text):
        if token not in seen:
            seen.add(token)
            terms.append('"' + token.replace('"', '""') + '"')
        if len(terms) >= limit:
            break
    return " OR ".join(terms)


def classify_family(relpath: str, pack: dict) -> str:
    lowered = relpath.lower()
    parent = lowered.split("/", 1)[0]
    # default_family controls retrieval when callers omit --family.  It must not
    # silently classify every unknown source file as that family.
    best = (0, str(pack.get("unmatched_family", pack.get("default_family", "default"))))
    for family in pack.get("families", []):
        for pattern in family.get("patterns", []):
            p = str(pattern).lower()
            if p and p in lowered:
                # Directory taxonomy is stronger evidence than a title mention.
                score = len(p) + (1000 if p in parent else 0)
                if score > best[0]:
                    best = (score, str(family["id"]))
    return best[1]


def is_lore(relpath: str, pack: dict) -> bool:
    lowered = relpath.lower()
    return any(str(p).lower() in lowered for p in pack.get("exclude_patterns", []))


def corpus_signature(files: Sequence[Path], root: Path) -> str:
    h = hashlib.sha256()
    for path in files:
        stat = path.stat()
        h.update(path.relative_to(root).as_posix().encode("utf-8"))
        h.update(str(stat.st_size).encode("ascii"))
        h.update(str(stat.st_mtime_ns).encode("ascii"))
    return h.hexdigest()


def _post_json(url: str, payload: dict, timeout: int = 120) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def embed_texts(texts: Sequence[str], provider: str, model: str, endpoint: str | None = None) -> list[list[float]]:
    if provider == "none":
        return []
    if provider == "hash":
        vectors = []
        for text in texts:
            vec = [0.0] * 64
            for tok in lexical_tokens(text):
                digest = hashlib.blake2b(tok.encode("utf-8"), digest_size=8).digest()
                pos = int.from_bytes(digest[:4], "little") % len(vec)
                vec[pos] += 1.0 if digest[4] & 1 else -1.0
            vectors.append(_normalize_vector(vec))
        return vectors
    if provider != "ollama":
        raise ValueError(f"unsupported embedding provider: {provider}")
    base = (endpoint or os.getenv("STYLE_VECTOR_OLLAMA_URL") or DEFAULT_OLLAMA_URL).rstrip("/")
    data = _post_json(f"{base}/api/embed", {"model": model, "input": list(texts)})
    values = data.get("embeddings")
    if not isinstance(values, list) or len(values) != len(texts):
        raise RuntimeError("Ollama returned an unexpected embedding response")
    return [_normalize_vector([float(v) for v in row]) for row in values]


def _normalize_vector(vec: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    return [float(v / norm) for v in vec] if norm else [float(v) for v in vec]


def _pack_vector(vec: Sequence[float]) -> bytes:
    return array.array("f", vec).tobytes()


def _unpack_vector(blob: bytes) -> array.array:
    values = array.array("f")
    values.frombytes(blob)
    return values


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        PRAGMA journal_mode=WAL;
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE passages (
            id INTEGER PRIMARY KEY,
            relpath TEXT NOT NULL,
            family TEXT NOT NULL,
            is_lore INTEGER NOT NULL DEFAULT 0,
            chunk_no INTEGER NOT NULL,
            text TEXT NOT NULL,
            text_hash TEXT NOT NULL UNIQUE
        );
        CREATE VIRTUAL TABLE passages_fts USING fts5(passage_id UNINDEXED, tokens);
        CREATE TABLE vectors (
            passage_id INTEGER PRIMARY KEY REFERENCES passages(id) ON DELETE CASCADE,
            dim INTEGER NOT NULL,
            embedding BLOB NOT NULL
        );
        CREATE INDEX idx_passages_family ON passages(family, is_lore);
        """
    )


def build_index(
    author: str,
    authors_root: str | Path | None = None,
    index_root: str | Path | None = None,
    corpus_root: str | Path | None = None,
    provider: str = "ollama",
    model: str = DEFAULT_MODEL,
    endpoint: str | None = None,
    batch_size: int = 32,
    include_lore: bool = False,
) -> dict:
    _, pack = resolve_pack(author, authors_root)
    corpus = resolve_corpus(pack, corpus_root)
    index_path = resolve_index(author, index_root)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in corpus.rglob("*") if p.is_file() and p.suffix.lower() in TEXT_SUFFIXES)
    temp_path = index_path.with_suffix(index_path.suffix + ".building")
    if temp_path.exists():
        temp_path.unlink()
    conn = sqlite3.connect(temp_path)
    _create_schema(conn)
    file_hashes: set[str] = set()
    chunk_hashes: set[str] = set()
    rows: list[tuple[str, str, int, int, str, str]] = []
    skipped_lore = skipped_duplicate_files = 0
    try:
        for file_path in files:
            rel = file_path.relative_to(corpus).as_posix()
            lore = is_lore(rel, pack)
            if lore and not include_lore:
                skipped_lore += 1
                continue
            raw = file_path.read_bytes()
            file_hash = hashlib.sha256(raw).hexdigest()
            if file_hash in file_hashes:
                skipped_duplicate_files += 1
                continue
            file_hashes.add(file_hash)
            text = normalize_text(read_text(file_path))
            family = classify_family(rel, pack)
            for chunk_no, chunk in enumerate(iter_chunks(text)):
                digest = hashlib.sha256(chunk.encode("utf-8")).hexdigest()
                if digest in chunk_hashes:
                    continue
                chunk_hashes.add(digest)
                rows.append((rel, family, int(lore), chunk_no, chunk, digest))
        conn.executemany(
            "INSERT INTO passages(relpath,family,is_lore,chunk_no,text,text_hash) VALUES(?,?,?,?,?,?)",
            rows,
        )
        selected = conn.execute("SELECT id,text FROM passages ORDER BY id").fetchall()
        conn.executemany(
            "INSERT INTO passages_fts(passage_id,tokens) VALUES(?,?)",
            ((pid, " ".join(lexical_tokens(text))) for pid, text in selected),
        )
        vector_dim = 0
        if provider != "none":
            total = len(selected)
            for start in range(0, total, max(1, batch_size)):
                batch = selected[start : start + max(1, batch_size)]
                vectors = embed_texts([text for _, text in batch], provider, model, endpoint)
                if vectors:
                    vector_dim = len(vectors[0])
                conn.executemany(
                    "INSERT INTO vectors(passage_id,dim,embedding) VALUES(?,?,?)",
                    ((pid, len(vec), _pack_vector(vec)) for (pid, _), vec in zip(batch, vectors)),
                )
                if total and (start == 0 or start + len(batch) == total or (start // max(1, batch_size)) % 10 == 0):
                    print(f"embedded {min(start + len(batch), total)}/{total}", file=sys.stderr, flush=True)
        meta = {
            "schema_version": SCHEMA_VERSION,
            "author": author,
            "provider": provider,
            "model": model if provider != "none" else "",
            "vector_dim": vector_dim,
            "corpus_signature": corpus_signature(files, corpus),
            "chunk_target": 900,
            "chunk_maximum": 1400,
        }
        conn.executemany(
            "INSERT INTO meta(key,value) VALUES(?,?)",
            ((key, json.dumps(value, ensure_ascii=False)) for key, value in meta.items()),
        )
        conn.commit()
    except Exception:
        conn.close()
        if temp_path.exists():
            temp_path.unlink()
        raise
    conn.close()
    os.replace(temp_path, index_path)
    return {
        "ok": True,
        "author": author,
        "index": str(index_path),
        "files_seen": len(files),
        "unique_files": len(file_hashes),
        "passages": len(rows),
        "skipped_lore_files": skipped_lore,
        "skipped_duplicate_files": skipped_duplicate_files,
        "provider": provider,
        "model": model if provider != "none" else None,
        "vector_dim": vector_dim,
    }


def _meta(conn: sqlite3.Connection) -> dict:
    return {key: json.loads(value) for key, value in conn.execute("SELECT key,value FROM meta")}


def _connect_readonly(path: Path) -> sqlite3.Connection:
    """Open a completed index without asking SQLite to create WAL/lock files."""
    uri = path.resolve().as_uri() + "?mode=ro&immutable=1"
    return sqlite3.connect(uri, uri=True)


def _median(values: Sequence[int]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def derive_style_metrics(texts: Sequence[str]) -> dict:
    """Reduce retrieved prose to non-verbatim controls safe for prompt injection."""
    joined = "\n\n".join(texts)
    total = max(1, len(joined))
    sentences = [s.strip() for s in re.split(r"(?<=[。！？!?])", joined) if s.strip()]
    paragraphs = [p.strip() for p in re.split(r"\n+", joined) if p.strip()]
    quoted = re.findall(r"[“\"]([^”\"]+)[”\"]", joined)
    sentence_lengths = [len(s) for s in sentences]
    paragraph_lengths = [len(p) for p in paragraphs]
    punctuation = {
        mark: round(joined.count(mark) * 1000 / total, 2)
        for mark in ("。", "，", "！", "？", "…")
    }
    return {
        "sample_count": len(texts),
        "average_sentence_chars": round(sum(sentence_lengths) / max(1, len(sentence_lengths)), 1),
        "median_paragraph_chars": round(_median(paragraph_lengths), 1),
        "short_paragraph_ratio": round(
            sum(1 for length in paragraph_lengths if length <= 30) / max(1, len(paragraph_lengths)), 3
        ),
        "quoted_text_ratio": round(sum(len(value) for value in quoted) / total, 3),
        "punctuation_per_1000_chars": punctuation,
    }


def _filters(family: str | None, source_lore: bool, alias: str = "p") -> tuple[str, list[object]]:
    clauses = [f"{alias}.is_lore = ?"]
    params: list[object] = [1 if source_lore else 0]
    if family:
        clauses.append(f"{alias}.family = ?")
        params.append(family)
    return " AND ".join(clauses), params


def query_index(
    author: str,
    query: str,
    authors_root: str | Path | None = None,
    index_root: str | Path | None = None,
    family: str | None = None,
    limit: int = 5,
    source_lore: bool = False,
    endpoint: str | None = None,
) -> dict:
    resolve_pack(author, authors_root)
    index_path = resolve_index(author, index_root)
    if not index_path.exists():
        return {"ok": False, "mode": "static", "error": f"index not found: {index_path}", "hits": []}
    conn = _connect_readonly(index_path)
    conn.row_factory = sqlite3.Row
    meta = _meta(conn)
    where, params = _filters(family, source_lore)
    lexical: list[sqlite3.Row] = []
    fq = fts_query(query)
    if fq:
        lexical = conn.execute(
            f"""SELECT p.*, bm25(passages_fts) AS rank
                FROM passages_fts JOIN passages p ON p.id=passages_fts.passage_id
                WHERE passages_fts MATCH ? AND {where}
                ORDER BY rank LIMIT ?""",
            [fq, *params, max(limit * 8, 30)],
        ).fetchall()
    scores: dict[int, float] = {}
    evidence: dict[int, set[str]] = {}
    for rank, row in enumerate(lexical, 1):
        pid = int(row["id"])
        scores[pid] = scores.get(pid, 0.0) + 1.0 / (60 + rank)
        evidence.setdefault(pid, set()).add("fts5")
    vector_error = None
    vector_used = False
    provider = str(meta.get("provider", "none"))
    if provider != "none":
        try:
            qvec = embed_texts([query], provider, str(meta.get("model", DEFAULT_MODEL)), endpoint)[0]
            vector_rows = conn.execute(
                f"""SELECT p.*,v.embedding FROM vectors v JOIN passages p ON p.id=v.passage_id
                    WHERE {where}""",
                params,
            ).fetchall()
            ranked = sorted(
                ((_dot(qvec, _unpack_vector(row["embedding"])), row) for row in vector_rows),
                key=lambda item: item[0],
                reverse=True,
            )[: max(limit * 8, 30)]
            vector_used = True
            for rank, (_, row) in enumerate(ranked, 1):
                pid = int(row["id"])
                scores[pid] = scores.get(pid, 0.0) + 1.0 / (60 + rank)
                evidence.setdefault(pid, set()).add("vector")
        except Exception as exc:
            vector_error = str(exc)
    if not scores:
        conn.close()
        return {
            "ok": True,
            "mode": "static",
            "hits": [],
            "style_metrics": {},
            "vector_error": vector_error,
        }
    ids = [pid for pid, _ in sorted(scores.items(), key=lambda item: item[1], reverse=True)[:limit]]
    placeholders = ",".join("?" for _ in ids)
    rows = {int(row["id"]): row for row in conn.execute(f"SELECT * FROM passages WHERE id IN ({placeholders})", ids)}
    selected_texts = [str(rows[pid]["text"]) for pid in ids]
    hits = []
    for pid in ids:
        row = rows[pid]
        text = str(row["text"])
        hits.append({
            "id": pid,
            "relpath": row["relpath"],
            "family": row["family"],
            "score": round(scores[pid], 8),
            "evidence": sorted(evidence.get(pid, set())),
            "snippet": text[:360] + ("…" if len(text) > 360 else ""),
        })
    conn.close()
    mode = "hybrid" if lexical and vector_used else ("fts5" if lexical else "vector")
    return {
        "ok": True,
        "mode": mode,
        "hits": hits,
        "style_metrics": derive_style_metrics(selected_texts),
        "vector_error": vector_error,
        "index": str(index_path),
    }


def prepare_context(
    author: str,
    query: str,
    authors_root: str | Path | None = None,
    index_root: str | Path | None = None,
    family: str | None = None,
    limit: int = 4,
    source_lore: bool = False,
    endpoint: str | None = None,
    include_excerpts: bool = False,
) -> dict:
    _, pack = resolve_pack(author, authors_root)
    chosen_family = family or str(pack.get("default_family", "default"))
    retrieval = query_index(author, query, authors_root, index_root, chosen_family, limit, source_lore, endpoint)
    family_label = chosen_family
    for entry in pack.get("families", []):
        if entry.get("id") == chosen_family:
            family_label = str(entry.get("label", chosen_family))
            break
    context = {
        "positioning": pack.get("positioning", "high-level inspiration only"),
        "family": {"id": chosen_family, "label": family_label},
        "traits": pack.get("traits", []),
        "scene_controls": pack.get("scene_controls", []),
        "negative_constraints": pack.get("negative_constraints", []),
        "retrieval_metrics": retrieval.get("style_metrics", {}),
        "project_priority": "项目人物、世界观、事实、时间线和既有人格卡优先",
        "evidence_instruction": (
            "已将检索结果压缩为统计特征；写作上下文不含原文"
            if not include_excerpts
            else "原文片段已由用户显式启用，只用于分析，不得续写、拼接或近似改写"
        ),
    }
    raw_hits = list(retrieval.get("hits", []))
    evidence = raw_hits if include_excerpts else [
        {key: hit.get(key) for key in ("id", "relpath", "family", "score", "evidence")}
        for hit in raw_hits
    ]
    return {
        "ok": True,
        "author": author,
        "query": query,
        "mode": retrieval.get("mode", "static"),
        "writing_context": context,
        "evidence": evidence,
        "vector_error": retrieval.get("vector_error"),
        "source_lore": bool(source_lore),
        "excerpts_enabled": bool(include_excerpts),
    }


def status(author: str, authors_root: str | Path | None = None, index_root: str | Path | None = None) -> dict:
    pack_dir, pack = resolve_pack(author, authors_root)
    index_path = resolve_index(author, index_root)
    result = {
        "ok": True,
        "author": author,
        "pack": str(pack_dir),
        "pack_portable": True,
        "corpus_env": pack.get("corpus", {}).get("env"),
        "corpus_configured": False,
        "index": str(index_path),
        "index_exists": index_path.exists(),
        "mode": "static",
    }
    env_name = result["corpus_env"]
    if env_name and os.getenv(str(env_name)):
        result["corpus_configured"] = Path(os.environ[str(env_name)]).is_dir()
    if index_path.exists():
        conn = _connect_readonly(index_path)
        meta = _meta(conn)
        result["passages"] = conn.execute("SELECT COUNT(*) FROM passages").fetchone()[0]
        conn.close()
        result["embedding_provider"] = meta.get("provider")
        result["embedding_model"] = meta.get("model")
        result["mode"] = "hybrid" if meta.get("provider") != "none" else "fts5"
    return result


def reclassify_index(
    author: str,
    authors_root: str | Path | None = None,
    index_root: str | Path | None = None,
) -> dict:
    """Refresh family/lore metadata without recomputing embeddings."""
    _, pack = resolve_pack(author, authors_root)
    index_path = resolve_index(author, index_root)
    if not index_path.exists():
        return {"ok": False, "error": f"index not found: {index_path}"}
    conn = sqlite3.connect(index_path)
    rows = conn.execute("SELECT id,relpath FROM passages").fetchall()
    updates = [
        (classify_family(relpath, pack), int(is_lore(relpath, pack)), pid)
        for pid, relpath in rows
    ]
    conn.executemany("UPDATE passages SET family=?,is_lore=? WHERE id=?", updates)
    conn.commit()
    counts = dict(conn.execute("SELECT family,COUNT(*) FROM passages GROUP BY family").fetchall())
    conn.close()
    return {"ok": True, "author": author, "index": str(index_path), "updated": len(updates), "families": counts}


def audit_overlap(
    author: str,
    input_path: str | Path,
    authors_root: str | Path | None = None,
    index_root: str | Path | None = None,
    family: str | None = None,
    ratio_threshold: float = 0.72,
    exact_run_threshold: int = 24,
) -> dict:
    text = normalize_text(read_text(Path(input_path)))
    probes = list(iter_chunks(text, target=260, maximum=420, minimum=40))
    warnings = []
    for probe_no, probe in enumerate(probes, 1):
        result = query_index(author, probe, authors_root, index_root, family, 5, False)
        for hit in result.get("hits", []):
            source = str(hit.get("snippet", "")).rstrip("…")
            matcher = SequenceMatcher(None, probe, source, autojunk=False)
            ratio = matcher.ratio()
            longest = matcher.find_longest_match(0, len(probe), 0, len(source)).size
            if ratio >= ratio_threshold or longest >= exact_run_threshold:
                warnings.append({
                    "probe": probe_no,
                    "source": hit.get("relpath"),
                    "ratio": round(ratio, 4),
                    "longest_exact_run": longest,
                    "generated_excerpt": probe[:160],
                })
                break
    return {
        "ok": True,
        "input": str(Path(input_path)),
        "probes": len(probes),
        "warnings": warnings,
        "requires_manual_review": bool(warnings),
        "note": "命中仅表示需要人工复核，不自动判定抄袭",
    }


# ---------------------------------------------------------------- card → pack


CARD_TRAIT_LABELS = {
    "voice.person": "叙述人称",
    "voice.focalization": "聚焦方式",
    "voice.reliability": "叙述可靠性",
    "voice.distance": "叙述距离",
    "voice.tense": "时态",
    "timeline.base_order": "基准时序",
    "timeline.ellipsis_span": "典型省略",
    "scene.show_vs_tell": "展示/讲述分工",
    "scene.sensory_mix": "感官配比",
    "scene.scenery_function": "景物功能",
    "syntax.register": "语域",
    "syntax.sentence_rhythm_note": "句法节奏",
    "dialogue_style.subtext_density": "潜台词密度",
    "dialogue_style.individuality": "角色语言区分度",
    "dialogue_style.exposition_in_dialogue": "对白承载背景",
    "imagery.metaphor_type": "比喻偏好",
    "imagery.recurrence_interval": "意象复现节律",
}

CARD_CONTROL_LABELS = {
    "plotlines.switch_trigger": "切线触发",
    "plotlines.switch_transition": "切线过渡",
    "plotlines.line_close_pattern": "支线收束方式",
    "serial_rhythm.chapter_hook_type": "章末钩子",
    "serial_rhythm.hook_strength_by_position": "钩子位置差",
    "serial_rhythm.tension_relax_ratio": "张弛比",
    "serial_rhythm.words_curve_note": "篇幅曲线",
}


def _strip_comment(raw: str) -> str:
    """去掉行内注释；带引号的值只认闭合引号后的注释。"""
    text = raw.strip()
    if text.startswith("#"):
        return ""
    if text[:1] in ("\"", "'"):
        quote = text[0]
        end = text.find(quote, 1)
        return text[: end + 1] if end != -1 else text
    cut = text.find(" #")
    return text[:cut].strip() if cut != -1 else text


def _scalar(raw: str):
    text = _strip_comment(raw)
    if not text:
        return ""
    if text == "[]":
        return []
    if text[:1] == "[" and text[-1:] == "]":
        inner = text[1:-1].strip()
        return [] if not inner else [_scalar(part.strip()) for part in inner.split(",")]
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("\"", "'"):
        return text[1:-1]
    lowered = text.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return text


def _parse_restricted(text: str) -> dict:
    """只支持 style-card 模板用到的 YAML 子集：映射、标量列表、映射列表、
    行注释、流式空列表/短列表。其余（多行标量、锚点、制表符缩进）直接报错。
    """
    rows: list[tuple[int, int, str]] = []
    for no, raw in enumerate(text.splitlines(), 1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if "\t" in raw[:indent]:
            raise ValueError(f"line {no}: tabs are not supported")
        rows.append((no, indent, stripped))
    index = 0

    def parse_mapping(indent: int) -> dict:
        nonlocal index
        out: dict = {}
        while index < len(rows):
            no, cur, content = rows[index]
            if cur < indent:
                break
            if cur > indent:
                raise ValueError(f"line {no}: unexpected indentation")
            if content.startswith("- "):
                raise ValueError(f"line {no}: list item in mapping context")
            key, sep, rest = content.partition(":")
            if not sep:
                raise ValueError(f"line {no}: expect \"key: value\"")
            key = key.strip()
            index += 1
            if _strip_comment(rest):
                out[key] = _scalar(rest)
            elif index < len(rows) and rows[index][1] > indent:
                child_indent = rows[index][1]
                out[key] = (
                    parse_sequence(child_indent)
                    if rows[index][2].startswith("- ")
                    else parse_mapping(child_indent)
                )
            else:
                out[key] = ""
        return out

    def parse_sequence(indent: int) -> list:
        nonlocal index
        items: list = []
        while index < len(rows):
            no, cur, content = rows[index]
            if cur != indent or not content.startswith("- "):
                break
            body = content[2:].strip()
            index += 1
            if body[:1] in ("\"", "'") or ":" not in body:
                items.append(_scalar(body))
                continue
            key, _sep, rest = body.partition(":")
            item = {key.strip(): _scalar(rest)}
            while index < len(rows) and rows[index][1] > indent and not rows[index][2].startswith("- "):
                c_no, _c, c_content = rows[index]
                c_key, c_sep, c_rest = c_content.partition(":")
                if not c_sep:
                    raise ValueError(f"line {c_no}: expect \"key: value\" inside list item")
                item[c_key.strip()] = _scalar(c_rest)
                index += 1
            items.append(item)
        return items

    if not rows:
        return {}
    if rows[0][2].startswith("- "):
        raise ValueError("style card root must be a mapping")
    return parse_mapping(rows[0][1])


def _dig(obj: object, dotted: str):
    cur: object = obj
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _filled(value) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, (list, dict)):
        return len(value) > 0
    return value is not None


def _iter_strings(obj: object):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for value in obj.values():
            yield from _iter_strings(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _iter_strings(value)


def import_pack(
    author: str,
    card_path: str | Path,
    authors_root: str | Path | None = None,
    display_name: str | None = None,
    corpus_env: str | None = None,
    force: bool = False,
) -> dict:
    """style-card.yaml → pack.json 桥接（识别侧产物进入执行侧的唯一通道）。

    0/""/[] 视为模板未填，跳过。红线在桥里程序化把关：
    meta.target_work 禁止泄漏进 pack；任何字段值含中文弯引号视为原文摘录，拒绝。
    """
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", author):
        raise ValueError(f"invalid slug: {author}（用小写字母/数字/-/_）")
    card_file = Path(card_path).expanduser()
    if not card_file.exists():
        raise FileNotFoundError(f"style card not found: {card_file}")
    card = _parse_restricted(read_text(card_file))
    if not isinstance(card, dict) or not card:
        raise ValueError("style card is empty or not a mapping")

    traits: list[str] = []
    controls: list[str] = []
    for dotted, label in CARD_TRAIT_LABELS.items():
        value = _dig(card, dotted)
        if isinstance(value, str) and value.strip():
            traits.append(f"{label}：{value.strip()}")
    for dotted, label in CARD_CONTROL_LABELS.items():
        value = _dig(card, dotted)
        if isinstance(value, str) and value.strip():
            controls.append(f"{label}：{value.strip()}")

    for dotted, label in (
        ("voice.narrator_comment_ratio", "叙述者评论占比"),
        ("timeline.analepsis_ratio", "倒叙占比"),
        ("timeline.prolepsis_ratio", "预叙占比"),
        ("timeline.scene_time_ratio", "叙述/故事时长比"),
        ("scene.description_ratio", "描写占比"),
    ):
        value = _dig(card, dotted)
        if _filled(value):
            traits.append(f"{label}：{value}")

    domains = _dig(card, "imagery.semantic_domains")
    if isinstance(domains, list) and domains:
        traits.append("高频语义域：" + "、".join(str(d) for d in domains))

    wc = _dig(card, "serial_rhythm.words_per_chapter")
    if isinstance(wc, dict) and _filled(wc.get("median")):
        traits.append(f"章字数：中位 {wc['median']}（p10 {wc.get('p10', '-')} / p90 {wc.get('p90', '-')}）")
    sl = _dig(card, "syntax.sentence_len")
    if isinstance(sl, dict):
        bits = [f"中位 {sl['median']}" for _ in [0] if _filled(sl.get("median"))]
        bits += [f"p90 {sl['p90']}" for _ in [0] if _filled(sl.get("p90"))]
        bits += [f"短句≤10 占 {sl['short_le10_ratio']}" for _ in [0] if _filled(sl.get("short_le10_ratio"))]
        bits += [f"长句≥40 占 {sl['long_ge40_ratio']}" for _ in [0] if _filled(sl.get("long_ge40_ratio"))]
        if bits:
            traits.append("句长：" + " / ".join(bits))
    pl = _dig(card, "syntax.paragraph_len")
    if isinstance(pl, dict):
        bits = [f"中位 {pl['median']}" for _ in [0] if _filled(pl.get("median"))]
        bits += [f"p90 {pl['p90']}" for _ in [0] if _filled(pl.get("p90"))]
        bits += [f"短段占比 {pl['short_para_ratio']}" for _ in [0] if _filled(pl.get("short_para_ratio"))]
        if bits:
            traits.append("段长：" + " / ".join(bits))
    if _filled(_dig(card, "syntax.dialogue_char_ratio")) or _filled(_dig(card, "syntax.dialogue_blocks_per_1k")):
        traits.append(
            f"对话：字符占比 {_dig(card, 'syntax.dialogue_char_ratio') or 0}"
            f" / 每千字 {_dig(card, 'syntax.dialogue_blocks_per_1k') or 0} 块"
        )
    punct = _dig(card, "syntax.punct_per_1k")
    if isinstance(punct, dict):
        bits = [
            f"{name} {punct[name]}"
            for name in ("exclam", "question", "ellipsis", "dash")
            if _filled(punct.get(name))
        ]
        if bits:
            traits.append("标点每千字：" + "、".join(bits))

    lines = _dig(card, "plotlines.lines")
    weights: list[float] = []
    if isinstance(lines, list):
        for entry in lines:
            if not isinstance(entry, dict):
                continue
            parts = [str(entry[k]) for k in ("role", "function") if _filled(entry.get(k))]
            if _filled(entry.get("pov")):
                parts.append(f"视角 {entry['pov']}")
            if _filled(entry.get("weight")):
                parts.append(f"权重 {entry['weight']}")
                weights.append(float(entry["weight"]))
            if parts:
                controls.append(f"情节线 {entry.get('id', '?')}：" + "·".join(parts))
    if weights and abs(sum(weights) - 1.0) > 0.06:
        raise ValueError(f"plotlines 权重和应为 1.0，实际 {round(sum(weights), 2)}（回卡内核对）")
    for dotted, text in (
        ("plotlines.count", "活跃线数：{v}"),
        ("plotlines.convergence_every", "主线副线交汇：每 {v} 章"),
        ("serial_rhythm.scenes_per_chapter", "章均场景数：{v}"),
        ("serial_rhythm.arc_length", "高潮弧跨度：{v} 章"),
        ("serial_rhythm.arc_recovery", "高潮后缓冲：{v} 章"),
        ("serial_rhythm.cliffhanger_frequency", "强断章频率：{v}"),
    ):
        value = _dig(card, dotted)
        if _filled(value):
            controls.append(text.format(v=value))
    scene_words = _dig(card, "scene.scene_words")
    if isinstance(scene_words, list) and len(scene_words) == 2 and any(_filled(w) for w in scene_words):
        controls.append(f"单场景字数区间：{scene_words[0]}-{scene_words[1]}")

    negatives = ["禁止复刻原作语句、人名、地名与具体情节"]
    taboo = _dig(card, "imagery.taboo")
    if isinstance(taboo, list):
        negatives += [str(t) for t in taboo if _filled(t)]
    if not traits:
        raise ValueError("style card 未填写任何方法字段，无可导入内容")

    sample_range = str(_dig(card, "meta.sample_range") or "").strip()
    pack = {
        "schema_version": SCHEMA_VERSION,
        "slug": author,
        "display_name": display_name or f"{author}-inspired",
        "positioning": "高维抽象特征，仅方法不含内容"
        + (f"（样本 {sample_range}）" if sample_range else ""),
        "corpus": {"env": corpus_env or f"{re.sub(r'[^A-Z0-9]', '_', author.upper())}_CORPUS"},
        "families": [],
        "default_family": "other",
        "unmatched_family": "other",
        "exclude_patterns": ["设定集", "人物小传"],
        "traits": traits,
        "scene_controls": controls,
        "negative_constraints": negatives,
    }

    target_work = str(_dig(card, "meta.target_work") or "").strip()
    if target_work and target_work in json.dumps(pack, ensure_ascii=False):
        raise ValueError(f"红线：meta.target_work「{target_work}」泄漏进了卡片字段，先回卡内清除")
    for value in _iter_strings(pack):
        if "“" in value or "”" in value:
            raise ValueError(f"红线：字段值含中文弯引号，疑似原文摘录：{value[:40]}")

    root = Path(authors_root).expanduser() if authors_root else _default_author_root()
    pack_dir = root / author
    pack_file = pack_dir / "pack.json"
    if pack_file.exists() and not force:
        raise FileExistsError(f"pack exists: {pack_file}（--force 覆盖）")
    pack_dir.mkdir(parents=True, exist_ok=True)
    pack_file.write_text(json.dumps(pack, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "author": author,
        "pack": str(pack_file),
        "traits": len(traits),
        "scene_controls": len(controls),
        "negative_constraints": len(negatives),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Portable author-inspired style engine")
    sub = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--author", required=True)
    common.add_argument("--authors-root")
    common.add_argument("--index-root")
    p_status = sub.add_parser("status", parents=[common])
    sub.add_parser("reclassify", parents=[common])
    p_build = sub.add_parser("build", parents=[common])
    p_build.add_argument("--corpus-root")
    p_build.add_argument("--provider", choices=["none", "ollama", "hash"], default="ollama")
    p_build.add_argument("--model", default=DEFAULT_MODEL)
    p_build.add_argument("--endpoint")
    p_build.add_argument("--batch-size", type=int, default=32)
    p_build.add_argument("--include-lore", action="store_true")
    for name in ("query", "prepare"):
        p = sub.add_parser(name, parents=[common])
        p.add_argument("--query", required=True)
        p.add_argument("--family")
        p.add_argument("--limit", type=int, default=5 if name == "query" else 4)
        p.add_argument("--source-lore", action="store_true")
        p.add_argument("--endpoint")
        if name == "prepare":
            p.add_argument(
                "--include-excerpts",
                action="store_true",
                help="explicitly include short source excerpts; off by default",
            )
    p_audit = sub.add_parser("audit-overlap", parents=[common])
    p_audit.add_argument("--input", required=True)
    p_audit.add_argument("--family")
    p_audit.add_argument("--ratio-threshold", type=float, default=0.72)
    p_audit.add_argument("--exact-run-threshold", type=int, default=24)
    p_import = sub.add_parser(
        "import-pack",
        help="bridge an analysis style-card into a runtime pack.json",
    )
    p_import.add_argument("--author", required=True)
    p_import.add_argument("--card", required=True, help="path to style-card.yaml")
    p_import.add_argument("--authors-root")
    p_import.add_argument("--display-name")
    p_import.add_argument("--corpus-env", help="corpus env var name for the generated pack")
    p_import.add_argument("--force", action="store_true", help="overwrite existing pack.json")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    values = vars(args)
    command = values.pop("command")
    try:
        if command == "status":
            result = status(**values)
        elif command == "reclassify":
            result = reclassify_index(**values)
        elif command == "build":
            result = build_index(**values)
        elif command == "query":
            result = query_index(**values)
        elif command == "prepare":
            result = prepare_context(**values)
        elif command == "import-pack":
            values["card_path"] = values.pop("card")
            result = import_pack(**values)
        else:
            values["input_path"] = values.pop("input")
            result = audit_overlap(**values)
        _json_print(result)
        return 0 if result.get("ok") else 2
    except Exception as exc:
        _json_print({"ok": False, "error": str(exc), "type": type(exc).__name__})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
