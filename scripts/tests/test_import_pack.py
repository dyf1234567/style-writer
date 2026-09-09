from __future__ import annotations

import json
import sys
import tempfile
import unittest
from unittest.mock import patch
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
  lexical_fingerprint: ["忽然", "顿时"]

dialogue_style:
  subtext_density: "高"
  individuality: "句式"
  exposition_in_dialogue: "少"

imagery:
  semantic_domains: ["天气", "身体感"]
  metaphor_type: "明喻为主"
  recurrence_interval: "每卷翻转一次"
  taboo: []
  simile_markers_per_1k: 5.3

emotion_writing:
  carrier: "身体反应与动作细节"
  escalation_pattern: "层层加压后单次释放"
  restraint_level: "克制"
  peak_label: "峰值处切短段高速剪辑"
  emotion_words_per_1k: 8.7
  body_reaction_ratio: "约四成（置信度中）"

reward_rhythm:
  payoff_unit: "翻案与身份揭示"
  payoff_interval: 6
  buildup_release_ratio: "4:1"
  installment_style: "分层递进结清"
  promise_drift: "平均滞后 12 章"
  curve_within_arc: "双峰"
  chapter_micro_payoff: "每章必带信息增量"
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
        # v2 card dimensions
        self.assertIn("情绪载体：身体反应与动作细节", traits_text)
        self.assertIn("情绪词密度(每千字)：8.7", traits_text)
        self.assertIn("明喻标记密度(每千字)：5.3", traits_text)
        self.assertIn("词汇指纹：忽然、顿时", traits_text)
        self.assertIn("压抑/释放比：4:1", traits_text)
        self.assertIn("兑现单元：翻案与身份揭示", controls_text)
        self.assertIn("小回报间隔：每 6 章", controls_text)
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

    def test_force_preserves_runtime_config_and_uses_existing_corpus_env(self) -> None:
        card = self._card(SAMPLE_CARD)
        style_engine.import_pack("keep", card, self.authors)
        directory, pack = style_engine.resolve_pack("keep", self.authors)
        config = {"display_name": "自定义", "corpus": {"env": "CUSTOM_CORPUS"},
                  "families": [{"id": "modern", "patterns": ["现代"]}],
                  "default_family": "modern", "unmatched_family": "unknown",
                  "exclude_patterns": ["私稿"]}
        pack.update(config)
        (directory / "pack.json").write_text(json.dumps(pack), encoding="utf-8")
        with patch.object(style_engine, "_read_corpus", return_value=(None, "测试无语料")) as reader:
            style_engine.import_pack("keep", card, self.authors, force=True)
        reader.assert_called_once_with(None, "CUSTOM_CORPUS")
        _, updated = style_engine.resolve_pack("keep", self.authors)
        self.assertEqual({key: updated[key] for key in config}, config)
        style_engine.import_pack("keep", card, self.authors, force=True,
                                 display_name="改名", corpus_env="OTHER_CORPUS")
        _, updated = style_engine.resolve_pack("keep", self.authors)
        self.assertEqual(updated["display_name"], "改名")
        self.assertEqual(updated["corpus"]["env"], "OTHER_CORPUS")

    def test_empty_or_too_short_corpus_is_skipped(self) -> None:
        corpus = self.root / "empty.txt"
        for text in ("", "ASCII only", "短文"):
            corpus.write_text(text, encoding="utf-8")
            result = style_engine.import_pack("empty", self._card(SAMPLE_CARD), self.authors,
                                             corpus_root=corpus, force=True)
            self.assertEqual(result["redline"]["source_overlap"]["status"], "skipped")
            self.assertTrue(result["warnings"])
        with self.assertRaises(ValueError):
            style_engine.import_pack("bad", self._card(SAMPLE_CARD), self.authors, overlap_run=0)

    def test_yaml_inline_quotes_are_preserved(self) -> None:
        text = '''imagery:
  semantic_domains: ["天气, 器物", "身体 # 动作", 'it''s quiet'] # 注释
voice:
  person: "他说\\\"你好\\\"" # 注释
'''
        card = style_engine._parse_restricted(text)
        self.assertEqual(card["imagery"]["semantic_domains"], ["天气, 器物", "身体 # 动作", "it's quiet"])
        self.assertEqual(card["voice"]["person"], '他说"你好"')

    def test_yaml_invalid_quotes_fail_explicitly(self) -> None:
        for value in ('["未闭合]', '"完整" 垃圾', "'未闭合", '[a,,b]', '[a, [b]]', '[a, b'):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    style_engine._parse_restricted("field: " + value)

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

    # ------------------------------------------------ 红线覆盖面
    def _corpus(self, text: str) -> Path:
        root = self.root / "corpus"
        root.mkdir(exist_ok=True)
        path = root / "work.txt"
        path.write_text(text, encoding="utf-8")
        return root

    def test_corner_and_single_quotes_are_rejected(self) -> None:
        for label, ins in (
            ("「」", 'register: "口语化，常说「来了老弟」"'),
            ("『』", 'register: "口语化，常说『来了老弟』"'),
            ("‘’", 'register: "口语化，常说‘来了老弟’"'),
        ):
            with self.subTest(marks=label):
                with self.assertRaises(ValueError) as ctx:
                    style_engine.import_pack("corner", self._card(
                        SAMPLE_CARD.replace('register: "口语化"', ins)), self.authors)
                self.assertIn("直角引号", str(ctx.exception))

    def test_bare_source_sentence_is_caught_by_corpus_check(self) -> None:
        """不带任何引号的原文摘录：只有与语料比对才拦得住（旧实现完全放行）。"""
        leaked = SAMPLE_CARD.replace(
            'register: "口语化"', 'register: "口语化，老兵把军牌埋进雪里转身朝山口走去"'
        )
        corpus = self._corpus("那天雪很大。老兵把军牌埋进雪里，转身朝山口走去，再也没有回来。\n")
        with self.assertRaises(ValueError) as ctx:
            style_engine.import_pack("bare", self._card(leaked), self.authors, corpus_root=corpus)
        self.assertIn("连续重合", str(ctx.exception))
        self.assertNotIn("军牌", str(ctx.exception), "报错信息不得把原文打出来")

    def test_clean_card_passes_against_unrelated_corpus(self) -> None:
        """阴性对照：语料用词相近但没有连续重合时不得误报，否则红线会被人关掉。"""
        corpus = self._corpus("雪下了整夜。老兵转身朝山口走去。军牌埋在雪里。" * 20)
        result = style_engine.import_pack(
            "clean", self._card(SAMPLE_CARD), self.authors, corpus_root=corpus)
        self.assertTrue(result["ok"])
        self.assertEqual(result["redline"]["source_overlap"]["status"], "checked")
        self.assertEqual(result["warnings"], [])

    def test_unreachable_corpus_is_reported_not_silently_passed(self) -> None:
        result = style_engine.import_pack("nocorpus", self._card(SAMPLE_CARD), self.authors)
        self.assertTrue(result["ok"])
        self.assertEqual(result["redline"]["source_overlap"]["status"], "skipped")
        self.assertTrue(any("未执行" in w for w in result["warnings"]), result["warnings"])

    def test_cli_dispatch_accepts_corpus_root(self) -> None:
        corpus = self._corpus("城墙外的旌旗在风里倒向北方，将军没有回头。")
        code = style_engine.main([
            "import-pack", "--author", "cli-corpus", "--card", str(self._card(SAMPLE_CARD)),
            "--authors-root", str(self.authors), "--corpus-root", str(corpus), "--overlap-run", "8",
        ])
        self.assertEqual(code, 0)
        pack = json.loads((self.authors / "cli-corpus" / "pack.json").read_text(encoding="utf-8"))
        self.assertTrue(pack["traits"])


if __name__ == "__main__":
    unittest.main()
