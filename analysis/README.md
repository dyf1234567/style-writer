# analysis/ — 风格识别（原 novel-style-kit）

写作**前**的一次性流程：把一部参考作品读成一张 `style-card.yaml`（只记方法、不记内容）。
识别侧的产物从这里出去；运行时检索与防抄在 `../scripts/style_engine.py`。

若用户要求从语料一直完成作者包和索引，使用 [新作者初始化](../references/new-author.md)
衔接本流程与构建、检索验收。只请求分析时，不自动建库。

## 文件

| 文件 | 作用 |
|---|---|
| `analysis-report.template.md` | 11 节分析报告模板（过程文档，允许出现情节做论证）；v2 起含 8 情感书写、9 回报节律 |
| `style-card.template.yaml` | 最终产物特征卡模板（[M]/[R]/[I] 三类来源标注 + 红线自检）；v2 起 9 节，新增 `emotion_writing`、`reward_rhythm`，`syntax.lexical_fingerprint` 与 `imagery.simile_markers_per_1k` 接词层量化 |
| `scripts/measure.py` | 量化统计（txt/epub，纯标准库）：章/段/句分布、对话与叙述者评论占比、标点密度；v4 起新增 `lexeme` 词层段——口癖 top-N、情绪词密度、明喻标记密度（封闭词表，零原文输出）；v5 起对白覆盖 “” / "" / 「」 三种排版，并输出 `source`（epub spine 命中情况）与 `warnings`（可能让数字失真的事实）。**只输出统计量，绝不输出原文片段** |
| `tests/test_chapter_split.py` | 分章逻辑回归测试（F2a' 冒号误杀、F2b' 短尾章吞并等历史 bug 的固化用例） |
| `tests/test_lexeme_stats.py` | 词层指标回归测试（长词吸收、频次口径、红线：输出仅含词表词与数字） |
| `tests/test_loader_and_dialogue.py` | 载入与对白口径回归测试（F4 BOM/CRLF、F5 「」与实体引号与长对白、F6 spine 丢页），含干净文档不产生告警的阴性对照 |

## 标准流程

```powershell
# 1. 量化（[M] 字段的数据源；含 v4 词层 lexeme 段；JSON 不入库，报告引用其路径即可）
#    控制台末尾会打印 warnings；不清零就不要往下抄数
python analysis/scripts/measure.py <作品.txt|作品.epub> -o metrics.json

# 2. 复制两个模板到受控工作目录，按报告模板逐维度精读填写（[R]/[I] 项标注置信度）
#    v2 起需额外填 8 情感书写、9 回报节律；emotion_words_per_1k / simile_markers_per_1k
#    / lexical_fingerprint 直接取 metrics.json 的 lexeme 段

# 3. 按报告第 10 节的翻译规则收敛成 style-card.yaml，跑文件末尾红线自检

# 4. 桥接为运行时风格包（见仓库根 README「从卡到包」）
python scripts/style_engine.py import-pack --card style-card.yaml --author <slug>
```

## 口径备忘

- 数值一律用**中位数 + p90**（长篇均值会被个别长章带偏）。
- 分章与载入口径的历次修正记录在 `scripts/measure.py` 文件头（v2 的 F1/F2/F3、v3 的 F2a'/F2b'、v5 的 F4/F5/F6）；改分章逻辑必须先过 `tests/`。
- **`warnings` 非空就不要把数字抄进特征卡**：v5 的三类告警（spine 丢页、整篇匹配不到对白块、退化到 block 切分）都属「0 是测不出来而不是没有」，而 0 值会被 `import-pack` 当作模板未填直接丢弃。
- 词层指标（v4 的 `lexeme` 段）基于三个封闭词表计数，只输出通用词与频次，天然不含专名与原句；扩充词表须同步 `tests/test_lexeme_stats.py` 的红线用例。
- 卡里出现任何人名/地名/原句/情节 = 作废，改写字段或删除；`meta.target_work` 仅内部标注，禁止进入生成提示词。
