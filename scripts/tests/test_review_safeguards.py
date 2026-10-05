"""Protect existing indexes and reject invalid measured domains before import."""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import style_engine as engine


class StyleSafeguards(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.authors = self.root / "authors"
        self.indexes = self.root / "indexes"
        self.corpus = self.root / "corpus"
        self.corpus.mkdir()
        self.card = self.root / "card.yaml"
        self.card.write_text("voice:\n  person: 第三人称\n", encoding="utf-8")
        engine.import_pack("demo", self.card, self.authors)

    def test_empty_build_variants_preserve_old_index_and_clean_temporary(self):
        (self.corpus / "小说.txt").write_text("她把书放回架子。门外传来脚步声。" * 80, encoding="utf-8")
        engine.build_index("demo", self.authors, self.indexes, self.corpus, provider="none")
        index = self.indexes / "demo.sqlite3"
        initial = hashlib.sha256(index.read_bytes()).hexdigest()
        for variant in ("empty", "unsupported", "blank", "excluded"):
            folder = self.root / variant
            folder.mkdir()
            if variant == "unsupported":
                (folder / "story.bin").write_bytes(b"unindexed")
            if variant == "blank":
                (folder / "story.txt").write_text(" \n\t", encoding="utf-8")
            if variant == "excluded":
                (folder / "设定集.txt").write_text("她把书放回架子。" * 100, encoding="utf-8")
                pack_file = self.authors / "demo/pack.json"
                pack = json.loads(pack_file.read_text(encoding="utf-8"))
                pack["exclude_patterns"] = ["设定集"]
                pack_file.write_text(json.dumps(pack, ensure_ascii=False), encoding="utf-8")
            with self.subTest(variant=variant), patch.object(engine, "embed_texts") as embed:
                with self.assertRaisesRegex(ValueError, "没有可检索片段"):
                    engine.build_index("demo", self.authors, self.indexes, folder, provider="hash")
                embed.assert_not_called()
                self.assertEqual(hashlib.sha256(index.read_bytes()).hexdigest(), initial)
                self.assertFalse(index.with_suffix(".sqlite3.building").exists())
        self.assertTrue(engine.query_index("demo", "门外", authors_root=self.authors,
                                           index_root=self.indexes)["hits"])

    def test_first_empty_build_does_not_claim_readiness(self):
        with self.assertRaises(ValueError):
            engine.build_index("demo", self.authors, self.indexes, self.corpus, provider="none")
        self.assertFalse((self.indexes / "demo.sqlite3").exists())

    def test_invalid_domains_do_not_overwrite_existing_pack(self):
        pack = self.authors / "demo/pack.json"
        initial = pack.read_bytes()
        invalid = (
            "scene:\n  description_ratio: 1.5\n",
            "syntax:\n  dialogue_char_ratio: -0.2\n",
            "scene:\n  scene_words: [-800, 1800]\n",
            "scene:\n  scene_words: [0, -1]\n",
            "emotion_writing:\n  emotion_words_per_1k: -1\n",
            "plotlines:\n  count: 1.5\n",
            "plotlines:\n  lines:\n    - id: A\n      weight: -1\n    - id: B\n      weight: 2\n",
            "syntax:\n  sentence_len:\n    short_le10_ratio: 1.1\n",
        )
        for card in invalid:
            with self.subTest(card=card):
                self.card.write_text(card, encoding="utf-8")
                with self.assertRaises(ValueError):
                    engine.import_pack("demo", self.card, self.authors, force=True)
                self.assertEqual(pack.read_bytes(), initial)

    def test_intervals_and_time_scaling_are_not_proportions(self):
        self.card.write_text("timeline:\n  scene_time_ratio: 2.5\nserial_rhythm:\n"
                             "  cliffhanger_frequency: 4\n  scenes_per_chapter: 2.5\n", encoding="utf-8")
        self.assertTrue(engine.import_pack("demo", self.card, self.authors, force=True)["ok"])

    def test_boundary_proportions_and_template_zeros_remain_valid(self):
        self.card.write_text("voice:\n  narrator_comment_ratio: 1\nscene:\n"
                             "  description_ratio: 0\n  scene_words: [0, 0]\n"
                             "syntax:\n  dialogue_char_ratio: null\n", encoding="utf-8")
        result = engine.import_pack("demo", self.card, self.authors, force=True)
        self.assertTrue(result["ok"])
        self.assertIn("syntax.dialogue_char_ratio", result["explicit_null_fields"])
        self.assertFalse(any("scene_words" in warning for warning in result["warnings"]))


if __name__ == "__main__":
    unittest.main()
