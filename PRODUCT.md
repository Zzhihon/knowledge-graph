# Knowledge Graph Vault — 个人知识图谱系统

> **Markdown 为源，AI 为引擎，间隔重复为节奏。**
> 一个为工程师打造的结构化知识管理与算法面试备战系统。

---

## 它解决什么问题？

你有没有这样的经历：

- 刷过 200 道 LeetCode，面试时看到变体题还是懵
- 笔记记了一大堆，需要时根本找不到
- 学了一个新技术点，三个月后忘得干干净净
- 团队知识散落在 Slack、Notion、脑子里，新人上手极慢

**Knowledge Graph Vault** 不是又一个笔记工具。它是一个 **三层知识架构 + AI 自动化 + 间隔重复** 的学习系统。

---

## 核心架构

```
┌─────────────────────────────────────────────────┐
│                  Claude Code CLI                 │
│         Hook 自动检测 · Slash 命令交互            │
├─────────────────────────────────────────────────┤
│                                                  │
│   ┌───────────┐  ┌───────────┐  ┌───────────┐   │
│   │ 01-原理层  │  │ 02-模式层  │  │ 08-实战层  │   │
│   │ Principles│→ │ Patterns  │→ │ Problems  │   │
│   │   为什么   │  │   怎么做   │  │   练起来   │   │
│   └───────────┘  └───────────┘  └───────────┘   │
│                                                  │
├──────────┬──────────┬───────────┬────────────────┤
│  Qdrant  │ SurrealDB│   SM-2    │   Claude API   │
│ 语义搜索  │ 关系图谱  │ 间隔重复   │  知识提取      │
└──────────┴──────────┴───────────┴────────────────┘
│                                                  │
│              Obsidian Markdown Vault             │
│            （所有数据的唯一真相源）                  │
└─────────────────────────────────────────────────┘
```

### 三层知识结构

| 层级 | 目录 | 作用 | 示例 |
|------|------|------|------|
| **原理层** | `01-Principles/` | 回答「为什么」 | 为什么滑动窗口是 O(n) 而不是 O(n²)？ |
| **模式层** | `02-Patterns/` | 回答「怎么做」 | 滑动窗口三种变体的代码模板（C++ / Go） |
| **实战层** | `08-Problems/` | 回答「用起来」 | LC-3 无重复字符最长子串完整解析 |

知识从原理流向模式，从模式流向实战。不是碎片化刷题，是 **体系化学习**。

---

## 六大能力

### 1. Hook 自动上下文注入

```
你说：「复习滑动窗口」
     ↓
Hook 自动识别算法学习意图
     ↓
Qdrant 语义检索匹配模式模板 + 5 道相关题
     ↓
Claude 读取模板 → 呈现结构化学习面板
     ↓
你选择：复习模板 / 做题 / 测验
```

不需要手动搜索，不需要记文件名。**说出意图，系统自动就位。**

### 2. 间隔重复测验（SM-2）

```bash
/sc:kg-quiz --domain algorithm --count 5
```

- 系统扫描所有 `review_date ≤ 今天` 或 `confidence < 0.6` 的条目
- 只展示问题和上下文，等你作答
- 根据回答更新复习间隔：
  - **记得** → confidence +0.05，30 天后再复习
  - **模糊** → confidence 不变，7 天后再复习
  - **忘了** → confidence -0.1，明天再复习
- 揭晓完整解析，展示关键洞察

不是死记硬背，是 **科学遗忘曲线管理**。

### 3. 混合检索引擎

```
最终得分 = 向量语义相似度 × 0.7 + BM25 关键词匹配 × 0.3
```

| 引擎 | 擅长 | 示例 |
|------|------|------|
| **Qdrant 向量搜索** | 语义相近的概念 | 搜"滑动窗口"也能找到"双指针"相关条目 |
| **SurrealDB 图搜索** | 依赖关系和前置知识 | 查"LC-76"自动关联到滑动窗口模式模板 |
| **BM25 关键词** | 精确术语匹配 | 搜"goroutine"不会返回"coroutine" |

三种检索互补，**不遗漏，不错配**。

### 4. 知识健康巡检

```bash
/sc:kg-review --domain algorithm --gaps
```

自动识别：
- 超过 180 天未更新的过时条目
- 置信度低于 0.6 的薄弱知识点
- 仍处于 draft 状态的未完成条目
- 子领域覆盖空白（比如还没有动态规划模式）

**你的知识库会告诉你它哪里不够好。**

### 5. 多格式导出

```bash
/sc:kg-export --format study-guide --domain algorithm
/sc:kg-export --format blog --domain golang
/sc:kg-export --format onboarding --domain cloud-native
```

同一份知识，三种输出：

| 格式 | 受众 | 特点 |
|------|------|------|
| **Study Guide** | 自己 | 按前置知识排序，含练习题和置信度 |
| **Blog Post** | 技术社区 | 叙事流：问题 → 分析 → 洞察 |
| **Onboarding Doc** | 新同事 | 团队上下文 → 核心原则 → 常见坑 → Checklist |

### 6. 对话知识提取

```bash
/sc:kg   # 从当前对话中捕获知识
/sc:kg-ingest exported-chat.md   # 从文档批量提取
```

Claude 自动从技术对话中识别有价值的知识点，生成结构化条目写入对应目录。**你的每次讨论都可以沉淀为资产。**

---

## 技术栈

| 组件 | 选型 | 理由 |
|------|------|------|
| **知识源** | Obsidian Markdown | 人类可读、Git 可控、永不锁定 |
| **向量数据库** | Qdrant (本地磁盘模式) | 语义搜索，零云端依赖 |
| **图数据库** | SurrealDB (SurrealKV) | 关系发现，本地运行 |
| **嵌入模型** | all-MiniLM-L6-v2 (384d) | 离线推理，无 API 调用 |
| **AI 引擎** | Claude API | 知识提取、导出生成 |
| **交互层** | Claude Code CLI | Hook + Slash 命令，零切换成本 |
| **间隔重复** | SM-2 算法 | 经典有效，参数可调 |

**核心原则：Markdown 是唯一真相源。** 数据库是可重建的索引，不是数据本体。你可以随时删除 Qdrant 和 SurrealDB，从 Markdown 文件完整重建。

---

## 当前规模

| 指标 | 数据 |
|------|------|
| 知识域 | 8+ (算法、Go、云原生、分布式系统...) |
| 算法模式模板 | 6 个（滑动窗口、双指针、二分查找、二叉树遍历、DFS、BFS） |
| 算法题解 | 30 道（每模式 5 道，Easy/Medium/Hard 混合） |
| 待建模式 | 5 个（动态规划、回溯、贪心、单调栈、堆） |
| 题解语言 | C++ + Go 双语 |
| 模板类型 | 6 种（知识条目、问题条目、MOC、复盘、团队、项目） |

---

## 工作流示例

### 场景一：面试前复习滑动窗口

```
你：复习滑动窗口

[系统自动]
→ Hook 检测意图 → Qdrant 语义搜索
→ 加载模式模板 + 5 道相关题

Claude：
┌─────────────────────────────────────────────┐
│ 📋 滑动窗口学习面板                           │
├─────┬────────┬────────┬──────────┬──────────┤
│ 题号 │ 难度    │ 置信度  │ 复习状态  │ 频率     │
├─────┼────────┼────────┼──────────┼──────────┤
│ LC-3 │ Medium │  0.80  │ ⚠️ 过期   │ High    │
│ LC-76│ Hard   │  0.65  │ ⚠️ 过期   │ High    │
│ LC-209│ Medium│  0.85  │ ✅ 正常   │ Medium  │
│ LC-438│ Medium│  0.70  │ ⏳ 待复习  │ Medium  │
│ LC-567│ Medium│  0.75  │ ✅ 正常   │ Medium  │
└─────┴────────┴────────┴──────────┴──────────┘

建议：先复习 LC-76（置信度最低），再做 LC-3 测验。
要开始测验还是先看模板？
```

### 场景二：从零学习新模式

```
你：学习动态规划

[系统自动]
→ Hook 检测「学习」意图 → 搜索 pattern type
→ 发现：动态规划模式模板尚未创建

Claude：
动态规划模式模板还未建立。要现在创建吗？
我会生成：
1. 模式模板（含 4 种 DP 变体 + C++/Go 代码模板）
2. 5 道经典题目（LC-70, 322, 300, 1143, 72）
3. 更新算法 MOC 索引

你：好的，开始

[Claude 创建 6 个文件，更新 MOC]
```

### 场景三：日常知识捕获

```
你：（和 Claude 讨论了 Go 的 GMP 调度模型）

你：/sc:kg

Claude：
从本次对话中识别到 2 个知识点：
1. 「Go GMP 调度模型：M:N 线程映射」→ 01-Principles/
2. 「Goroutine 栈增长策略：连续栈 vs 分段栈」→ 01-Principles/

已写入，置信度设为 0.7，7 天后首次复习。
```

---

## 设计哲学

1. **Markdown First** — 数据不被任何工具绑架。Obsidian 倒闭了，你的知识还在。
2. **三层递进** — 原理 → 模式 → 实战，知识有结构才能迁移。
3. **主动遗忘管理** — 不是「记了就完」，是「科学地忘，科学地复习」。
4. **零摩擦交互** — Hook 自动检测意图，不需要你记命令、找文件。
5. **本地优先** — 向量库、图库、嵌入模型全部本地运行，无云端依赖。
6. **可重建索引** — 数据库是索引不是真相，随时可从 Markdown 重建。

---

## 命令速查

命令分为两层：**Claude Code Slash 命令**（在对话中使用）和 **`kg` CLI 命令**（在终端中使用）。

### Claude Code Slash 命令

| 命令 | 作用 |
|------|------|
| `/sc:kg` | 从当前对话捕获知识条目，写入对应目录 |
| `/sc:kg-load <topic>` | 加载某话题相关知识作为上下文 |
| `/sc:kg-quiz [--domain <d>] [--count <n>]` | 启动间隔重复测验 |
| `/sc:kg-review [--domain <d>] [--gaps]` | 知识健康巡检 + 覆盖空白分析 |
| `/sc:kg-export --format <f> [--domain <d>]` | 导出为博客 / 学习指南 / 入职文档 |
| `/sc:kg-ingest <file>` | 从外部文档批量提取知识条目 |

---

### `kg` CLI 命令（终端）

#### 初始化与同步

```bash
# 初始化知识库目录结构
kg init

# 增量同步（只处理新增/修改/删除的条目）
kg sync

# 强制全量重建 Qdrant + SurrealDB 索引
kg sync --full
```

#### 检索与问答

```bash
# 语义搜索
kg query '滑动窗口最长子串'

# 按域/类型/深度过滤
kg query '并发模型' --domain golang --type pattern --top-k 10

# 输出 JSON（供脚本处理）
kg query '二分查找' --format json

# RAG 知识问答（检索 + Claude 生成综合回答）
kg ask '滑动窗口和双指针有什么区别？'
kg ask 'goroutine 泄漏怎么排查？' --domain golang --top-k 8

# 禁用图上下文增强
kg ask '问题' --no-graph
```

#### 复习与测验

```bash
# 展示待复习条目（按优先级排序）
kg quiz
kg quiz --domain algorithm --count 5

# 交互模式：展示答案 + 自评 + 自动更新 frontmatter
kg quiz -i
kg quiz --domain algorithm --count 3 -i
# 自评选项: confident / partial / forgot
```

#### 知识提取与导入

```bash
# 从对话导出文件提取知识条目
kg ingest exported-chat.md

# 预览模式（不实际写入）
kg ingest notes.md --dry-run
```

#### 审查与统计

```bash
# 知识库健康概览
kg review
kg review --domain algorithm

# 生成完整 Markdown 报告
kg review --report
kg review --domain golang --report --output report.md

# 知识库统计（域/类型/深度/状态分布）
kg stats

# 知识域强度雷达（覆盖率、深度、新鲜度、置信度）
kg radar
kg radar --domain algorithm
```

#### 导出

```bash
# 导出为技术博客
kg export --format blog --domain golang

# 导出为学习指南（按前置知识排序）
kg export --format guide --domain algorithm

# 导出为团队入职文档
kg export --format onboarding --domain cloud-native

# 指定输出文件
kg export --format guide --domain algorithm --output study-guide.md
```

#### 关系发现与演化追踪

```bash
# 发现条目间潜在关联链接
kg link
kg link --top-n 20 --threshold 0.8

# 查看条目演进历史（supersedes 链）
kg history ke-20260309-sliding-window-pattern-template

# 查看条目内容变更 diff
kg diff ke-20260309-sliding-window-pattern-template
kg diff ke-20260309-lc3-longest-substring --limit 5

# 仅显示变更统计
kg diff ke-20260309-lc3-longest-substring --stats
```

#### 通用选项

```bash
# 查看版本
kg --version

# 查看任意子命令帮助
kg query --help
kg quiz --help
kg export --help
```

---

### 常用工作流组合

```bash
# 新设备初次使用
kg init && kg sync

# 日常刷题前
kg quiz --domain algorithm --count 5 -i

# 添加新笔记后
kg sync                              # 增量更新索引
kg link                              # 发现新关联

# 面试前全面复习
kg radar --domain algorithm          # 查看薄弱项
kg review --domain algorithm --gaps  # 定位知识空白
kg quiz --domain algorithm --count 10 -i  # 集中复习

# 定期维护
kg review --report --output claudedocs/health-$(date +%Y%m).md
kg stats                             # 全库统计
```

---

*Built with Obsidian + Qdrant + SurrealDB + Claude Code*
