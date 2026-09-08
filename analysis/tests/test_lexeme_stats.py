#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""measure.py v4 词层指标回归测试。

覆盖：
  W1 top_lexemes 只收词表命中项，频次=每千字，降序排列。
  W2 长词优先摘除，组合词不重复计入单字（好像 不计入 像）。
  W3 明喻长/短词表拆分计数；输出不含任何原文片段（红线：值全是
     封闭词表词 + 数字）。
  W4 情绪词长词同样摘除后再数单字，避免 喉咙/想哭 里的字重复。
  W5 summarize() 包含三行新指标且不抛错。

运行（在 analysis 目录）：
    python -m unittest discover -s tests -v
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import measure  # noqa: E402

# 40 个汉字一句的模板，方便精确控制每千字频次
def line(n):
    return "他忽然觉得冷起来忽然又暖起来可是风还是那么大吹得窗子直响。" * n


class LexemeStatsTests(unittest.TestCase):
    def test_top_lexemes_counts_and_ordering(self) -> None:
        text = "他终于明白了。" * 100  # 6 汉字/句 ×100 = 600 字 → 终于 ×100 = 166.667/千字
        m = measure.lexeme_stats(text, measure.cn_len(text))
        top = {item["word"]: item["per_1k"] for item in m["top_lexemes"]}
        self.assertEqual(top["终于"], 166.667)
        values = [item["per_1k"] for item in m["top_lexemes"]]
        self.assertEqual(values, sorted(values, reverse=True))

    def test_long_word_absorption_simile(self) -> None:
        # 好像×10：应计入 long 表，短词「像」不得重复计数
        text = "这好像一堵墙。" * 10
        m = measure.lexeme_stats(text, measure.cn_len(text))
        markers = m["simile"]["markers"]
        self.assertIn("好像", markers)
        self.assertNotIn("像", markers)  # 全部被长词吸收

    def test_simile_short_remnant(self) -> None:
        # 2 个「好像」(long) + 3 个「像」(short 残余)
        text = "好像" * 2 + "人像" * 3
        m = measure.lexeme_stats(text, measure.cn_len(text))
        markers = m["simile"]["markers"]
        self.assertEqual(markers["好像"], 2)
        self.assertEqual(markers["像"], 3)

    def test_emotion_long_absorption(self) -> None:
        # 喉咙×5：喉/咙 不在短表，但「想哭」吸收「哭」不重复
        text = "他喉咙发紧。" * 5 + "她想哭了。" * 4
        m = measure.lexeme_stats(text, measure.cn_len(text))
        words = {item["word"] for item in m["emotion"]["top"]}
        self.assertIn("喉咙", words)
        self.assertIn("想哭", words)
        # 「哭」单独不应再出现（被 想哭 吸收）
        self.assertNotIn("哭", words)

    def test_output_contains_no_source_text(self) -> None:
        """红线：词层输出只能出现封闭词表的词与数字。"""
        sample = "他忽然想哭，眼眶发热，这一切像梦一样。" * 8
        m = measure.lexeme_stats(sample, measure.cn_len(sample))
        allowed = set(measure.LEXEME_WATCH) | set(measure.EMOTION_WATCH) \
            | set(measure.SIMILE_LONG) | set(measure.SIMILE_SHORT)
        emitted = {item["word"] for item in m["top_lexemes"]} \
            | {item["word"] for item in m["emotion"]["top"]} \
            | set(m["simile"]["markers"])
        self.assertTrue(emitted <= allowed, emitted - allowed)

    def test_measure_end_to_end_and_summary(self) -> None:
        chapters = []
        for i in range(1, 6):
            title = f"第{i}章"
            body = line(6) + "他忽然觉得，这消息像针。她眼眶一热，喉咙发紧。" * 12
            chapters.append(f"{title}\n\n{body}")
        text = "\n\n".join(chapters)
        m = measure.measure(text)
        self.assertIn("lexeme", m)
        out = measure.summarize(m)
        for key in ("口癖top", "情绪词密度", "明喻标记密度"):
            self.assertIn(key, out)


if __name__ == "__main__":
    unittest.main()
