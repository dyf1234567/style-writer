from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

import style_engine

# 与夹具语料没有任何共同二元组的文本：检索必然空手而归，用来验证「空跑不算通过」
NO_OVERLAP_TEXT = "鑫犉" * 90


class StyleEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.authors = self.root / "authors"
        self.indexes = self.root / "indexes"
        self.corpus = self.root / "corpus"
        pack_dir = self.authors / "demo"
        pack_dir.mkdir(parents=True)
        self.corpus.mkdir()
        pack = {
            "schema_version": 1,
            "slug": "demo",
            "display_name": "demo-inspired",
            "positioning": "high-level only",
            "corpus": {"env": "DEMO_STYLE_CORPUS"},
            "default_family": "modern",
            "unmatched_family": "other",
            "families": [
                {"id": "modern", "label": "现代", "patterns": ["现代"]},
                {"id": "epic", "label": "史诗", "patterns": ["史诗"]},
            ],
            "exclude_patterns": ["设定集"],
            "traits": ["动作承载情绪"],
            "scene_controls": ["克制收束"],
            "negative_constraints": ["不借用原作人物"],
        }
        (pack_dir / "pack.json").write_text(json.dumps(pack, ensure_ascii=False), encoding="utf-8")
        modern = "雨夜里的少年把旧车票折好。他说没关系，手指却一直按着口袋。\n\n" * 8
        (self.corpus / "现代故事.txt").write_text(modern, encoding="utf-8")
        (self.corpus / "现代故事副本.txt").write_text(modern, encoding="utf-8")
        (self.corpus / "史诗故事.txt").write_text(
            "城墙外的旌旗在风里倒向北方。将军没有回头，只让号角再响一次。\n\n" * 8,
            encoding="utf-8",
        )
        (self.corpus / "设定集.txt").write_text("禁用世界观专名和人物词条。\n\n" * 20, encoding="utf-8")
        os.environ["DEMO_STYLE_CORPUS"] = str(self.corpus)

    def tearDown(self) -> None:
        os.environ.pop("DEMO_STYLE_CORPUS", None)
        self.temp.cleanup()

    def build(self, provider: str = "hash") -> dict:
        return style_engine.build_index(
            "demo", self.authors, self.indexes, provider=provider, batch_size=2
        )

    def test_build_deduplicates_and_excludes_lore(self) -> None:
        result = self.build("none")
        self.assertEqual(result["skipped_duplicate_files"], 1)
        self.assertEqual(result["skipped_lore_files"], 1)
        self.assertGreater(result["passages"], 0)
        self.assertEqual(result["provider"], "none")

    def test_chunker_version_current_and_missing_index(self) -> None:
        state = style_engine.status("demo", self.authors, self.indexes)
        self.assertEqual(state["index_compatibility"]["status"], "no-index")
        self.assertEqual(state["warnings"], [])
        # Legacy packs without provenance fields remain usable.
        context = style_engine.prepare_context("demo", "雨夜", self.authors, self.indexes)
        self.assertEqual(context["explicit_null_fields"], [])
        self.build("none")
        for result in (style_engine.status("demo", self.authors, self.indexes),
                       style_engine.prepare_context("demo", "雨夜", self.authors, self.indexes)):
            self.assertEqual(result["index_compatibility"]["status"], "current")
            self.assertEqual(result["index_compatibility"]["recorded_chunker_version"], style_engine.CHUNKER_VERSION)
            self.assertEqual(result["warnings"], [])

    def test_legacy_index_warns_without_mutating_or_blocking(self) -> None:
        self.build("none")
        index = style_engine.resolve_index("demo", self.indexes)
        with sqlite3.connect(index) as conn:
            conn.execute("DELETE FROM meta WHERE key='chunker_version'")
        conn.close()
        before = index.read_bytes()
        for result in (style_engine.status("demo", self.authors, self.indexes),
                       style_engine.query_index("demo", "雨夜", self.authors, self.indexes),
                       style_engine.prepare_context("demo", "雨夜", self.authors, self.indexes),
                       style_engine.prepare_context("demo", NO_OVERLAP_TEXT, self.authors, self.indexes),
                       style_engine.prepare_context("demo", "雨夜", self.authors, self.indexes, family="absent")):
            self.assertEqual(result["index_compatibility"]["status"], "unknown")
            self.assertIn("无法确认", result["warnings"][0])
        self.assertEqual(index.read_bytes(), before)
        context = style_engine.prepare_context("demo", "雨夜", self.authors, self.indexes)
        self.assertEqual(context["mode"], "fts5")
        self.assertTrue(context["evidence"])
        style_engine.reclassify_index("demo", self.authors, self.indexes)
        self.assertEqual(style_engine.status("demo", self.authors, self.indexes)["index_compatibility"]["status"], "unknown")

    def test_mismatched_chunker_version_warns_and_rebuild_clears_it(self) -> None:
        self.build("none")
        index = style_engine.resolve_index("demo", self.indexes)
        for version in (0, 999, "1", True):
            with self.subTest(version=version):
                with sqlite3.connect(index) as conn:
                    conn.execute("UPDATE meta SET value=? WHERE key='chunker_version'", (json.dumps(version),))
                conn.close()
                result = style_engine.prepare_context("demo", "雨夜", self.authors, self.indexes)
                self.assertEqual(result["index_compatibility"]["status"], "mismatch")
                self.assertTrue(result["warnings"])
                self.assertTrue(result["ok"])
        self.build("none")
        state = style_engine.status("demo", self.authors, self.indexes)
        self.assertEqual(state["index_compatibility"]["status"], "current")
        self.assertEqual(state["warnings"], [])

    def test_chunks_preserve_short_documents_and_tails(self) -> None:
        for source in ("短文", "甲" * 900 + "\n\n" + "乙" * 50, "甲" * 2900):
            with self.subTest(length=len(source)):
                chunks = list(style_engine.iter_chunks(source))
                self.assertEqual("".join(chunks).replace("\n", ""), source.replace("\n", ""))
                self.assertTrue(all(len(chunk) <= 1400 for chunk in chunks))

    def test_partial_audit_is_not_clean(self) -> None:
        self.build("none")
        draft = self.root / "partial.md"
        draft.write_text("雨夜" + "甲" * 258 + "\n\n" + "龘" * 260, encoding="utf-8")
        result = style_engine.audit_overlap("demo", draft, self.authors, self.indexes)
        self.assertEqual(result["probes"], 2)
        self.assertEqual(result["probes_with_candidates"], 1)
        self.assertEqual(result["verdict"], "inconclusive")
        self.assertFalse(result["ok"])
        self.assertTrue(result["requires_manual_review"])

    def test_query_dimension_mismatch_reports_fallback(self) -> None:
        self.build("hash")
        with patch.object(style_engine, "embed_texts", return_value=[[1.0, 0.0]]):
            result = style_engine.query_index("demo", "雨夜", self.authors, self.indexes)
        self.assertIn("dimension", result["vector_error"])
        self.assertEqual(result["mode"], "fts5")
        with self.assertRaises(ValueError):
            style_engine._dot([1.0], [1.0, 0.0])

    def test_build_rejects_bad_embedding_batches(self) -> None:
        for vectors in ([], [[1.0], [1.0, 0.0]], [[], []]):
            with self.subTest(vectors=vectors):
                with patch.object(style_engine, "embed_texts", return_value=vectors):
                    with self.assertRaises(ValueError):
                        self.build("hash")

    def test_parent_directory_wins_over_title_keyword(self) -> None:
        _, pack = style_engine.resolve_pack("demo", self.authors)
        pack["families"].append({"id": "interview", "label": "访谈", "patterns": ["访谈"]})
        self.assertEqual(
            style_engine.classify_family("访谈/现代小说作者访谈.txt", pack),
            "interview",
        )
        self.assertEqual(style_engine.classify_family("其它/陌生作品.txt", pack), "other")

    def test_prepare_uses_hybrid_and_family(self) -> None:
        self.build("hash")
        result = style_engine.prepare_context(
            "demo",
            "雨夜 少年 车票 口袋",
            self.authors,
            self.indexes,
            family="modern",
        )
        self.assertTrue(result["ok"])
        self.assertIn(result["mode"], {"hybrid", "fts5", "vector"})
        self.assertTrue(result["evidence"])
        self.assertTrue(all(hit["family"] == "modern" for hit in result["evidence"]))
        self.assertTrue(all("snippet" not in hit for hit in result["evidence"]))
        self.assertGreater(
            result["writing_context"]["retrieval_metrics"]["sample_count"], 0
        )
        self.assertFalse(result["source_lore"])
        self.assertFalse(result["excerpts_enabled"])
        self.assertIn("不借用原作人物", result["writing_context"]["negative_constraints"])

    def test_source_excerpts_require_explicit_opt_in(self) -> None:
        self.build("hash")
        result = style_engine.prepare_context(
            "demo",
            "雨夜 少年 车票 口袋",
            self.authors,
            self.indexes,
            family="modern",
            include_excerpts=True,
        )
        self.assertTrue(result["excerpts_enabled"])
        self.assertTrue(any("snippet" in hit for hit in result["evidence"]))

    def test_readonly_query_creates_no_sqlite_sidecars(self) -> None:
        self.build("hash")
        index = self.indexes / "demo.sqlite3"
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(index) + suffix)
            if sidecar.exists():
                sidecar.unlink()
        result = style_engine.query_index(
            "demo", "雨夜 少年", self.authors, self.indexes, family="modern"
        )
        self.assertTrue(result["ok"])
        self.assertFalse(Path(str(index) + "-wal").exists())
        self.assertFalse(Path(str(index) + "-shm").exists())

    def test_static_profile_survives_without_index(self) -> None:
        result = style_engine.prepare_context(
            "demo", "任意场景", self.authors, self.indexes, family="epic"
        )
        self.assertEqual(result["mode"], "static")
        self.assertEqual(result["writing_context"]["family"]["id"], "epic")
        self.assertEqual(result["evidence"], [])

    def test_overlap_flags_copied_material(self) -> None:
        self.build("hash")
        draft = self.root / "draft.txt"
        draft.write_text(
            "雨夜里的少年把旧车票折好。他说没关系，手指却一直按着口袋。" * 4,
            encoding="utf-8",
        )
        result = style_engine.audit_overlap(
            "demo", draft, self.authors, self.indexes, family="modern", exact_run_threshold=16
        )
        self.assertTrue(result["requires_manual_review"])
        self.assertTrue(result["warnings"])

    def test_overlap_detects_copy_beyond_snippet_prefix(self) -> None:
        """v2 修复回归：抄袭落在长 chunk 第 360 字之后也必须命中。
        旧实现拿 chunk 前 360 字做比对，本用例的抄写句在第 400 字后，会漏检。"""
        corpus_dir = self.root / "corpus2"
        corpus_dir.mkdir()
        filler = "".join(
            f"墙上的挂钟指针挪到第{i}格，落满灰的桌面没有一件东西在动。"
            for i in range(1, 16)
        )  # ~390 字，把抄写句推到 360 字之后
        copied = (
            "老兵把军牌埋进雪里，转身朝山口走去，风把他的帽檐掀起来，"
            "他没有回头，雪地上只剩下一串越来越浅的脚印和半截烧焦的旗杆。"
        )
        (corpus_dir / "现代长篇.txt").write_text(filler + copied, encoding="utf-8")
        pack_dir = self.authors / "demo2"
        pack_dir.mkdir(parents=True)
        pack = {
            "schema_version": 1, "slug": "demo2", "display_name": "demo2-inspired",
            "positioning": "high-level only", "corpus": {"env": "DEMO2_STYLE_CORPUS"},
            "default_family": "modern", "unmatched_family": "other",
            "families": [{"id": "modern", "label": "现代", "patterns": ["现代"]}],
            "exclude_patterns": [], "traits": ["t"], "scene_controls": [],
            "negative_constraints": [],
        }
        (pack_dir / "pack.json").write_text(json.dumps(pack, ensure_ascii=False), encoding="utf-8")
        os.environ["DEMO2_STYLE_CORPUS"] = str(corpus_dir)
        try:
            built = style_engine.build_index(
                "demo2", self.authors, self.indexes, provider="hash", batch_size=2
            )
            self.assertGreaterEqual(built["passages"], 1)
            # 抄写句位于单 chunk 第 ~390 字处（> 360 字 snippet 前缀），
            # 旧实现必然漏检；修复后应命中。
            draft = self.root / "draft2.txt"
            draft.write_text("那天下着小雨。" * 5 + copied, encoding="utf-8")
            result = style_engine.audit_overlap(
                "demo2", draft, self.authors, self.indexes, family="modern",
                exact_run_threshold=24,
            )
            self.assertTrue(
                result["requires_manual_review"],
                f"360 字盲区未修复: {result['warnings']}",
            )
        finally:
            os.environ.pop("DEMO2_STYLE_CORPUS", None)

    # ------------------------------------------------ 空跑不得判「干净」
    def test_audit_without_index_is_not_a_pass(self) -> None:
        draft = self.root / "copy.txt"
        draft.write_text("雨夜里的少年把旧车票折好。他说没关系，手指却一直按着口袋。" * 4, encoding="utf-8")
        result = style_engine.audit_overlap("demo", draft, self.authors, self.indexes, family="modern")
        self.assertFalse(result["ok"], "缺索引时必须报错，不能返回干净")
        self.assertEqual(result["mode"], "no-index")
        self.assertEqual(result["verdict"], "error")
        code = style_engine.main([
            "audit-overlap", "--author", "demo", "--input", str(draft),
            "--authors-root", str(self.authors), "--index-root", str(self.indexes),
            "--family", "modern",
        ])
        self.assertNotEqual(code, 0, "发布闸门靠退出码把关")

    def test_audit_unknown_family_is_not_a_pass(self) -> None:
        self.build("none")
        draft = self.root / "copy3.txt"
        draft.write_text("雨夜里的少年把旧车票折好。他说没关系，手指却一直按着口袋。" * 4, encoding="utf-8")
        result = style_engine.audit_overlap("demo", draft, self.authors, self.indexes, family="mordern")
        self.assertFalse(result["ok"])
        self.assertEqual(result["mode"], "empty-scope")
        self.assertIn("modern", str(result["error"]))

    def test_audit_with_no_candidate_is_inconclusive(self) -> None:
        """逐字抄写之外的另一种空跑：检索一个候选都没取到，不能读成 clean。"""
        self.build("none")
        draft = self.root / "odd.txt"
        draft.write_text(NO_OVERLAP_TEXT, encoding="utf-8")
        result = style_engine.audit_overlap("demo", draft, self.authors, self.indexes, family="modern")
        self.assertFalse(result["ok"])
        self.assertEqual(result["verdict"], "inconclusive")
        self.assertEqual(result["probes_with_candidates"], 0)
        self.assertGreater(result["passages_in_scope"], 0)

    def test_audit_flags_review_when_candidates_were_copied(self) -> None:
        self.build("none")
        draft = self.root / "odd2.txt"
        draft.write_text(
            NO_OVERLAP_TEXT + "\n\n" + "雨夜里的少年把旧车票折好。他说没关系，手指却一直按着口袋。" * 10,
            encoding="utf-8",
        )
        result = style_engine.audit_overlap("demo", draft, self.authors, self.indexes, family="modern")
        self.assertFalse(result["ok"])  # 有重合仍需审查，且首个 probe 未覆盖。
        self.assertEqual(result["verdict"], "review")
        self.assertGreater(result["probes_with_candidates"], 0)

    def test_audit_can_say_clean_once_candidates_were_compared(self) -> None:
        """取到候选但连续重合不够长 -> clean；与「一个候选都没取到」区分开。"""
        self.build("none")
        draft = self.root / "odd3.txt"
        draft.write_text("雨夜里的少年" + "鑫" * 250, encoding="utf-8")
        result = style_engine.audit_overlap("demo", draft, self.authors, self.indexes, family="modern")
        self.assertTrue(result["ok"])
        self.assertEqual(result["verdict"], "clean")
        self.assertEqual(result["warnings"], [])
        self.assertEqual(result["probes_with_candidates"], result["probes"])

    def test_prepare_keeps_static_label_but_says_why(self) -> None:
        result = style_engine.prepare_context("demo", "雨夜 少年", self.authors, self.indexes, family="modern")
        self.assertEqual(result["mode"], "static")
        self.assertFalse(result["index_found"])
        self.assertIn("未找到索引", result["retrieval_note"])

    def test_prepare_distinguishes_no_match_from_static(self) -> None:
        self.build("none")
        result = style_engine.prepare_context("demo", NO_OVERLAP_TEXT, self.authors, self.indexes, family="modern")
        self.assertEqual(result["mode"], "no-match")
        self.assertTrue(result["index_found"])
        self.assertGreater(result["passages_in_scope"], 0)
        self.assertEqual(result["writing_context"]["retrieval_metrics"], {})
        self.assertIn("没有一条", result["retrieval_note"])

    def test_prepare_errors_on_unknown_family(self) -> None:
        self.build("none")
        result = style_engine.prepare_context("demo", "雨夜 少年", self.authors, self.indexes, family="mordern")
        self.assertFalse(result["ok"])
        self.assertEqual(result["mode"], "empty-scope")
        code = style_engine.main([
            "prepare", "--author", "demo", "--query", "雨夜 少年",
            "--authors-root", str(self.authors), "--index-root", str(self.indexes),
            "--family", "mordern",
        ])
        self.assertNotEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
