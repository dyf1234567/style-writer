#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""measure.py 分章逻辑回归测试。

覆盖历史 bug 与关键口径（顺序即优先级）：
  T1 带全角冒号的章节标题（第一章：xxx）必须保留 —— F2a' v3 修正的回归用例。
     旧实现会把此类标题整行跳过：章节数坍缩、<3 个标记时整体退化为 block 切分。
  T2 正文行首的「第十六章…」引用句（行内含句读）仍须被 F2a 过滤，不得生成假章节。
  T3 目录行仍须被 F2b 距离过滤整块剔除，不得漏进正文。
  T4 无章节标记的文本退化为空行切块（主流程不回归）。
  T5 短尾章（尾声 < min_gap 字）必须保留 —— F2b' v3 修正的回归用例。
     旧实现用 len(text) 当末候选的「下一标记」，末章篇幅 < 300 字即被吞并，
     导致 kept < 3 整体退化为 block 切分。

运行（在项目根目录）：
    python -m unittest discover -s tests -v
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import measure  # noqa: E402

S1 = "雨点敲着窗玻璃，他数着拍子等天亮，终于等来那声门响。"
S2 = "她站在灯下没有动，手里攥着一封信，纸角已经被汗浸软了。"
S3 = "山道很长，马蹄声碎在雾里，前头的人忽然勒住了缰绳。"

# 章节间距必须 > min_gap(300) 才不会被目录过滤误删：
# 每行正文 ~27 字 + 换行 ≈ 28 字符，12 行 ≈ 340 字符，留出安全余量。
BODY_LINES = 12


def body(lines: int, start: int = 0) -> str:
    pool = [S1, S2, S3]
    return "\n".join(pool[(start + i) % 3] for i in range(lines))


def chapter(title: str, lines: int = BODY_LINES) -> str:
    return f"{title}\n{body(lines)}"


class TestChapterSplit(unittest.TestCase):
    def test_T1_colon_titles_are_kept(self):
        text = "\n\n".join(
            [chapter("第一章：重生"), chapter("第二章：云涌"), chapter("第三章：归途")]
        )
        spans, note = measure.split_chapters(text)
        self.assertTrue(note.startswith("chapter"), note)
        self.assertEqual(len(spans), 3)

    def test_T2_dialogue_pseudo_trigger_still_filtered(self):
        ch2_head = "第二章：云涌\n第十六章的事情他全忘了，说了也是白说。\n"
        text = "\n\n".join(
            [
                chapter("第一章：风起"),
                ch2_head + body(BODY_LINES + 1),
                chapter("第三章：归途", 6),
            ]
        )
        spans, note = measure.split_chapters(text)
        self.assertTrue(note.startswith("chapter"), note)
        self.assertEqual(len(spans), 3)

    def test_T3_toc_lines_are_filtered_by_gap(self):
        toc = (
            "第一章：风起 ............... 1\n"
            "第二章：云涌 ............... 2\n"
            "第三章：归途 ............... 3\n"
        )
        text = toc + "\n\n" + "\n\n".join(
            [chapter("第一章：风起"), chapter("第二章：云涌"), chapter("第三章：归途")]
        )
        spans, note = measure.split_chapters(text)
        self.assertTrue(note.startswith("chapter"), note)
        self.assertEqual(len(spans), 3)

    def test_T4_fallback_to_blocks_when_no_chapter_markers(self):
        text = "\n\n\n\n".join(body(BODY_LINES - 3) for _ in range(4))
        spans, note = measure.split_chapters(text)
        self.assertTrue(note.startswith("block"), note)
        self.assertGreaterEqual(len(spans), 3)

    def test_T5_short_tail_chapter_is_kept(self):
        # 末章「尾声」仅 5 行 ≈ 140 字 < min_gap(300)，仍必须保留
        text = "\n\n".join(
            [
                chapter("第一章：风起"),
                chapter("第二章：云涌"),
                chapter("尾声", 5),
            ]
        )
        spans, note = measure.split_chapters(text)
        self.assertTrue(note.startswith("chapter"), note)
        self.assertEqual(len(spans), 3)


if __name__ == "__main__":
    unittest.main()
