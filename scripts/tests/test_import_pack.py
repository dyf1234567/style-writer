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
    def test_section_shape_and_line_text_validation(self) -> None:
        for section in ("voice", "scene", "meta", "plotlines", "syntax", "imagery"):
            for value in ([{"person": "第三人称"}], 123, "文字", False):
                with self.subTest(section=section, value=value):
                    with self.assertRaisesRegex(ValueError, section):
                        style_engine._validate_card_types({section: value})
            style_engine._validate_card_types({section: None})
        for field in ("id", "role", "function", "pov"):
            for value in (123, True, ["文字"], {"key": "value"}):
                with self.assertRaisesRegex(ValueError, field):
                    style_engine._validate_card_types({"plotlines": {"lines": [{field: value}]}})
        with self.assertRaisesRegex(ValueError, "下限"):
            style_engine._validate_card_types({"scene": {"scene_words": [1800, 800]}})
        style_engine._validate_card_types({"scene": {"scene_words": [800, 800]}})

    def test_duplicate_keys_rejected_in_all_mapping_contexts(self) -> None:
        for text in ("voice:\n  person: 第一人称\n  person: 第二人称",
                     "voice: null\nvoice: null",
                     "plotlines:\n  lines:\n    - id: A\n      id: B",
                     "plotlines:\n  lines:\n    - id: A\n      role: 主线\n      role: 副线"):
            with self.assertRaisesRegex(ValueError, r"line \d+: duplicate key"):
                style_engine._parse_restricted(text)
        parsed = style_engine._parse_restricted("plotlines:\n  lines:\n    - id: A\n    - id: B")
        self.assertEqual(len(parsed["plotlines"]["lines"]), 2)

    def test_all_labeled_text_fields_reject_non_strings(self) -> None:
        for path in (*style_engine.CARD_TRAIT_LABELS, *style_engine.CARD_CONTROL_LABELS):
            section, key = path.split(".")
            for value in (123, False, ["触觉", "听觉"], {"key": "value"}):
                with self.subTest(path=path, value=value):
                    with self.assertRaises(ValueError) as caught:
                        style_engine._validate_card_types({section: {key: value}})
                    self.assertIn(path, str(caught.exception))
            for value in (None, "", "克制表达"):
                style_engine._validate_card_types({section: {key: value}})

    def test_invalid_text_import_does_not_overwrite(self) -> None:
        style_engine.import_pack("demo", self._card(SAMPLE_CARD), self.authors)
        target = self.authors / "demo" / "pack.json"
        before = target.read_bytes()
        for body in ("voice:\n  person: 123\nscene:\n  sensory_mix: 触觉优先",
                     "voice:\n  person: 第三人称\nscene:\n  sensory_mix:\n    - 触觉\n    - 听觉"):
            with self.assertRaises(ValueError):
                style_engine.import_pack("demo", self._card(body), self.authors, force=True)
            self.assertEqual(target.read_bytes(), before)

    def test_partial_scene_range_reports_missing_boundary(self) -> None:
        for values, warning_index in (([800, ""], 1), (["", 1800], 0), ([800, None], 1),
                                      ([None, 1800], 0), ([0, ""], None),
                                      ([0, 1800], 0), ([800, 0], 1),
                                      ([800, 1800], None), ([0, 0], None), (["", ""], None),
                                      ([None, None], None)):
            with self.subTest(values=values):
                body = "voice:\n  person: 第三人称\nscene:\n  scene_words: " + json.dumps(values)
                result = style_engine.import_pack("ranges", self._card(body), self.authors, force=True)
                range_warnings = [w for w in result["warnings"] if "scene.scene_words[" in w]
                _, pack = style_engine.resolve_pack("ranges", self.authors)
                if warning_index is not None:
                    self.assertEqual(len(range_warnings), 1)
                    self.assertIn(f"[{warning_index}]", range_warnings[0])
                    self.assertFalse(any("单场景字数区间" in t for t in pack["scene_controls"]))
                else:
                    self.assertFalse(range_warnings)
                expected_nulls = [f"scene.scene_words[{i}]" for i, v in enumerate(values) if v is None]
                self.assertEqual(result["explicit_null_fields"], expected_nulls)
                if values == [800, 1800]:
                    self.assertIn("单场景字数区间：800-1800", pack["scene_controls"])

    def test_yaml_scalar_errors_include_source_line(self) -> None:
        for body, line in (("voice:\n  register: \"口语化", 2),
                           ("items:\n  - \"未闭合", 2),
                           ("items:\n  - id: a\n    pov: \"未闭合", 3),
                           ('voice:\n  register: "\\q"', 2)):
            with self.subTest(body=body), self.assertRaisesRegex(ValueError, f"^line {line}: "):
                style_engine._parse_restricted(body)

    def test_schema_error_shows_string_quotes(self) -> None:
        with patch.object(Path, "exists", return_value=True), patch.object(
                Path, "read_text", return_value='{"schema_version":"1","slug":"demo"}'):
            with self.assertRaisesRegex(ValueError, "unsupported pack schema: '1'"):
                style_engine.resolve_pack("demo", self.authors)

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

    def test_corrupt_pack_requires_explicit_recovery(self) -> None:
        card = self._card(SAMPLE_CARD)
        folder = self.authors / "broken"
        folder.mkdir(parents=True)
        target = folder / "pack.json"
        for damaged in (b'{"schema_version":1,}', b'[]', b'\xff',
                        b'{"schema_version":null}', b'{"schema_version":2}',
                        b'{"schema_version":1,"slug":"someone-else"}',
                        b'{"schema_version":1,"slug":"broken","corpus":12}',
                        b'{"schema_version":1,"slug":"broken","families":"wrong"}'):
            with self.subTest(damaged=damaged):
                target.write_bytes(damaged)
                with self.assertRaisesRegex(ValueError, "discard-existing-config"):
                    style_engine.import_pack("broken", card, self.authors, force=True)
                self.assertEqual(target.read_bytes(), damaged)
                result = style_engine.import_pack("broken", card, self.authors, force=True,
                                                 discard_existing_config=True)
                self.assertTrue(any("已放弃旧配置" in w for w in result["warnings"]))
                _, restored = style_engine.resolve_pack("broken", self.authors)
                self.assertEqual(restored["default_family"], "other")

    def test_discard_requires_force_and_cli_accepts_pair(self) -> None:
        card = self._card(SAMPLE_CARD)
        with self.assertRaisesRegex(ValueError, "必须与 --force"):
            style_engine.import_pack("new", card, self.authors, discard_existing_config=True)
        self.assertFalse((self.authors / "new").exists())
        args = ["import-pack", "--author", "new", "--card", str(card),
                "--authors-root", str(self.authors), "--discard-existing-config"]
        self.assertNotEqual(style_engine.main(args), 0)
        self.assertEqual(style_engine.main(args + ["--force"]), 0)

    def test_force_does_not_swallow_permission_error(self) -> None:
        card = self._card(SAMPLE_CARD)
        style_engine.import_pack("permission", card, self.authors)
        with patch.object(style_engine, "resolve_pack", side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                style_engine.import_pack("permission", card, self.authors, force=True)

    def test_redline_failure_preserves_old_bytes(self) -> None:
        card = self._card(SAMPLE_CARD)
        style_engine.import_pack("safe", card, self.authors)
        target = self.authors / "safe" / "pack.json"
        original = target.read_bytes()
        card = self._card(SAMPLE_CARD.replace('person: "第三人称"', 'person: "“原句”"'))
        for discard in (False, True):
            with self.assertRaisesRegex(ValueError, "红线"):
                style_engine.import_pack("safe", card, self.authors, force=True,
                                         discard_existing_config=discard)
            self.assertEqual(target.read_bytes(), original)

    def test_atomic_write_and_replace_failures_preserve_old_bytes(self) -> None:
        card = self._card(SAMPLE_CARD)
        style_engine.import_pack("safe", card, self.authors)
        target = self.authors / "safe" / "pack.json"
        original = target.read_bytes()
        with patch.object(style_engine.os, "replace", side_effect=OSError("replace failed")):
            with self.assertRaises(OSError):
                style_engine.import_pack("safe", card, self.authors, force=True)
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(list(target.parent.glob(".pack-*.tmp")), [])
        factory = style_engine.tempfile.NamedTemporaryFile

        def failing_file(*args, **kwargs):
            stream = factory(*args, **kwargs)
            write = stream.write

            def partial_write(text):
                write(text[:10])
                stream.flush()
                raise OSError("disk full")

            stream.write = partial_write
            return stream

        with patch.object(style_engine.tempfile, "NamedTemporaryFile", side_effect=failing_file):
            with self.assertRaisesRegex(OSError, "disk full"):
                style_engine.import_pack("safe", card, self.authors, force=True)
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(list(target.parent.glob(".pack-*.tmp")), [])

    def test_null_numeric_fields_skip_and_quoted_null_is_not_numeric(self) -> None:
        for literal in ("null", "Null", "NULL", "~"):
            with self.subTest(literal=literal):
                self.assertIsNone(style_engine._scalar(literal))
                card = self._card(SAMPLE_CARD.replace("description_ratio: 0.22", "description_ratio: " + literal))
                style_engine.import_pack("nullable", card, self.authors, force=True)
                _, pack = style_engine.resolve_pack("nullable", self.authors)
                self.assertFalse(any("描写占比" in t for t in pack["traits"]))
        self.assertEqual(style_engine._scalar('"null"'), "null")
        for literal in ('"null"', '"0.2"', "true", "false", "NaN", "inf", "-inf", ".nan"):
            with self.subTest(literal=literal):
                card = self._card(SAMPLE_CARD.replace("description_ratio: 0.22", "description_ratio: " + literal))
                with self.assertRaisesRegex(ValueError, "scene.description_ratio"):
                    style_engine.import_pack("invalid", card, self.authors)

    def test_explicit_null_paths_refresh_without_entering_prompt(self) -> None:
        card = self._card('''voice:
  person: 第三人称
  distance: "null"
scene:
  description_ratio: null
plotlines:
  lines:
    - id: A
      weight: ~
meta:
  sample_words: NULL
''')
        expected = ["meta.sample_words", "plotlines.lines[0].weight", "scene.description_ratio"]
        result = style_engine.import_pack("nullable", card, self.authors)
        _, pack = style_engine.resolve_pack("nullable", self.authors)
        self.assertEqual(result["explicit_null_fields"], expected)
        self.assertEqual(pack["explicit_null_fields"], expected)
        status = style_engine.status("nullable", self.authors, self.root / "indexes")
        context = style_engine.prepare_context("nullable", "场景", self.authors, self.root / "indexes")
        self.assertEqual(status["explicit_null_fields"], expected)
        self.assertEqual(context["explicit_null_fields"], expected)
        self.assertNotIn("explicit_null_fields", context["writing_context"])
        self.assertNotIn("scene.description_ratio", json.dumps(context["writing_context"]))
        result = style_engine.import_pack("nullable", self._card(SAMPLE_CARD), self.authors, force=True)
        self.assertEqual(result["explicit_null_fields"], [])
        _, updated = style_engine.resolve_pack("nullable", self.authors)
        self.assertEqual(updated["explicit_null_fields"], [])

    def test_colon_rules_and_mapping_contexts(self) -> None:
        for literal, expected in (("避免:连续感叹号", "避免:连续感叹号"),
                                  ('"避免: 连续感叹号"', "避免: 连续感叹号"),
                                  ("https://example.test", "https://example.test"),
                                  ("12:30", "12:30")):
            self.assertEqual(style_engine._parse_restricted("items:\n  - " + literal)["items"], [expected])
        self.assertEqual(style_engine._parse_restricted("items:\n  - 避免: 连续感叹号")["items"],
                         [{"避免": "连续感叹号"}])
        for text in ("pov:人物甲", "plotlines:\n  lines:\n    - id: A\n      pov:人物甲"):
            with self.assertRaises(ValueError):
                style_engine._parse_restricted(text)
        parsed = style_engine._parse_restricted("plotlines:\n  lines:\n    - id: A\n      pov: 人物甲")
        self.assertEqual(parsed["plotlines"]["lines"][0]["pov"], "人物甲")

    def test_all_string_lists_validate_block_and_inline_items(self) -> None:
        for section, name in (("imagery", "taboo"), ("imagery", "semantic_domains"),
                              ("syntax", "lexical_fingerprint")):
            for value in ("\n    - 避免: 连续感叹号", "[123, true]", "[null]", "123"):
                with self.subTest(field=name, value=value):
                    card = self._card("voice:\n  person: 第三人称\n" + section + ":\n  " + name + ": " + value)
                    with self.assertRaisesRegex(ValueError, section + r"\." + name):
                        style_engine.import_pack("invalid", card, self.authors)

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
