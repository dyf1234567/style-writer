from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

import style_engine

REPO_ROOT = SCRIPT_DIR.parent
TEMPLATE_PATH = REPO_ROOT / "analysis" / "style-card.template.yaml"

SAMPLE_CARD = """meta:
  target_work: "某作品"
  sample_range: "第 1-120 章"
  sample_words: 0

voice:
  person: "第三人称"
  focalization: "可变内聚焦"
  reliability: "可靠"
  distance: ""
  narrator_comment_ratio: 0.0
  tense: "过去"

timeline:
  base_order: "顺叙为主"
  analepsis_ratio: 0.08
  prolepsis_ratio: 0.0
  scene_time_ratio: 1.4
  ellipsis_span: "跨过三日只写一句"

plotlines:
  count: 2
  lines:
    - id: A
      role: "主线"
      function: "推进目标"
      weight: 0.6
      pov: "人物甲"
    - id: B
      role: "副线"
      function: "补世界观"
      weight: 0.4
      pov: "人物乙"
  switch_trigger: "按悬念最强处切"
  switch_transition: "尾词钩连"
  convergence_every: 12
  line_close_pattern: "长弧并入主线"

serial_rhythm:
  words_per_chapter:
    median: 3200
    p10: 2600
    p90: 4100
  chapter_hook_type: "悬念式"
  tension_relax_ratio: "3:2"
  arc_length: 8
  arc_recovery: 2
  cliffhanger_frequency: 0.3
  scenes_per_chapter: 2.0

scene:
  scene_words: [800, 1800]
  show_vs_tell: "七三"
  description_ratio: 0.22
  sensory_mix: "触觉优先"
  scenery_function: "情绪外化"

syntax:
  sentence_len:
    median: 18
    p90: 41
    short_le10_ratio: 0.21
    long_ge40_ratio: 0.07
  paragraph_len:
    median: 64
    p90: 180
    short_para_ratio: 0.18
  dialogue_char_ratio: 0.24
  dialogue_blocks_per_1k: 3.1
  punct_per_1k:
    exclam: 2.1
    question: 6.0
    ellipsis: 3.4
    dash: 1.2
  register: "口语化"
  sentence_rhythm_note: "长句铺陈短句收束"

dialogue_style:
  subtext_density: "高"
  individuality: "句式"
  exposition_in_dialogue: "少"

imagery:
  semantic_domains: ["天气", "身体感"]
  metaphor_type: "明喻为主"
  recurrence_interval: "每卷翻转一次"
  taboo: []
"""


class ImportPackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.authors = self.root / "authors"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _card(self, text: str) -> Path:
        path = self.root / "style-card.yaml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_template_parses_and_rejects_empty(self) -> None:
        """真实空白模板必须能被受限解析器读懂，且因无字段被拒。"""
        self.assertTrue(TEMPLATE_PATH.exists(), f"missing {TEMPLATE_PATH}")
        parsed = style_engine._parse_restricted(TEMPLATE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(parsed["plotlines"]["lines"][0]["id"], "A")
        self.assertEqual(parsed["imagery"]["semantic_domains"], [])
        with self.assertRaises(ValueError):
            style_engine.import_pack("t-from-template", TEMPLATE_PATH, self.authors)

    def test_sample_card_roundtrips_through_resolve_pack(self) -> None:
        result = style_engine.import_pack(
            "demo-style", self._card(SAMPLE_CARD), self.authors, display_name="作者甲式"
        )
        self.assertTrue(result["ok"])
        _, pack = style_engine.resolve_pack("demo-style", self.authors)
        traits_text = "\n".join(pack["traits"])
        controls_text = "\n".join(pack["scene_controls"])
        self.assertIn("叙述人称：第三人称", traits_text)
        self.assertIn("句长：中位 18", traits_text)
        self.assertIn("高频语义域：天气、身体感", traits_text)
        self.assertIn("情节线 A：主线·推进目标·视角 人物甲·权重 0.6", controls_text)
        self.assertIn("高潮弧跨度：8 章", controls_text)
        self.assertIn("单场景字数区间：800-1800", controls_text)
        self.assertEqual(pack["negative_constraints"][0], "禁止复刻原作语句、人名、地名与具体情节")
        self.assertIn("第 1-120 章", pack["positioning"])

    def test_target_work_leak_is_blocked(self) -> None:
        leaked = SAMPLE_CARD.replace(
            'target_work: "某作品"', 'target_work: "某某某"'
        ).replace('sensory_mix: "触觉优先"', 'sensory_mix: "触觉优先，某某某"')
        with self.assertRaises(ValueError) as ctx:
            style_engine.import_pack("leaky", self._card(leaked), self.authors)
        self.assertIn("target_work", str(ctx.exception))

    def test_curved_quotes_redline(self) -> None:
        quoted = SAMPLE_CARD.replace(
            'register: "口语化"', 'register: "口语化，常说“来了老弟”"'
        )
        with self.assertRaises(ValueError) as ctx:
            style_engine.import_pack("quoted", self._card(quoted), self.authors)
        self.assertIn("弯引号", str(ctx.exception))

    def test_plotline_weights_must_sum(self) -> None:
        broken = SAMPLE_CARD.replace("weight: 0.6", "weight: 0.9")
        with self.assertRaises(ValueError) as ctx:
            style_engine.import_pack("unbalanced", self._card(broken), self.authors)
        self.assertIn("权重和", str(ctx.exception))

    def test_existing_pack_requires_force(self) -> None:
        card = self._card(SAMPLE_CARD)
        style_engine.import_pack("twice", card, self.authors)
        with self.assertRaises(FileExistsError):
            style_engine.import_pack("twice", card, self.authors)
        result = style_engine.import_pack("twice", card, self.authors, force=True)
        self.assertTrue(result["ok"])

    def test_invalid_slug_rejected(self) -> None:
        with self.assertRaises(ValueError):
            style_engine.import_pack("作者甲", self._card(SAMPLE_CARD), self.authors)

    def test_cli_dispatch_accepts_card_flag(self) -> None:
        """CLI 参数 --card 与函数参数 card_path 的映射必须有测试覆盖。"""
        card = self._card(SAMPLE_CARD)
        code = style_engine.main(
            ["import-pack", "--author", "cli-style", "--card", str(card),
             "--authors-root", str(self.authors)]
        )
        self.assertEqual(code, 0)
        self.assertTrue((self.authors / "cli-style" / "pack.json").exists())


if __name__ == "__main__":
    unittest.main()
