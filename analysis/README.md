# analysis/ — 风格识别（原 novel-style-kit）

写作**前**的一次性流程：把一部参考作品读成一张 `style-card.yaml`（只记方法、不记内容）。
识别侧的产物从这里出去；运行时检索与防抄在 `../scripts/style_engine.py`。

## 文件

| 文件 | 作用 |
|---|---|
| `analysis-report.template.md` | 9 维度分析报告模板（过程文档，允许出现情节做论证） |
| `style-card.template.yaml` | 最终产物特征卡模板（[M]/[R]/[I] 三类来源标注 + 红线自检） |
| `scripts/measure.py` | 量化统计（txt/epub，纯标准库）：章/段/句分布、对话与叙述者评论占比、标点密度。**只输出统计量，绝不输出原文片段** |
| `tests/test_chapter_split.py` | 分章逻辑回归测试（F2a' 冒号误杀、F2b' 短尾章吞并等历史 bug 的固化用例） |

## 标准流程

```powershell
# 1. 量化（[M] 字段的数据源；JSON 不入库，报告引用其路径即可）
python analysis/scripts/measure.py <作品.txt|作品.epub> -o metrics.json

# 2. 复制两个模板到受控工作目录，按报告模板逐维度精读填写（[R]/[I] 项标注置信度）

# 3. 按报告第 8 节的翻译规则收敛成 style-card.yaml，跑文件末尾红线自检

# 4. 桥接为运行时风格包（见仓库根 README「从卡到包」）
python scripts/style_engine.py import-pack --card style-card.yaml --author <slug>
```

## 口径备忘

- 数值一律用**中位数 + p90**（长篇均值会被个别长章带偏）。
- 分章正则的历次修正记录在 `scripts/measure.py` 文件头（v2 的 F1/F2/F3、v3 的 F2a'/F2b'）；改分章逻辑必须先过 `tests/`。
- 卡里出现任何人名/地名/原句/情节 = 作废，改写字段或删除；`meta.target_work` 仅内部标注，禁止进入生成提示词。
