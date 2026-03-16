# Changelog

本文件记录 Knowledge Graph Vault 的重要变更。格式基于 [Keep a Changelog](https://keepachangelog.com/)。

## [0.2.1] - 2026-03-15

### Added

- **测试骨架** — 从零建立 pytest 测试套件（53 个测试，1s 全量通过）
  - `tests/conftest.py` — 共享 fixtures（tmp_vault、vector_store、graph_store、sample_entry）
  - `tests/test_utils.py` — 8 个测试覆盖 slugify / generate_id / get_entry_dir / compute_content_hash / load_entries
  - `tests/test_vector_store.py` — 8 个测试覆盖 Qdrant 集合管理、upsert/search、payload scroll、delete
  - `tests/test_graph_store.py` — 12 个测试覆盖 _parse_relations 纯函数、Entry CRUD、关系操作、sync_partial
  - `tests/test_sync_integration.py` — 4 个端到端测试覆盖全量同步、增量无变更/变更检测/删除检测
  - `tests/test_cli.py` — 5 个 CLI 冒烟测试（CliRunner）

### Removed

- `knowledge-graph/knowledge-graph/` 嵌套子目录（旧版 git clone 副本）
  - 唯一文件已迁移至顶层：`sc/*.md`（5 个）、`README.md`

## [0.2.0] - 2026-03-15

### Added

- **增量同步** — `kg sync` 默认只处理新增、修改、删除的条目，跳过未变更条目
  - 基于 SHA-256 content hash 的变更检测（对比磁盘文件 hash 与 Qdrant payload 中存储的 hash）
  - 首次同步或旧格式索引自动回退全量重建
  - 无变更时输出"所有条目均为最新"并跳过 embed
- `kg sync --full` flag，强制全量重建（保留原有 drop + recreate 行为）
- `agents/utils.py` — `compute_content_hash()` 工具函数
- `agents/vector_store.py` — 三个新方法：
  - `ensure_collection()` — 非破坏性创建集合（已存在则跳过）
  - `get_all_payloads()` — scroll 全部 payload（不加载 vector），用于 hash 对比
  - `delete_points(entry_ids)` — 按 entry_id 批量删除 point
- `agents/graph_store.py` — 三个新方法：
  - `delete_entry()` — 删除单个节点
  - `delete_entry_edges()` — 删除节点所有出入边
  - `sync_partial()` — 增量图同步，只处理变更/删除条目及其边

### Changed

- `upsert_entries()` payload 新增 `content_hash` 字段
- `kg sync` 命令拆分为 `_full_sync()` 和 `_incremental_sync()` 两条路径
- 增量同步摘要表增加 new / changed / deleted / unchanged 分类统计

### Backward Compatibility

| 场景 | 行为 |
|------|------|
| 首次 sync（无 collection） | 自动走全量 |
| 已有索引但缺 `content_hash`（旧格式） | 警告 + 自动全量重建 |
| `--full` | 保留原来的 drop + recreate |
| 无变更 | 输出"所有条目均为最新"并退出 |
| `kg query` / `kg link` / `kg graph` | 不受影响 |

### Verified (126 entries, 406 edges)

| # | 场景 | 结果 |
|---|------|------|
| 1 | 首次 sync（clean indexes） | 全量写入 126 条目 + 406 边 |
| 2 | 立即再跑 sync | "所有 126 个条目均为最新，无需操作" |
| 3 | 编辑 1 个 .md → sync | 修改 1，未变 125，只 embed 1 条 |
| 4 | 删除 1 个 .md → sync | 删除 1（Qdrant + SurrealDB），未变 125 |
| 5 | `kg sync --full` | 全量重建 126 条目 |
| 6 | `kg query` / `kg link` / `kg graph` | 功能正常，不受增量逻辑影响 |

## [0.1.0] - 2026-03-10

初始版本。

- `kg ingest` — 从对话/文档提取知识条目
- `kg sync` — 全量同步 Qdrant + SurrealDB
- `kg query` — 语义检索知识库
- `kg review` — 知识库健康审查
- `kg stats` — 统计信息
- `kg link` — 潜在链接发现
- `kg quiz` — 间隔重复复习
- `kg radar` — 知识域强度雷达
- `kg export` — 导出格式化文档
- `kg history` — 条目演进追踪
- `kg graph` — 图关系探索
