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
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable, Iterator, Sequence

SCHEMA_VERSION = 1
# Increment whenever normalization or corpus chunk boundaries change.
CHUNKER_VERSION = 1
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
    if not isinstance(pack, dict):
        raise ValueError("pack root must be a mapping")
    if type(pack.get("schema_version")) is not int or pack["schema_version"] != SCHEMA_VERSION:
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
    if not 0 < minimum <= target <= maximum:
        raise ValueError("chunk sizes must satisfy 0 < minimum <= target <= maximum")
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(paragraphs) <= 1:
        paragraphs = [p.strip() for p in text.splitlines() if p.strip()]
    buf = ""
    pending = ""
    for para in paragraphs:
        if len(para) > maximum:
            parts = [p for p in re.split(r"(?<=[。！？!?])", para) if p]
        else:
            parts = [para]
        parts = [piece[i:i + maximum] for piece in parts for i in range(0, len(piece), maximum)]
        for part in parts:
            candidate = f"{buf}\n{part}".strip() if buf else part
            if buf and len(candidate) > maximum:
                if pending:
                    yield pending
                pending = buf
                buf = part
            else:
                buf = candidate
            if len(buf) >= target:
                if pending:
                    yield pending
                pending = buf
                buf = ""
    if pending and buf and len(buf) < minimum and len(pending) + 1 + len(buf) <= maximum:
        yield pending + "\n" + buf
        return
    if pending:
        yield pending
    if buf:
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
    if not len(a) or len(a) != len(b):
        raise ValueError("embedding dimensions differ or are empty; rebuild the index")
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
                if len(vectors) != len(batch):
                    raise ValueError("embedding count differs from input count")
                if vectors:
                    vector_dim = vector_dim or len(vectors[0])
                    if not vector_dim or any(len(vec) != vector_dim for vec in vectors):
                        raise ValueError("embedding dimensions changed during build; rebuild with one model")
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
            "chunker_version": CHUNKER_VERSION,
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


def _scope_count(conn: sqlite3.Connection, family: str | None, source_lore: bool) -> int:
    """Passages actually inside the requested retrieval scope.

    Zero here means the query could not have matched anything, which is a
    different fact from "the query matched nothing".
    """
    where, params = _filters(family, source_lore)
    return int(conn.execute(f"SELECT COUNT(*) FROM passages p WHERE {where}", params).fetchone()[0])


def _index_compatibility(meta: dict | None) -> dict:
    recorded = meta.get("chunker_version") if meta is not None else None
    warnings = []
    if meta is None:
        state = "no-index"
    elif recorded is None:
        state = "unknown"
        warnings.append("索引未记录分块器版本，无法确认是否采用当前分块规则，建议运行 build 重建；"
                        "这不代表已确认存在片段遗漏。")
    elif type(recorded) is int and recorded == CHUNKER_VERSION:
        state = "current"
    else:
        state = "mismatch"
        warnings.append("索引分块器版本与当前程序不一致，请核对生成索引的程序版本，必要时运行 build；"
                        "现有索引仍可检索，不会自动重建。")
    return {
        "index_compatibility": {"status": state, "recorded_chunker_version": recorded,
                                "expected_chunker_version": CHUNKER_VERSION},
        "warnings": warnings,
    }


def query_index(
    author: str,
    query: str,
    authors_root: str | Path | None = None,
    index_root: str | Path | None = None,
    family: str | None = None,
    limit: int = 5,
    source_lore: bool = False,
    endpoint: str | None = None,
    with_full_text: bool = False,
) -> dict:
    _, pack = resolve_pack(author, authors_root)
    index_path = resolve_index(author, index_root)
    if not index_path.exists():
        return {
            **_index_compatibility(None),
            "ok": False,
            "mode": "no-index",
            "index_found": False,
            "passages_in_scope": 0,
            "error": f"index not found: {index_path}",
            "hits": [],
        }
    conn = _connect_readonly(index_path)
    conn.row_factory = sqlite3.Row
    meta = _meta(conn)
    compatibility = _index_compatibility(meta)
    where, params = _filters(family, source_lore)
    scope = _scope_count(conn, family, source_lore)
    if scope == 0:
        conn.close()
        candidates = [str(entry.get("id")) for entry in pack.get("families", []) if entry.get("id")]
        candidates += [str(pack.get("default_family") or ""), str(pack.get("unmatched_family") or "")]
        known = sorted({c for c in candidates if c})
        return {
            **compatibility,
            "ok": False,
            "mode": "empty-scope",
            "index_found": True,
            "passages_in_scope": 0,
            "error": (f"索引里没有 family={family!r}、is_lore={1 if source_lore else 0} 的 passage，"
                      f"检索不可能命中（可用 family: {known or '无'}；改过 pack 后先跑 reclassify 或 build）"),
            "hits": [],
        }
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
            if not qvec or len(qvec) != int(meta.get("vector_dim", 0)):
                raise ValueError("query embedding dimension differs from index; rebuild the index")
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
            **compatibility,
            "ok": True,
            "mode": "no-match",
            "index_found": True,
            "passages_in_scope": scope,
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
        hit = {
            "id": pid,
            "relpath": row["relpath"],
            "family": row["family"],
            "score": round(scores[pid], 8),
            "evidence": sorted(evidence.get(pid, set())),
            "snippet": text[:360] + ("…" if len(text) > 360 else ""),
        }
        if with_full_text:
            # 仅供 audit-overlap 内部使用（全 chunk 比对，消除 snippet 截断盲区）；
            # 默认关闭，prepare/query 的对外输出依旧只有 360 字摘录。
            hit["full_text"] = text
        hits.append(hit)
    conn.close()
    mode = "hybrid" if lexical and vector_used else ("fts5" if lexical else "vector")
    return {
        **compatibility,
        "ok": True,
        "mode": mode,
        "index_found": True,
        "passages_in_scope": scope,
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
    if retrieval.get("mode") == "empty-scope":
        return {
            "index_compatibility": retrieval["index_compatibility"],
            "warnings": retrieval["warnings"],
            "explicit_null_fields": pack.get("explicit_null_fields", []),
            "ok": False,
            "author": author,
            "query": query,
            "mode": "empty-scope",
            "index_found": True,
            "passages_in_scope": 0,
            "error": retrieval.get("error"),
            "writing_context": {"traits": pack.get("traits", [])},
        }
    family_label = chosen_family
    for entry in pack.get("families", []):
        if entry.get("id") == chosen_family:
            family_label = str(entry.get("label", chosen_family))
            break
    retrieval_mode = str(retrieval.get("mode", "no-index"))
    # "static" is the documented pack-only fallback: it is what callers get when
    # there is no index at all, so the reason is reported separately in retrieval_note.
    mode = "static" if retrieval_mode == "no-index" else retrieval_mode
    notes = {
        "no-index": f"未找到索引 {resolve_index(author, index_root)}；仅使用静态画像（先跑 build）",
        "no-match": (f"索引里有 {retrieval.get('passages_in_scope')} 条 passage，但没有一条与该 query 命中；"
                     "retrieval_metrics 为空不代表风格不匹配"),
    }
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
        "mode": mode,
        "index_compatibility": retrieval["index_compatibility"],
        "warnings": retrieval["warnings"],
        "explicit_null_fields": pack.get("explicit_null_fields", []),
        "retrieval_note": notes.get(retrieval_mode) or (
            f"vector 检索失败，已降级为 {mode}：{retrieval['vector_error']}"
            if retrieval.get("vector_error") else None
        ),
        "index_found": bool(retrieval.get("index_found")),
        "passages_in_scope": retrieval.get("passages_in_scope", 0),
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
        **_index_compatibility(None),
        "ok": True,
        "author": author,
        "pack": str(pack_dir),
        "explicit_null_fields": pack.get("explicit_null_fields", []),
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
        result.update(_index_compatibility(meta))
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
    if not probes:
        return {
            "ok": False,
            "verdict": "error",
            "input": str(Path(input_path)),
            "probes": 0,
            "error": "输入没有可比较的片段（空文件或全空白）",
            "warnings": [],
        }
    warnings = []
    probes_with_candidates = 0
    scope = 0
    for probe_no, probe in enumerate(probes, 1):
        result = query_index(
            author, probe, authors_root, index_root, family, 5, False,
            with_full_text=True,
        )
        if result.get("mode") in ("no-index", "empty-scope"):
            return {
                "ok": False,
                "verdict": "error",
                "input": str(Path(input_path)),
                "probes": len(probes),
                "mode": result.get("mode"),
                "passages_in_scope": result.get("passages_in_scope", 0),
                "error": result.get("error"),
                "warnings": [],
            }
        scope = int(result.get("passages_in_scope") or 0)
        hits = result.get("hits", [])
        if hits:
            probes_with_candidates += 1
        for hit in hits:
            # v2 修复：旧实现用 hit["snippet"]（chunk 前 360 字）比对，
            # 抄写落在长 chunk 第 360 字之后即整段漏检。现取完整 chunk 文本；
            # full_text 由 with_full_text 显式开启，不进入任何对外输出。
            source = str(hit.get("full_text") or hit.get("snippet", "")).rstrip("…")
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
    # 任一 probe 未取到候选都意味着覆盖不完整，不能读成「干净」。
    inconclusive = probes_with_candidates < len(probes)
    verdict = "review" if warnings else ("inconclusive" if inconclusive else "clean")
    return {
        "ok": not inconclusive,
        "input": str(Path(input_path)),
        "verdict": verdict,
        "probes": len(probes),
        "probes_with_candidates": probes_with_candidates,
        "passages_in_scope": scope,
        "warnings": warnings,
        "requires_manual_review": bool(warnings) or inconclusive,
        "coverage": f"{probes_with_candidates}/{len(probes)} 个 probe 取到候选文本",
        "note": (
            "只检测连续重合（默认 ≥24 字）：改词换序式抄写不在检测范围内；"
            "clean 只表示每个 probe 都有候选，且这些候选内没有达到阈值的重合；并非全语料穷举"
            + ("；存在未取到候选的 probe，本次覆盖不完整，请检查 build/family"
               if inconclusive else "")
        ),
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
    # v2 card: emotion writing & reward rhythm (string methods only;
    # numeric stats ride the dedicated handlers below)
    "emotion_writing.carrier": "情绪载体",
    "emotion_writing.escalation_pattern": "情绪升级路径",
    "emotion_writing.restraint_level": "抒情克制度",
    "emotion_writing.peak_label": "情绪峰值手法",
    "emotion_writing.body_reaction_ratio": "身体反应占比(推断)",
    "reward_rhythm.buildup_release_ratio": "压抑/释放比",
    "reward_rhythm.installment_style": "回报分期方式",
    "reward_rhythm.promise_drift": "立flag到兑现跨度",
    "reward_rhythm.curve_within_arc": "弧内回报分布",
    "reward_rhythm.chapter_micro_payoff": "章内小回报",
}

CARD_CONTROL_LABELS = {
    "plotlines.switch_trigger": "切线触发",
    "plotlines.switch_transition": "切线过渡",
    "plotlines.line_close_pattern": "支线收束方式",
    "serial_rhythm.chapter_hook_type": "章末钩子",
    "serial_rhythm.hook_strength_by_position": "钩子位置差",
    "serial_rhythm.tension_relax_ratio": "张弛比",
    "serial_rhythm.words_curve_note": "篇幅曲线",
    "reward_rhythm.payoff_unit": "兑现单元",
}


def _strip_comment(raw: str) -> str:
    """只移除引号外的注释，保留转义与内联列表中的内容。"""
    text = raw.strip()
    quote = None
    i = 0
    while i < len(text):
        c = text[i]
        if quote:
            if quote == '"' and c == "\\":
                i += 2
                continue
            if c == quote:
                if quote == "'" and text[i:i + 2] == "''":
                    i += 2
                    continue
                quote = None
        elif c in "\"'" and (i == 0 or text[i - 1] in " [,:"):
            quote = c
        elif c == "#" and (i == 0 or text[i - 1].isspace()):
            return text[:i].rstrip()
        i += 1
    if quote:
        raise ValueError("unclosed YAML quote")
    return text


def _inline_items(inner: str) -> list[str]:
    items, start, i, quote = [], 0, 0, None
    while i < len(inner):
        c = inner[i]
        if quote:
            if quote == '"' and c == "\\":
                i += 2
                continue
            if c == quote:
                if quote == "'" and inner[i:i + 2] == "''":
                    i += 2
                    continue
                quote = None
        elif c in "\"'" and not inner[start:i].strip():
            quote = c
        elif c in "[]{}":
            raise ValueError("nested YAML flow collections are not supported")
        elif c == ",":
            if not inner[start:i].strip():
                raise ValueError("empty YAML inline item")
            items.append(inner[start:i].strip())
            start = i + 1
        i += 1
    if quote:
        raise ValueError("unclosed YAML quote")
    if inner[start:].strip():
        items.append(inner[start:].strip())
    return items


def _scalar(raw: str):
    text = _strip_comment(raw)
    if not text:
        return ""
    if text == "[]":
        return []
    if text[:1] == "[" and text[-1:] == "]":
        inner = text[1:-1].strip()
        return [] if not inner else [_scalar(part) for part in _inline_items(inner)]
    if text.startswith("[") or text.startswith("{"):
        raise ValueError("invalid or unsupported YAML flow collection")
    if text.startswith('"'):
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("invalid YAML double-quoted scalar (use JSON escapes)") from exc
    if text.startswith("'"):
        if not re.fullmatch(r"'(?:[^']|'')*'", text):
            raise ValueError("invalid YAML single-quoted scalar")
        return text[1:-1].replace("''", "'")
    lowered = text.lower()
    if text in ("null", "Null", "NULL", "~"):
        return None
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


def _mapping_parts(text: str) -> tuple[str, str, str]:
    """Find a mapping delimiter outside quoted strings; colon must end or precede whitespace."""
    quote = None
    i = 0
    while i < len(text):
        char = text[i]
        if quote:
            if quote == '"' and char == "\\":
                i += 2
                continue
            if char == quote:
                if quote == "'" and text[i:i + 2] == "''":
                    i += 2
                    continue
                quote = None
        elif char in "\"'" and (i == 0 or text[i - 1].isspace()):
            quote = char
        elif char == ":" and (i + 1 == len(text) or text[i + 1].isspace()):
            return text[:i], ":", text[i + 1:]
        i += 1
    return text, "", ""


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
            key, sep, rest = _mapping_parts(_strip_comment(content))
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
            key, sep, rest = _mapping_parts(_strip_comment(body))
            if body[:1] in ("\"", "'") or not sep:
                items.append(_scalar(body))
                continue
            item = {key.strip(): _scalar(rest)}
            while index < len(rows) and rows[index][1] > indent and not rows[index][2].startswith("- "):
                c_no, _c, c_content = rows[index]
                c_key, c_sep, c_rest = _mapping_parts(_strip_comment(c_content))
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


def _iter_paths(obj: object, prefix: str = "") -> Iterator[tuple[str, str]]:
    if isinstance(obj, str):
        yield prefix, obj
    elif isinstance(obj, dict):
        for key, value in obj.items():
            yield from _iter_paths(value, f"{prefix}.{key}" if prefix else str(key))
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            yield from _iter_paths(value, f"{prefix}[{index}]")


# 原文摘录的排版痕迹：弯引号、单弯引号、直角引号、双直角引号。
# 只拦 “” 会让「」排版的语料原文堂而皇之地通过桥（实测）。
SOURCE_QUOTE_MARKS = "“”‘’「」『』"

_CN_ONLY = re.compile(r"[^\u4e00-\u9fff]+")


def _cn_only(text: str) -> str:
    """只留汉字，把标点与换行剔除后比对：改标点式抄写同样要露出来。"""
    return _CN_ONLY.sub("", text)


def find_source_overlap(values: Iterable[tuple[str, str]], corpus_text: str,
                        run: int = 10) -> list[dict]:
    """返回与语料存在 >=run 连续汉字重合的字段。

    只报告字段路径与重合长度，不打印命中文本，避免工具本身输出原文。
    """
    hay = _cn_only(corpus_text)
    hits = []
    for path, value in values:
        needle = _cn_only(value)
        if len(needle) < run:
            continue
        for start in range(len(needle) - run + 1):
            if needle[start:start + run] in hay:
                hits.append({"field": path, "run": run})
                break
    return hits


def _read_corpus(explicit: str | Path | None, env_name: str) -> tuple[str | None, str | None]:
    """桥的原文比对数据源：显式路径优先，否则读 pack 声明的语料环境变量。"""
    if explicit:
        root = Path(explicit).expanduser()
    elif env_name and os.getenv(env_name):
        root = Path(os.environ[env_name]).expanduser()
    else:
        return None, f"未给 --corpus-root，且语料环境变量 {env_name or '(未声明)'} 未设置"
    if not root.exists():
        return None, f"语料路径不存在: {root}"
    files = [root] if root.is_file() else sorted(
        p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in TEXT_SUFFIXES
    )
    if not files:
        return None, f"语料路径下没有可读文本文件（{', '.join(TEXT_SUFFIXES)}）: {root}"
    return "".join(read_text(path) for path in files), None


def _explicit_null_paths(value, path: str = "") -> list[str]:
    """Record null locations only, never infer why the author left them null."""
    if value is None:
        return [path] if path else []
    if isinstance(value, dict):
        return [found for key, child in value.items()
                for found in _explicit_null_paths(child, f"{path}.{key}" if path else str(key))]
    if isinstance(value, list):
        return [found for i, child in enumerate(value)
                for found in _explicit_null_paths(child, f"{path}[{i}]" )]
    return []


def _number(value, path: str) -> None:
    if value is None or value == "":
        return
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"{path} 应为有限数值（不能是字符串或布尔值）")


def _validate_card_types(card: dict) -> None:
    for path in ("imagery.taboo", "imagery.semantic_domains", "syntax.lexical_fingerprint"):
        values = _dig(card, path)
        if values is None:
            continue
        if not isinstance(values, list):
            raise ValueError(f"{path} 应为字符串列表")
        for i, value in enumerate(values):
            if not isinstance(value, str):
                raise ValueError(f'{path}[{i}] 应为字符串；包含“冒号＋空格”的文本请加引号。')
    paths = [
        "voice.narrator_comment_ratio", "timeline.analepsis_ratio", "timeline.prolepsis_ratio",
        "timeline.scene_time_ratio", "scene.description_ratio", "emotion_writing.emotion_words_per_1k",
        "imagery.simile_markers_per_1k", "syntax.dialogue_char_ratio", "syntax.dialogue_blocks_per_1k",
        "plotlines.count", "plotlines.convergence_every", "serial_rhythm.scenes_per_chapter",
        "serial_rhythm.arc_length", "serial_rhythm.arc_recovery", "serial_rhythm.cliffhanger_frequency",
        "reward_rhythm.payoff_interval",
    ]
    for parent, fields in (
        ("serial_rhythm.words_per_chapter", ("median", "p10", "p90")),
        ("syntax.sentence_len", ("median", "p90", "short_le10_ratio", "long_ge40_ratio")),
        ("syntax.paragraph_len", ("median", "p90", "short_para_ratio")),
        ("syntax.punct_per_1k", ("exclam", "question", "ellipsis", "dash")),
    ):
        container = _dig(card, parent)
        if container is not None and not isinstance(container, dict):
            raise ValueError(f"{parent} 应为映射")
        paths.extend(f"{parent}.{field}" for field in fields)
    for path in paths:
        _number(_dig(card, path), path)
    words = _dig(card, "scene.scene_words")
    if words is not None:
        if not isinstance(words, list) or len(words) not in (0, 2):
            raise ValueError("scene.scene_words 应为两个数值组成的列表")
        for i, value in enumerate(words):
            _number(value, f"scene.scene_words[{i}]")
    lines = _dig(card, "plotlines.lines")
    if lines is not None:
        if not isinstance(lines, list):
            raise ValueError("plotlines.lines 应为映射列表")
        for i, entry in enumerate(lines):
            if not isinstance(entry, dict):
                raise ValueError(f"plotlines.lines[{i}] 应为映射")
            _number(entry.get("weight"), f"plotlines.lines[{i}].weight")


def _atomic_pack_write(path: Path, pack: dict) -> None:
    content = json.dumps(pack, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".pack-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _validate_retained_config(pack: dict) -> None:
    for key in ("display_name", "default_family", "unmatched_family"):
        if key in pack and not isinstance(pack[key], str):
            raise ValueError(f"pack.{key} must be a string")
    if "corpus" in pack:
        corpus = pack["corpus"]
        if not isinstance(corpus, dict) or not isinstance(corpus.get("env"), str):
            raise ValueError("pack.corpus must contain a string env")
    if "exclude_patterns" in pack:
        patterns = pack["exclude_patterns"]
        if not isinstance(patterns, list) or any(not isinstance(p, str) for p in patterns):
            raise ValueError("pack.exclude_patterns must be a string list")
    if "families" in pack:
        families = pack["families"]
        if not isinstance(families, list):
            raise ValueError("pack.families must be a list")
        for family in families:
            if (not isinstance(family, dict) or not isinstance(family.get("id"), str)
                    or not isinstance(family.get("patterns"), list)
                    or any(not isinstance(p, str) for p in family["patterns"])):
                raise ValueError("pack.families items require string id and string-list patterns")


def import_pack(
    author: str,
    card_path: str | Path,
    authors_root: str | Path | None = None,
    display_name: str | None = None,
    corpus_env: str | None = None,
    force: bool = False,
    corpus_root: str | Path | None = None,
    overlap_run: int = 10,
    discard_existing_config: bool = False,
) -> dict:
    """style-card.yaml → pack.json 桥接（识别侧产物进入执行侧的唯一通道）。

    0/""/[] 视为模板未填，跳过。红线在桥里程序化把关：
    meta.target_work 禁止泄漏进 pack；字段值含任何排版引号（弯引号/直角引号）
    视为原文摘录，拒绝；能给到语料时（--corpus-root 或 pack 的语料环境变量）
    再做一次「与原文连续重合 ≥overlap_run 字」的比对，命中即拒绝。
    语料不可得时在结果 warnings 里明说未做该项检查，不让它冒充已通过。
    """
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", author):
        raise ValueError(f"invalid slug: {author}（用小写字母/数字/-/_）")
    if overlap_run <= 0:
        raise ValueError("overlap_run must be positive")
    if discard_existing_config and not force:
        raise ValueError("--discard-existing-config 必须与 --force 同时使用")
    root = Path(authors_root).expanduser() if authors_root else _default_author_root()
    pack_dir = root / author
    pack_file = pack_dir / "pack.json"
    existing = {}
    warnings: list[str] = []
    if pack_file.exists():
        if not force:
            raise FileExistsError(f"pack exists: {pack_file}（--force 覆盖）")
        if discard_existing_config:
            warnings.append("已放弃旧配置：display_name、corpus（语料环境变量）、families、"
                            "default_family、unmatched_family、exclude_patterns 将恢复导入默认值；"
                            "显式 --display-name / --corpus-env 仍优先。")
        else:
            try:
                _, existing = resolve_pack(author, root)
                _validate_retained_config(existing)
            except (ValueError, UnicodeError) as exc:
                raise ValueError("旧作者包无法读取或格式不兼容，未覆盖。请修复 pack.json；"
                                 "确认放弃旧配置时使用 --force --discard-existing-config。"
                                 f"原因：{exc}") from exc
    card_file = Path(card_path).expanduser()
    if not card_file.exists():
        raise FileNotFoundError(f"style card not found: {card_file}")
    card = _parse_restricted(read_text(card_file))
    if not isinstance(card, dict) or not card:
        raise ValueError("style card is empty or not a mapping")
    _validate_card_types(card)

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
        ("emotion_writing.emotion_words_per_1k", "情绪词密度(每千字)"),
        ("imagery.simile_markers_per_1k", "明喻标记密度(每千字)"),
    ):
        value = _dig(card, dotted)
        if _filled(value):
            traits.append(f"{label}：{value}")

    fingerprint = _dig(card, "syntax.lexical_fingerprint")
    if isinstance(fingerprint, list) and fingerprint:
        traits.append("词汇指纹：" + "、".join(str(f) for f in fingerprint if _filled(f)))

    domains = _dig(card, "imagery.semantic_domains")
    if isinstance(domains, list) and domains:
        traits.append("高频语义域：" + "、".join(str(d) for d in domains))

    wc = _dig(card, "serial_rhythm.words_per_chapter")
    if isinstance(wc, dict) and _filled(wc.get("median")):
        p10 = wc.get('p10') if _filled(wc.get('p10')) else '-'
        p90 = wc.get('p90') if _filled(wc.get('p90')) else '-'
        traits.append(f"章字数：中位 {wc['median']}（p10 {p10} / p90 {p90}）")
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
        ("reward_rhythm.payoff_interval", "小回报间隔：每 {v} 章"),
    ):
        value = _dig(card, dotted)
        if _filled(value):
            controls.append(text.format(v=value))
    scene_words = _dig(card, "scene.scene_words")
    if isinstance(scene_words, list) and len(scene_words) == 2 and all(w is not None and w != "" for w in scene_words) and any(_filled(w) for w in scene_words):
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
        "explicit_null_fields": sorted(_explicit_null_paths(card)),
    }

    for key in ("display_name", "corpus", "families", "default_family", "unmatched_family", "exclude_patterns"):
        if key in existing:
            pack[key] = existing[key]
    if display_name is not None:
        pack["display_name"] = display_name
    if corpus_env is not None:
        pack["corpus"] = {**pack["corpus"], "env": corpus_env}

    target_work = str(_dig(card, "meta.target_work") or "").strip()
    if target_work and target_work in json.dumps(pack, ensure_ascii=False):
        raise ValueError(f"红线：meta.target_work「{target_work}」泄漏进了卡片字段，先回卡内清除")

    for path, value in _iter_paths(pack):
        marks = "".join(sorted({c for c in SOURCE_QUOTE_MARKS if c in value}))
        if marks:
            raise ValueError(
                f"红线：字段 {path} 含弯引号/直角引号 {marks}，疑似原文摘录，回卡内改写成抽象描述"
            )

    redline: dict = {
        "target_work": "checked",
        "quote_marks": "checked",
        "source_overlap": {"status": "skipped"},
    }
    corpus_text, corpus_note = _read_corpus(corpus_root, str(pack["corpus"].get("env", "")))
    if corpus_text is not None and len(_cn_only(corpus_text)) < overlap_run:
        corpus_note = f"可比对汉字不足 {overlap_run} 字（实际 {len(_cn_only(corpus_text))} 字）"
        corpus_text = None
    if corpus_text is None:
        redline["source_overlap"] = {"status": "skipped", "reason": corpus_note}
        warnings.append(
            f"红线项「与原文连续重合」未执行（{corpus_note}）；"
            "本次导入不代表卡片里没有原句。可加 --corpus-root <目录或文件> 重跑。"
        )
    else:
        hits = find_source_overlap(_iter_paths(pack), corpus_text, overlap_run)
        redline["source_overlap"] = {
            "status": "checked", "corpus_cn_chars": len(_cn_only(corpus_text)), "run": overlap_run,
        }
        if hits:
            fields = "、".join(hit["field"] for hit in hits[:8])
            raise ValueError(
                f"红线：{len(hits)} 个字段与语料存在 ≥{overlap_run} 字连续重合（{fields}）；"
                "命中原文不打印，请回卡内改写这些字段"
            )

    pack_dir.mkdir(parents=True, exist_ok=True)
    _atomic_pack_write(pack_file, pack)
    return {
        "ok": True,
        "author": author,
        "pack": str(pack_file),
        "traits": len(traits),
        "scene_controls": len(controls),
        "negative_constraints": len(negatives),
        "explicit_null_fields": pack["explicit_null_fields"],
        "redline": redline,
        "warnings": warnings,
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
    p_import.add_argument("--corpus-root",
                          help="比对原文红线的语料（目录或文件）；缺省时读 pack 的语料环境变量")
    p_import.add_argument("--overlap-run", type=int, default=10,
                          help="与语料连续汉字重合多少字判为原句泄漏（默认 10）")
    p_import.add_argument("--force", action="store_true", help="overwrite existing pack.json")
    p_import.add_argument("--discard-existing-config", action="store_true",
                          help="requires --force; explicitly reset existing runtime configuration")
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
