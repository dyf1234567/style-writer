# 新作者初始化：从作品到可检索作者包

用于新增作者包并建库，或为已有作者包首次建库。沿用现有命令，不新增 CLI 子命令。
只请求分析、静态包或检索时，不自行扩展任务。示例命令从 Skill 目录运行，使用宿主已验证可用的 Python。

## 1. 确定输入、路径与冲突

- 确定作者标识（`[a-z0-9][a-z0-9_-]*`）、语料目录、作者包根目录、索引根目录。
- 路径优先沿用用户显式指定值，其次使用现有配置：`AUTHOR_STYLE_HOME`、`STYLE_INDEX_HOME`；未配置时分别为用户目录下 `.style-writer/authors`、`.style-writer/indexes`。语料来自显式目录或包内 `corpus.env` 指定的环境变量。使用前解析为绝对路径并报告实际位置。
- 检查 `<作者包根目录>/<作者标识>/pack.json` 和 `<索引根目录>/<作者标识>.sqlite3`。新建请求遇到同名文件，先说明并询问复用、改名还是更新；不要默认加 `--force` 或运行会替换索引的 `build`。若用户已明确要求重建该目标，则无需重复确认。
- 已有作者包的建库请求：验证包的 schema、slug 和配置，直接进入依赖检查，不重新分析或改写风格卡。
- 语料、分析产物和索引放在 Skill 与公开仓库之外。语料支持 `.txt`、`.md`、`.text`；`build` 接受目录，不接受单个文件。分析脚本另支持 EPUB，但检索构建不支持 EPUB；只有 EPUB 时先说明需要转换，保留原件。
- 路径缺失、多个目录难以判断或同名目标冲突时才询问，不把示例路径当作本机配置。

## 2. 分析、创建卡片并导入

先读 [分析流程](../analysis/README.md) 和 [作者包约定](pack-contract.md)。
复制模板到外部分析目录，结合量化与精读填写；报告实际阅读的作品、年代、章节范围和判断置信度。
不要把抽样分析写成全量精读，也不要将少量作品的特征概括为作者所有作品的统一风格。
多年代、多题材语料先区分子集；差异明显时分开分析或建立多个作者包，不能仅靠检索分组修正已混合的风格卡。

将抽象方法卡通过桥接命令导入，不手写一份绕过风格卡的派生特征：

```powershell
python scripts/style_engine.py import-pack --author <slug> --card "<分析目录>/style-card.yaml" --authors-root "<作者包根目录>" --corpus-root "<语料目录>" --corpus-env <作者专用环境变量名>
```

命令中的占位符须替换后执行。`--corpus-env` 只记录变量名称，不会设置系统环境变量；后续可继续显式传入语料目录，不必修改全局配置。
检查导入结果的 `warnings` 与 `redline.source_overlap`：`skipped` 表示原文检查未完成，不能报告为已通过；有可用语料时查明原因并重试，仍无法检查则明确交付限制。

新导入包默认无作品分组。根据真实语料路径配置 `families`、`default_family`、`unmatched_family` 和 `exclude_patterns`，或单一语料保留默认 `other`。
抽象风格仍由卡片维护；只在 `pack.json` 中维护这些运行配置。不编造目录匹配规则，也不借用其他作者的分组。

## 3. 检查依赖和构建成本

- 确认 Python 可执行，SQLite 支持 FTS5；当前构建没有 FTS5 缺失时的自动降级。
- 混合索引：检查所选 Ollama 端点可达、模型已安装。端点沿用显式值或 `STYLE_VECTOR_OLLAMA_URL`，默认本机 `http://127.0.0.1:11434`。调用远程端点会发送语料片段，不能把本地任务悄悄改成远程处理。
- 服务或模型缺失时说明缺项；需要安装、下载或改变端点而不在既有授权内时，先请求许可。不要擅自以 FTS5 替代用户指定的混合索引；用户选纯词法模式才用 `--provider none`。
- 说明这是全量重建：会扫描语料并计算向量，没有内置缓存复用或断点续建。大语料需要时间、磁盘和本机算力。资源紧张时可降低 `--batch-size`，但不保证消除超时。

## 4. 构建独立索引

确认目标没有冲突，或已有明确重建授权后执行：

```powershell
python scripts/style_engine.py build --author <slug> --authors-root "<作者包根目录>" --corpus-root "<语料目录>" --index-root "<索引根目录>" --provider ollama --model bge-m3 --endpoint http://127.0.0.1:11434 --batch-size 4
```

以上模型、端点和批量大小是本地小显存场景示例，遵循用户实际选择。
仅在完整成功后，新 `.building` 数据库才替换旧索引。不要同时对同一目标运行多个 build。
失败时检查具体报错；超时可降低批量大小后有限重试，不能反复无条件重跑。相同原因再次失败时报告未完成及所需操作，不把旧索引的存在当作新构建成功。

## 5. 检索验收与交付

```powershell
python scripts/style_engine.py status --author <slug> --authors-root "<作者包根目录>" --index-root "<索引根目录>"
python scripts/style_engine.py prepare --author <slug> --authors-root "<作者包根目录>" --index-root "<索引根目录>" --family <包内实际分组ID> --query "<与该语料相关的场景描述>" --endpoint http://127.0.0.1:11434
```

若使用默认分组可省略 `--family`。使用实际相关的场景查询，不能通过切换到不相关分组掩盖空命中。
默认不加 `--include-excerpts`，只返回风格特征、统计和来源元数据。

验收需同时满足：

- 本次 `build` 成功且 `passages > 0`；不能只看命令退出码。
- `prepare.ok` 为真、有候选证据且 `writing_context.retrieval_metrics.sample_count > 0`。
- 要求混合索引时，实际 `mode` 为 `hybrid` 且 `vector_error` 为空。`status` 的模式仅反映索引配置，不证明服务当前可用。
- 用户选择纯词法时，按 FTS5 验收；`static`、`no-match`、`empty-scope` 及向量降级要分别报告，不能称为混合检索成功。检索命中仅证明检索链路可用，不证明风格分析准确。

交付作者标识、包与索引绝对路径、语料范围、片段数、模型/维度、实际检索模式、未完成的检查和警告。
同一作者索引供不同小说项目复用；项目人物、正文、时间线和伏笔不自动加入作者语料库。
