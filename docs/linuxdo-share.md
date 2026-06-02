# Linux.do 分享稿：我做了一个 Markdown-first 的个人知识图谱工具

大家好，最近在整理自己的学习/项目笔记，顺手把一个个人知识管理工具开源出来：**Knowledge Graph CLI**。

它的核心思路很简单：**Markdown 是唯一真相源，数据库只是可重建的索引。**
也就是说，知识正文仍然保存在本地 Markdown / Obsidian Vault 里；Weaviate、SurrealDB 这类数据库只负责加速检索和关系发现，删掉后可以随时从 Markdown 重新 sync。

## 它解决什么问题？

我之前遇到的主要问题是：

- 技术笔记越写越多，但后来很难找回真正相关的内容；
- 单纯关键词搜索不够，很多问题需要语义检索；
- 知识点之间的依赖、演进、相似关系没有被显式建模；
- 看过的内容容易遗忘，需要一个复习节奏；
- 和 AI 讨论出来的知识，如果不沉淀，很快就丢了。

这个项目就是把这些事情串成一个本地工作流。

## 核心功能

### 1. Markdown / Obsidian 作为知识源

所有知识条目都是普通 Markdown 文件，带 YAML frontmatter：

- 人类可读；
- Git 可版本化；
- 不绑定任何数据库；
- 可以继续用 Obsidian 编辑。

数据库索引都是 derived data，可以重建。

### 2. Weaviate 语义检索

支持自然语言查询，不只是关键词匹配。

例如你问：

```bash
kg query "goroutine 为什么会泄漏"
```

它可以召回和 GMP 调度、channel 阻塞、生命周期管理相关的条目。

当前向量层使用 **Weaviate Embedded**，支持原生 hybrid search（BM25 + vector）。

### 3. SurrealDB 图关系

除了向量相似度，我还把条目之间的关系建成图：

- prerequisites：前置知识；
- references：引用；
- supersedes：新旧版本演进；
- related：相似/相关条目。

可以做 N 跳遍历、路径发现、知识演进链追踪。

### 4. AI 辅助知识提取

可以从文档、对话、网页内容中提取结构化知识条目：

```bash
kg ingest notes.md --dry-run
```

它会把非结构化内容整理成带 frontmatter 的 Markdown 条目，并尽量归类到合适的知识域和条目类型。

### 5. 间隔重复复习

内置一个简化版 SM-2 复习机制：

```bash
kg quiz --domain golang --count 5
```

系统会根据置信度、过期时间、条目年龄挑选需要复习的知识点。

### 6. 知识健康检查

可以检查哪些知识过期、薄弱、还是 draft：

```bash
kg review --domain cloud-native
kg radar
```

我把它理解成“知识库自己告诉你哪里还不够好”。

### 7. Web UI

除了 CLI，也有一个 React + FastAPI 的本地 Web UI，支持：

- Dashboard；
- 语义搜索 / RAG 问答；
- 图谱浏览；
- RSS 摄取；
- 题库 / 面试题管理；
- 知识健康视图。

开发模式：

```bash
kg serve --dev
```

## 技术栈

- Python / Click / Rich：CLI；
- FastAPI：后端 API；
- React / Vite / Tailwind：Web UI；
- Weaviate Embedded：向量与 hybrid search；
- SurrealDB：图关系；
- Markdown + YAML frontmatter：唯一知识源；
- Claude / OpenAI-compatible API：知识提取与问答；
- sentence-transformers / Gemini Embedding：可选 embedding 后端。

## 快速开始

```bash
git clone <repo-url>
cd knowledge-graph

uv sync
cp config.yaml.example config.yaml

# 初始化目录
uv run kg init

# 同步索引
uv run kg sync

# 查询
uv run kg query "Raft 选举为什么需要多数派"
```

如果要使用 AI 提取能力，需要配置自己的 API key；如果只是本地检索和浏览，可以先从本地 embedding / 已有 Markdown 开始。

## 适合谁？

我觉得它适合这几类人：

- 长期写技术笔记的人；
- 想把 Obsidian + AI + RAG 串起来的人；
- 想把学习记录变成可检索、可复习、可导出的知识资产的人；
- 想研究“个人知识库如何工程化”的同学。

## 项目特点

我最在意的几个原则：

1. **Markdown-first**：数据库永远不是最终事实；
2. **Local-first**：索引、图谱、Web UI 都可以本地跑；
3. **可重建**：Weaviate / SurrealDB 都可以从 Markdown 重新生成；
4. **AI 只是辅助**：AI 负责降低整理成本，不替代知识结构本身；
5. **复习闭环**：不是只收藏，还要能回忆和巩固。

## 当前状态

项目还比较偏个人工作流，代码里还有一些历史模块和文档债，但核心链路已经可用：

```text
Markdown → kg sync → Weaviate + SurrealDB → query / graph / quiz / review / export
```

欢迎感兴趣的朋友试用、提建议，也欢迎交流个人知识库/RAG/Obsidian 工程化相关的想法。
