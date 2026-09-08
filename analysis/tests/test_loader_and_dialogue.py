#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""measure.py v5 载入与对白口径回归测试。

覆盖 v5 的三处「静默失真」（顺序即优先级）：
  L1 (F4) UTF-8 BOM + 首行即章节标题：必须仍走 chapter 切分，卷首单元不得丢。
          旧实现先试 utf-8，BOM 留在开头使 ^ 匹配不到首个标记。
  L1b(F4) CRLF/GBK 语料与 LF 语料载入结果一致（\r 统一在 load_document 里做）。
  L2 (F5) 「」排版：对白占比必须与 “” 排版一致。旧实现只认 “”，整本算成 0。
  L3 (F5) 超过 300 字的长对白必须计入。
  L4 (F5) 整篇匹配不到对白块时必须在 warnings 里说出来（0 会被 import-pack 当未填丢弃）。
  L5 (F6) epub spine 里解析不到的 href 必须计数上报，不得静默丢页。
  L6 (F6) href 含 %20 等百分号编码时必须能解析到文件。
  L7 (F5) epub 用 &ldquo;/&rdquo; 实体时对白不得归零（先剥标签再 unescape）。
  L8      干净文档不得产生 warnings（L4/L5 的阴性对照，否则 PASS 可能是空转）。
  L9      退化到 block 切分时必须告警：章级指标口径已变。

运行（在 analysis 目录）：
    python -m unittest discover -s tests -v
"""

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import measure  # noqa: E402

S1 = "雨点敲着窗玻璃，他数着拍子等天亮，终于等来那声门响。"
S2 = "她站在灯下没有动，手里攥着一封信，纸角已经被汗浸软了。"
BODY = "\n".join([S1, S2] * 12)


def chapters(titles):
    return "\n\n".join(f"{t}\n{BODY}" for t in titles)


def build_epub(path, pages, encode_quotes=False, break_files=False, encoded_href=False):
    """pages: [(spine_name_in_opf, zip_name, [paragraphs])]，够搭出合法 epub。"""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml",
                   '<?xml version="1.0"?><container><rootfiles>'
                   '<rootfile full-path="OEBPS/content.opf"/></rootfiles></container>')
        items, refs = [], []
        for idx, (_href_hint, zip_name, paras) in enumerate(pages, 1):
            html = ["<?xml version='1.0'?><html><head><title>t</title></head><body>"]
            for p in paras:
                if encode_quotes:
                    p = p.replace("“", "&ldquo;").replace("”", "&rdquo;")
                html.append(f"<p>{p}</p>")
            html.append("</body></html>")
            # break_files: OPF 里声明两页，归档里只放第一页 -> href 解析不到
            if not (break_files and idx == len(pages)):
                z.writestr(zip_name, "\n".join(html))
            declared = Path(zip_name).name.replace(" ", "%20") if encoded_href else Path(zip_name).name
            items.append(f'<item id="i{idx}" href="{declared}" media-type="application/xhtml+xml"/>')
            refs.append(f'<itemref idref="i{idx}"/>')
        z.writestr("OEBPS/content.opf",
                   "<package><manifest>" + "".join(items) + "</manifest><spine>"
                   + "".join(refs) + "</spine></package>")


class LoaderAndDialogueTests(unittest.TestCase):
    # ---------------------------------------------------------------- F4
    def test_L1_bom_file_keeps_first_chapter(self):
        text = chapters(["序章", "第一章：重生", "第二章：云涌"])
        clean = Path(self.tmp) / "clean.txt"
        bom = Path(self.tmp) / "bom.txt"
        clean.write_bytes(text.encode("utf-8"))
        bom.write_bytes(b"\xef\xbb\xbf" + text.encode("utf-8"))
        ct, cinfo = measure.load_document(clean)
        bt, binfo = measure.load_document(bom)
        self.assertFalse(bt.startswith("\ufeff"), "BOM 必须被 utf-8-sig 吃掉")
        self.assertEqual(binfo.get("bom"), "stripped-by-utf-8-sig")
        cu, ckind = measure.split_chapters(ct)
        bu, bkind = measure.split_chapters(bt)
        self.assertTrue(bkind.startswith("chapter"), bkind)
        self.assertEqual(len(cu), len(bu))
        self.assertEqual(cu[0], bu[0], "卷首单元内容必须一致")

    def test_L1b_crlf_corpus_loads_like_lf(self):
        text = chapters(["序章", "第一章：重生", "第二章：云涌"])
        p = Path(self.tmp) / "crlf.txt"
        p.write_bytes(text.replace("\n", "\r\n").encode("gb18030"))
        t, info = measure.load_document(p)
        self.assertNotIn("\r", t)
        units, kind = measure.split_chapters(t)
        self.assertTrue(kind.startswith("chapter"), kind)
        self.assertEqual(len(units), 3)

    # ---------------------------------------------------------------- F5
    def test_L2_corner_bracket_dialogue_matches_curly(self):
        curly = chapters(["第一章：起", "第二章：承", "第三章：转"]) + "\n\n" + "\n".join(
            ["他说：“这件事我们得从长计议，不能就这么算了。”"] * 12)
        corner = curly.replace("“", "「").replace("”", "」")
        mc = measure.measure(curly)
        mk = measure.measure(corner)
        self.assertGreater(mc["dialogue"]["char_ratio"], 0.1)
        self.assertAlmostEqual(mc["dialogue"]["char_ratio"], mk["dialogue"]["char_ratio"], places=3)
        self.assertEqual(mk["warnings"], [])

    def test_L3_long_speech_is_counted(self):
        speech = "“" + "他说了一遍又一遍，窗外的雨始终没有停。" * 12 + "”"  # >160 字，配合正文足够长
        text = chapters([f"第{i}章：起" for i in range(1, 6)]) + "\n\n" + speech
        m = measure.measure(text)
        self.assertGreater(m["dialogue"]["blocks_per_1k_chars"], 0)

    def test_L4_zero_dialogue_is_reported_not_silent(self):
        text = chapters([f"第{i}章：起" for i in range(1, 40)])  # >5000 字，全书无引号
        m = measure.measure(text)
        self.assertEqual(m["dialogue"]["char_ratio"], 0.0)
        self.assertGreater(m["total_chars_cn"], 5000)
        self.assertTrue(any("没匹配到任何对白块" in w for w in m["warnings"]), m["warnings"])

    def test_L5_unresolved_spine_entries_are_reported(self):
        p = Path(self.tmp) / "partial.epub"
        build_epub(p, [("ch1.xhtml", "OEBPS/ch1.xhtml", [S1, S2]),
                       ("ch2.xhtml", "OEBPS/ch2.xhtml", [S1, S2])], break_files=True)
        text, info = measure.load_document(p)
        m = measure.measure(text, source=info)
        self.assertEqual(info["spine_items"], 2)
        self.assertEqual(info["files_read"], 1)
        self.assertEqual(info["missing_hrefs"], ["OEBPS/ch2.xhtml"])
        self.assertTrue(any("spine 条目没解析出来" in w for w in m["warnings"]), m["warnings"])

    def test_L6_percent_encoded_href_resolves(self):
        p = Path(self.tmp) / "space.epub"
        build_epub(p, [("ch ap1.xhtml", "OEBPS/ch ap1.xhtml", [S1, S2]),
                       ("ch ap2.xhtml", "OEBPS/ch ap2.xhtml", [S1, S2])], encoded_href=True)
        text, info = measure.load_document(p)
        self.assertEqual(info["missing_hrefs"], [])
        self.assertEqual(info["files_read"], 2)
        self.assertGreater(measure.cn_len(text), 0)

    def test_L7_entity_quotes_still_count_as_dialogue(self):
        p = Path(self.tmp) / "entity.epub"
        speech = "他说：“这件事我们得从长计议，不能就这么算了。”"
        build_epub(p, [("ch1.xhtml", "OEBPS/ch1.xhtml", [speech] * 12)], encode_quotes=True)
        text, info = measure.load_document(p)
        m = measure.measure(text, source=info)
        self.assertGreater(m["dialogue"]["char_ratio"], 0.3, "&ldquo; 未还原会导致对白归零")

    # ---------------------------------------------------------------- 对照
    def test_L8_clean_document_has_no_warnings(self):
        m = measure.measure(chapters([f"第{i}章：起" for i in range(1, 8)])
                            + "\n\n" + "\n".join(["她说：「明天见。」"] * 5))
        self.assertEqual(m["warnings"], [], m["warnings"])
        self.assertTrue(m["unit_kind"].startswith("chapter"), m["unit_kind"])
        self.assertGreater(m["dialogue"]["char_ratio"], 0)
        self.assertIn("警告          : 无", measure.summarize(m))

    def test_L9_block_fallback_warns(self):
        m = measure.measure("\n\n".join(BODY for _ in range(6)))
        self.assertTrue(m["unit_kind"].startswith("block"))
        self.assertTrue(any("按空行切块" in w for w in m["warnings"]), m["warnings"])

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.tmp = self._temp.name

    def tearDown(self):
        self._temp.cleanup()


if __name__ == "__main__":
    unittest.main()
