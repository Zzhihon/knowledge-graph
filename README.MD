# Knowledge Graph CLI

个人知识图谱管理系统 -- 基于 Obsidian + 向量/图双引擎 + AI 辅助的结构化知识管理工具。

## 设计目标

1. **Markdown 为唯一信源**: 所有知识以 Obsidian markdown + YAML frontmatter 存储，数据库索引可随时从源文件重建，不被任何存储引擎绑定
2. **语义检索而非关键词匹配**: 通过向量嵌入实现自然语言查询，问"goroutine 为什么会泄漏"能找到相关的 GMP 调度、channel 阻塞等条目
3. **图关系驱动知识关联**: 条目间的 references / prerequisites / supersedes 关系在图数据库中建模，支持 N 跳遍历和路径发现
4. **AI 辅助提取而非手动录入**: 用 Claude API 从对话记录、技术文档中自动提取结构化知识条目，降低知识沉淀的摩擦
5. **间隔重复对抗遗忘**: 内置 SM-2 变体的间隔重复引擎，根据置信度和复习周期主动推送待复习条目
6. **与 Claude Code 深度集成**: 通过 slash commands 和 hooks 实现会话自动加载上下文、写入后自动发现链接

## 功能

### 核心命令

| 命令 | 功能 | 示例 |
|------|------|------|
| `kg ingest <file>` | 从文档/对话中提取知识条目 | `kg ingest notes.md --dry-run` |
| `kg query <question>` | 语义检索知识库 | `kg query "Raft 选举机制"` |
| `kg sync` | 全量重建向量索引 + 图数据库 | `kg sync` |
| `kg link` | 发现条目间潜在关联 | `kg link --top-n 10 --threshold 0.75` |
| `kg graph <entry_id>` | 探索条目的图关系网络 | `kg graph ke-20260226-xxx --depth 3` |

### 复习与评估

| 命令 | 功能 | 示例 |
|------|------|------|
| `kg quiz` | 间隔重复复习 | `kg quiz --domain golang --count 5` |
| `kg review` | 知识库健康检查（过期/低置信度/草稿） | `kg review --domain ai-agent --report` |
| `kg radar` | 知识域强度雷达（覆盖/深度/新鲜/置信） | `kg radar --domain cloud-native` |

### 导出与统计

| 命令 | 功能 | 示例 |
|------|------|------|
| `kg export` | 导出为博客/学习指南/入职文档 | `kg export --format blog --domain golang` |
| `kg history <id>` | 追踪条目 supersedes 演进链 | `kg history ke-20260226-xxx` |
| `kg stats` | 知识库分布统计 | `kg stats` |
| `kg init` | 初始化/验证目录结构 | `kg init` |

### Claude Code 集成

- `/sc:kg-load <topic>` -- 会话开始时自动加载相关知识上下文
- `/sc:kg-ingest <file>` -- 在 Claude Code 中直接提取知识
- `/sc:kg-quiz` -- 交互式间隔重复
- `/sc:kg-review` -- 知识库健康审查
- `/sc:kg-export` -- 导出格式化文档

自动化 Hooks:
- **UserPromptSubmit**: 每次会话首条消息自动搜索知识库，推送相关上下文
- **PostToolUse**: 写入/编辑条目后自动运行链接发现

## 技术选型

### 存储层

| 组件 | 选型 | 职责 |
|------|------|------|
| 知识源文件 | **Obsidian Vault** (Markdown + YAML frontmatter) | 唯一信源，人类可读可编辑 |
| 向量数据库 | **Qdrant** (本地磁盘模式) | 语义检索，余弦相似度匹配，支持元数据过滤 |
| 图数据库 | **SurrealDB** (SurrealKV 本地存储) | 条目关系建模，BFS 路径查找，N 跳邻居遍历 |

### AI 与嵌入

| 组件 | 选型 | 说明 |
|------|------|------|
| 知识提取 | **Claude API** (claude-sonnet-4-20250514) | 从非结构化文本提取结构化知识条目 |
| 文本嵌入 | **sentence-transformers** (all-MiniLM-L6-v2) | 384 维向量，本地离线推理，无需 GPU |

### 应用层

| 组件 | 选型 | 说明 |
|------|------|------|
| CLI 框架 | **Click** | 声明式命令定义，参数校验，帮助文档生成 |
| 终端输出 | **Rich** | 彩色表格、面板、进度条等结构化终端渲染 |
| 配置管理 | **PyYAML** + frozen dataclass | 类型安全的 YAML 配置加载 |
| Frontmatter 解析 | **python-frontmatter** | Markdown YAML 头部读写 |
| 构建系统 | **Hatchling** | PEP 517 构建后端 |

### 链接发现算法

采用向量 + 图的混合评分策略：

```
final_score = vector_similarity * 0.6 + graph_proximity * 0.4
```

- 向量相似度 (60%): Qdrant 余弦距离，捕捉语义相关性
- 图邻近度 (40%): SurrealDB 2 跳可达性，捕捉结构关联

### 间隔重复算法

基于 SM-2 变体的优先级评分：

```
priority = (days_overdue * 2) + (age_days * 0.5) + ((1 - confidence) * 100)
```

复习后根据用户反馈调整置信度和下次复习间隔。

### 知识域强度评分

四维加权评分：

```
strength = coverage * 0.3 + depth * 0.3 + freshness * 0.2 + confidence * 0.2
```

## 项目结构

```
.
├── agents/                  # Python 核心包
│   ├── cli.py              # Click CLI 入口 (12 个命令)
│   ├── config.py           # YAML 配置加载器
│   ├── embeddings.py       # sentence-transformers 嵌入封装
│   ├── vector_store.py     # Qdrant 向量库封装
│   ├── graph_store.py      # SurrealDB 图库封装
│   ├── ingest.py           # Claude API 知识提取
│   ├── query.py            # 语义检索引擎
│   ├── link.py             # 混合链接发现
│   ├── review.py           # 健康检查与差距分析
│   ├── quiz.py             # 间隔重复引擎
│   ├── radar.py            # 知识域强度评分
│   ├── export_entries.py   # 文档导出 (博客/指南/入职)
│   └── history.py          # 条目演进链追踪
├── sc/                      # Claude Code slash commands
│   ├── kg-ingest.md        # /sc:kg-ingest 命令定义
│   ├── kg-load.md          # /sc:kg-load 命令定义
│   ├── kg-export.md        # /sc:kg-export 命令定义
│   ├── kg-quiz.md          # /sc:kg-quiz 命令定义
│   └── kg-review.md        # /sc:kg-review 命令定义
├── scripts/                 # 自动化脚本
│   ├── post-write-hook.sh  # PostToolUse hook: 写入后自动链接发现
│   ├── session-kg-load.sh  # UserPromptSubmit hook: 会话自动上下文加载
│   ├── review-reminder.py  # 每日复习提醒 (macOS 通知)
│   └── weekly-radar.py     # 每周知识域强度报告
├── config.yaml              # 项目配置 (知识域/条目类型/模型等)
└── pyproject.toml           # Python 包定义与依赖
```

`kg init` 运行后会自动创建 Obsidian Vault 所需的知识目录：

```
00-Index/        首页仪表盘 + 域 MOC 页
01-Principles/   原理深究条目
02-Patterns/     模式方案条目
03-Debug/        排障记录条目
04-Architecture/ 架构设计条目
05-Research/     研究探索条目
06-Team/         团队共享知识
07-Projects/     项目跟踪笔记
Templates/       Obsidian Templater 模板
indexes/         数据库本地存储 (Qdrant + SurrealDB)
```

## 架构

```
┌─────────────────────────────────────────────────────┐
│                  Obsidian Vault                      │
│         Markdown + YAML frontmatter (唯一信源)        │
└──────────────────────┬──────────────────────────────┘
                       │
                  kg sync (全量重建)
                       │
         ┌─────────────┼─────────────┐
         ▼                           ▼
┌─────────────────┐        ┌─────────────────┐
│     Qdrant      │        │   SurrealDB     │
│  向量嵌入 (384d) │        │  图关系 (边+节点) │
│  余弦相似度检索   │        │  BFS/N跳遍历     │
└────────┬────────┘        └────────┬────────┘
         │                          │
         │    ┌─────────────────┐   │
         └───►│   kg link       │◄──┘
              │ vector*0.6      │
              │ + graph*0.4     │
              └─────────────────┘
```

## 快速开始

### 环境要求

- Python >= 3.11
- `ANTHROPIC_API_KEY` 环境变量 (仅 `kg ingest` 需要)

### 安装

```bash
# 克隆项目后
cd knowledge-graph
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 初始化

```bash
# 验证/创建目录结构
kg init

# 构建向量索引 + 图数据库 (已有条目时)
kg sync
```

### 日常使用

```bash
# 从对话导出中提取知识
kg ingest conversation.md --dry-run   # 先预览
kg ingest conversation.md             # 确认后写入

# 同步索引
kg sync

# 语义检索
kg query "K8s Pod 调度策略"
kg query "Redis 集群分片" --domain databases --top-k 10

# 发现潜在链接
kg link --threshold 0.7

# 间隔重复复习
kg quiz --domain golang --count 3

# 查看知识域强度
kg radar

# 导出为博客
kg export --format blog --domain cloud-native --output blog.md
```

## 许可

MIT
