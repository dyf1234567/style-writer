#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小说文本量化统计 —— 为「叙事风格特征卡」提供硬数据。

设计原则
--------
1. 只输出统计量与分布，**不输出任何原文片段**。
   风格卡要进生成提示词，混入原文句子等于把受版权保护的文字带进产出。
2. 纯标准库，无第三方依赖，txt / epub 都能读。
3. 指标服务于「照着参数能复现手感」，不是语言学测量，够用即可。

口径修正（v2，来自 dragon-raja-style 项目的实测回退）
------------------------------------------------------
F1 分章正则必须认 序章/楔子/尾声/终章/后记。
   否则每卷的卷首卷尾单元被整块丢弃 —— 而卷首往往是最重要的视角单元
   （实测某样本卷首单元占全卷 11.5% 字数）。
F2 必须过滤伪触发与目录行。
   F2a 正文对白里的「第十六章…说的」会被 ^第N章 命中，凭空多出一章；
       判据是标题行内含句读或右引号。
   F2b 相邻章节标记间距 < 300 字判为目录行。
F3 『』 必须从对白里拆出，单列为「叙述者评论」。
   否则章首抒情引子会被记为对话，而它恰是 narrator_comment_ratio 的测量对象。

口径修正（v3，2026-09-06，novel-style-kit 回归测试暴露）
------------------------------------------------------
F2a' F2a 的伪触发排除集曾含全角冒号「：」，把「第一章：重生」类真实标题
     整行跳过 → 章节静默合并、字数分布失真。
     修复：排除集移除「：」；正文引用句仍由句读（。！？，；）与右引号兜底过滤。
F2b' 目录过滤对最后一个候选曾用 len(text) 当「下一标记」间距，篇幅
     < min_gap 的短尾章（尾声/后记/终章）会被误判为目录行吞并 → 末单元丢失。
     修复：末候选改为「紧跟前一候选（同属目录块）才滤除」。

用法
----
    python measure.py <file.txt|file.epub> [-o metrics.json] [--min-gap 300]

输出
----
    控制台打印人类可读摘要，JSON 写入 -o 指定路径（默认 <原名>.metrics.json）。
"""

import argparse
import json
import re
import statistics
import sys
import zipfile
from pathlib import Path

CN = r"\u4e00-\u9fa5"
SENT_END = re.compile(r"[。！？!?…]+[”』」]*")

# F3：对白与叙述者评论分开计数
DIA_FULL = re.compile(r"“([^”]{1,300}?)”")
DIA_HALF = re.compile(r'"([^"]{1,300}?)"')
NARR_COMMENT = re.compile(r"『([^』]{1,300}?)』")

# F1：分章正则，含卷首尾单元
CHAPTER = re.compile(
    r"^[ \t\u3000]*(?:第\s*[0-9零一二三四五六七八九十百千两]+\s*[章节回卷篇]"
    r"|(?:序\s*章|序\s*曲|楔\s*子|尾\s*声|终\s*章|后\s*记|番\s*外|前\s*言|自\s*序)"
    r"|Chapter\s+\d+|CHAPTER\s+\d+)",
    re.MULTILINE,
)
PARATEXT = re.compile(r"版权|内容简介|收集整理|作者注|致各位亲爱的读者")
SEPARATOR = re.compile(r"^[ \t]*[-*＝=—─~～·]{3,}[ \t]*$")
HTML_TAG = re.compile(r"<[^>]+>")
BLANK = re.compile(r"\n\s*\n")
PUNCT_WATCH = {
    "！": "exclam",
    "？": "question",
    "…": "ellipsis",
    "—": "dash",
    "，": "comma",
    "。": "period",
    "；": "semicolon",
    "：": "colon",
    "、": "enum_comma",
}


# ---------------------------------------------------------------- 载入

def _decode(raw: bytes):
    for enc in ("utf-8", "utf-8-sig", "gb18030", "gbk", "big5"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def load_epub(path: Path) -> str:
    """按 spine 顺序拼正文。解析失败时退化为「按文件名顺序读所有 xhtml」."""
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        try:
            container = _decode(z.read("META-INF/container.xml"))
            opf_path = re.search(r'full-path="([^"]+)"', container).group(1)
            opf = _decode(z.read(opf_path))
            base = str(Path(opf_path).parent)
            manifest = {}
            for tag in re.findall(r"<item\b[^>]*>", opf):
                i = re.search(r'id="([^"]+)"', tag)
                h = re.search(r'href="([^"]+)"', tag)
                if i and h:
                    manifest[i.group(1)] = h.group(1)
            spine_ids = re.findall(r'<itemref[^>]+idref="([^"]+)"', opf)
            order = [manifest[i] for i in spine_ids if i in manifest]
            files = [f"{base}/{h}" if base != "." else h for h in order]
            files = [f for f in files if f in names]
        except Exception:
            files = sorted(
                n for n in names if n.lower().endswith((".xhtml", ".html", ".htm"))
            )
        parts = []
        for f in files:
            html = _decode(z.read(f))
            html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
            text = HTML_TAG.sub("\n", html)
            parts.append(text)
    return "\n".join(parts)


def load_text(path: Path) -> str:
    if path.suffix.lower() == ".epub":
        return load_epub(path)
    return _decode(path.read_bytes())


# ---------------------------------------------------------------- 切分

def strip_separators(body: str) -> str:
    lines = body.split("\n")
    while lines and (not lines[-1].strip() or SEPARATOR.match(lines[-1])):
        lines.pop()
    return "\n".join(lines)


def split_chapters(text: str, min_gap: int = 300):
    """返回 (单元正文列表, 切分方式说明)。

    F2：过滤伪触发（对白里的「第N章…说的」）与目录行（相邻标记间距过小）。
    """
    # 第一遍：剔除伪触发与副文本
    cand = []
    for m in CHAPTER.finditer(text):
        eol = text.find("\n", m.start())
        line = text[m.start():eol] if eol > 0 else text[m.start():]
        if len(line) > 100:                       # 标题行不可能这么长
            continue
        # F2a 伪触发：行内出现句读或右引号 → 是正文引用而非标题
        # F2a'（v3 修正，2026-09-06 回归复现）：排除集不得含全角冒号「：」——
        #   「第一章：重生」是常见标题格式，含冒号会把真实标题整行误杀，
        #   造成章节静默合并、字数分布失真。
        if re.match(r"^[ \t\u3000]*第", line) and re.search(r"[，。！？；”』]", line):
            continue
        if PARATEXT.search(line):
            continue
        cand.append(m)

    # 第二遍：目录行过滤。必须在剔除伪触发之后做 —— 否则被剔除的伪触发仍会
    # 占据序列位置，抢走上一章的间距配额，导致真实章节被误删。
    # F2b'（v3 修正）：末候选不能用 len(text) 当「下一标记」间距 —— 否则篇幅
    #   < min_gap 的短尾章（尾声/后记/终章）会被误判为目录行而吞并。
    #   目录行只会连片出现在正文之前：末候选仅当它紧跟前一候选（同属目录块）
    #   时才滤除；正文末章到前一章的距离 = 前章篇幅，与 min_gap 无关。
    kept = []
    for i, m in enumerate(cand):
        # 只查"下一个"就够了：目录最后一行的下一标记就是正文首单元，
        #      二者距离同样很小，故整个目录块连同其尾行会被逐条滤掉；
        #      反过来若同时查"上一个"，会把紧跟目录的正文首单元一起误删。
        if i + 1 < len(cand):
            nxt = cand[i + 1].start()
            if nxt - m.start() < min_gap:
                continue
        else:
            prev = cand[i - 1].start() if i > 0 else -min_gap
            if m.start() - prev < min_gap:
                continue
        kept.append(m.start())

    if len(kept) >= 3:
        spans = []
        for i, s in enumerate(kept):
            e = kept[i + 1] if i + 1 < len(kept) else len(text)
            body = strip_separators(text[s:e])
            if cn_len(body) < 40:
                continue
            spans.append(body)
        return spans, f"chapter(识别到 {len(spans)} 个单元)"

    blocks = [b.strip() for b in BLANK.split(text) if len(b.strip()) > 200]
    return blocks, "block(未识别到章节标题，按空行切块)"


def split_paragraphs(text: str):
    return [p.strip() for p in text.split("\n") if p.strip()]


def split_sentences(text: str):
    return [s for s in SENT_END.split(text) if s and s.strip()]


def cn_len(s: str) -> int:
    return len(re.findall(rf"[{CN}]", s))


# ---------------------------------------------------------------- 统计

def _stats(values):
    if not values:
        return {}
    vs = sorted(values)
    n = len(vs)

    def pct(p):
        return vs[min(n - 1, int(n * p))]

    return {
        "n": n,
        "mean": round(statistics.fmean(vs), 1),
        "median": pct(0.50),
        "p10": pct(0.10),
        "p90": pct(0.90),
        "max": vs[-1],
    }


def measure(text: str, min_gap: int = 300) -> dict:
    units, unit_kind = split_chapters(text, min_gap)
    all_text = text

    total_cn = cn_len(all_text)
    if total_cn == 0:
        raise SystemExit("未提取到中文字符，检查文件编码或是否为扫描版 PDF。")

    # 章节 / 块
    unit_words = [cn_len(u) for u in units]
    curve = []
    bucket = max(1, len(unit_words) // 20)
    for i in range(0, len(unit_words), bucket):
        chunk = unit_words[i:i + bucket]
        curve.append(round(statistics.fmean(chunk)))

    # 段落
    paras = split_paragraphs(all_text)
    para_words = [cn_len(p) for p in paras]
    short_para = sum(1 for w in para_words if 0 < w <= 20)

    # 句子（限抽样，长篇全量太慢）
    sample_units = units if len(units) <= 400 else units[:200] + units[-200:]
    sents = []
    for u in sample_units:
        sents.extend(split_sentences(u))
    sent_words = [cn_len(s) for s in sents]

    # F3：对白与叙述者评论分开计数
    dia_blocks = DIA_FULL.findall(all_text) + DIA_HALF.findall(all_text)
    quoted_chars = sum(cn_len(m) for m in dia_blocks)
    narr_blocks = NARR_COMMENT.findall(all_text)
    narr_chars = sum(cn_len(m) for m in narr_blocks)

    # 标点密度（每千字）
    per1k = lambda c: round(all_text.count(c) / total_cn * 1000, 2)
    punct = {name: per1k(ch) for ch, name in PUNCT_WATCH.items()}

    return {
        "unit_kind": unit_kind,
        "total_chars_cn": total_cn,
        "units": {
            "count": len(units),
            "words": _stats(unit_words),
            "words_curve_20pt": curve,
        },
        "paragraph": {
            **_stats(para_words),
            "short_paragraph_ratio": round(short_para / max(1, len(para_words)), 3),
        },
        "sentence": {
            **_stats(sent_words),
            "short_ratio_le10": round(
                sum(1 for w in sent_words if w <= 10) / max(1, len(sent_words)), 3
            ),
            "long_ratio_ge40": round(
                sum(1 for w in sent_words if w >= 40) / max(1, len(sent_words)), 3
            ),
        },
        "dialogue": {
            "char_ratio": round(quoted_chars / total_cn, 3),
            "blocks_per_1k_chars": round(len(dia_blocks) / total_cn * 1000, 2),
        },
        "narrator_comment": {
            "blocks": len(narr_blocks),
            "char_ratio": round(narr_chars / total_cn, 5),
            "note": "『』块单列，不计入对话；用于 voice.narrator_comment_ratio",
        },
        "punct_per_1k": punct,
        "_sampled_sentences_from": len(sample_units),
    }


def summarize(m: dict) -> str:
    L = []
    a = L.append
    a(f"切分方式      : {m['unit_kind']}")
    a(f"中文字数      : {m['total_chars_cn']:,}")
    u = m["units"]
    a(
        f"单元数/字数   : {u['count']} 个 | 中位 {u['words'].get('median')} "
        f"| p10 {u['words'].get('p10')} | p90 {u['words'].get('p90')}"
    )
    a(f"篇幅曲线(20点): {u['words_curve_20pt']}")
    p = m["paragraph"]
    a(
        f"段落          : 中位 {p.get('median')} 字 | p90 {p.get('p90')} "
        f"| 短段(<=20字)占比 {p.get('short_paragraph_ratio')}"
    )
    s = m["sentence"]
    a(
        f"句长          : 中位 {s.get('median')} | 均值 {s.get('mean')} "
        f"| p90 {s.get('p90')} | 短句<=10 占 {s.get('short_ratio_le10')} "
        f"| 长句>=40 占 {s.get('long_ratio_ge40')}"
    )
    d = m["dialogue"]
    a(
        f"对话          : 字符占比 {d['char_ratio']} "
        f"| 每千字 {d['blocks_per_1k_chars']} 个对话块"
    )
    n = m["narrator_comment"]
    a(f"叙述者评论(『』): {n['blocks']} 块 | 字符占比 {n['char_ratio']}")
    a(f"标点密度(每千字): {m['punct_per_1k']}")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="小说叙事风格量化统计")
    ap.add_argument("file", help="txt 或 epub 路径")
    ap.add_argument("-o", "--out", help="JSON 输出路径")
    ap.add_argument(
        "--min-gap",
        type=int,
        default=300,
        help="相邻章节标记的最小字距，小于此值判为目录行（默认 300）",
    )
    args = ap.parse_args()

    path = Path(args.file)
    if not path.exists():
        raise SystemExit(f"文件不存在: {path}")

    text = load_text(path)
    m = measure(text, args.min_gap)
    print(summarize(m))

    out = Path(args.out) if args.out else path.with_suffix(".metrics.json")
    out.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {out}")


if __name__ == "__main__":
    main()
