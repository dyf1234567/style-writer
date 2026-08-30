from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

import style_engine


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


if __name__ == "__main__":
    unittest.main()
