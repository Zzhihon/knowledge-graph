# 知识库的五个板块与条目格式

知识库只存和"我"有关的东西：我在做的项目、我学到的原理、我踩过的坑、我想试的新东西。Markdown 仍是唯一信源，索引随时可以重建。自动沉淀（`kg capture` + agent-autoloop，见 [auto-capture.md](auto-capture.md)）和手动写入都按这里的格式写。

| 板块 | 目录 | `type` | 一句话 |
|---|---|---|---|
| 项目 | `07-Projects/` | `project` | 每个项目一页：背景、当前状态、决策、按天的进展、阻塞 |
| 通识 | `01-Principles/` `02-Patterns/` `04-Architecture/` `05-Research/` | `principle` `pattern` `architecture` `research` | 换个项目也成立的知识 |
| 经验 | `03-Debug/` `10-Experience/` | `debug` `experience` | 排障记录；做法、流程、教训（能直接改成分享贴） |
| 实验 | `11-Experiments/` | `experiment` | 想试的新技术、假设和试验结论，带状态 |
| 面试 | `08-Problems/` `09-Interview/` | `problem` `interview` | 保持原样，不进日报 |

日报写在 `00-Index/Daily/YYYY-MM-DD.md`。

## 共同的 frontmatter

```yaml
id: ke-20261105-goroutine-leak-on-unread-channel   # 前缀见下表，日期 + 英文短名
title: 未读 channel 导致 goroutine 泄漏
type: debug
domain: [golang]            # 列表，取 config.yaml 的 domains
tags: [goroutine, channel]
status: draft               # 各板块的状态见下文
scope: personal
source:
  type: agent-session       # agent-session | article | documentation | discussion | experience
  date: "2026-11-05"
  context: 一句话交代来源场景
  sessions: ["claude:7f3a9c1e", "codex:01a1629c"]   # 自动沉淀时必填，可回查原会话
related: ["[[ke-20260917-xxx]]"]   # 图谱只读 related，不要写 related_topics
supersedes: null            # 被本条替代的旧条目 id；旧条目 status 改 superseded
confidence: 0.8             # 0–1；只在会话里推测、没验证的写 0.6 以下
created: "2026-11-05"
updated: "2026-11-05"
```

| 板块 | id 前缀 | 文件名 |
|---|---|---|
| 项目 | `proj-<项目短名>` | `07-Projects/proj-<项目短名>.md`（一个项目只有一页） |
| 通识、排障 | `ke-YYYYMMDD-<短名>` | 与 id 相同 |
| 经验 | `xp-YYYYMMDD-<短名>` | 与 id 相同 |
| 实验 | `lab-YYYYMMDD-<短名>` | 与 id 相同 |

## 项目（`07-Projects/proj-*.md`）

额外字段：`status: active | paused | completed | archived`，`repo`（本机路径），`aliases`（会话里出现的项目名，如 `cchp-k8s-ops`，自动沉淀靠它找到这一页）。

正文固定几节，模板是 `Templates/project-tracker.md`：

- **Overview**：项目目标、技术栈。很少改。
- **Current State**：阶段 / 进度 / 下一步。**整节替换**为最新状态，不追加。
- **Architecture Decisions**：表格，一行一个决策（日期、决策、理由、影响）。推翻旧决策时在旧行加删除线并注明被哪天的决策替代，不删行。
- **Session Log**：按天倒序，一天一个 `### YYYY-MM-DD` 小节，写目标、完成、遗留、下次继续，每条尽量带会话编号。同一天多次整理合并进同一小节。
- **Blockers**：当前阻塞。解决了就移走，写进当天的 Session Log。
- **Key Knowledge Refs**：本项目沉淀出的通识 / 经验 / 实验条目链接。

## 通识（`principle` `pattern` `architecture` `research`）

模板 `Templates/knowledge-entry.md`：问题、背景、分析、关键洞察、取舍、相关。只收**换个项目也成立**的结论；项目特有的事实写进项目页。

## 经验

- **`debug`（`03-Debug/`）**：现象 → 排查过程（走过的弯路要写）→ 根因 → 修复 → 怎么验证 → 以后怎么避免。
- **`experience`（`10-Experience/`）**：做法、流程、协作和工具使用的心得。正文按分享贴写：背景、做法（可照着做的步骤）、效果、坑、适用范围。`status: draft | published`。

## 实验（`11-Experiments/`）

`status: idea | planned | trying | concluded | dropped`。额外字段：

```yaml
hypothesis: 用 LanceDB 替换 Weaviate + SurrealDB，检索质量不降、依赖更少
next_step: 先建 50 题评测集，测现状的 recall@5
result: ""                 # concluded 时写结论；dropped 写放弃原因
links: ["https://…"]       # 触发这个想法的文章（如 AIHOT 简报）
```

正文：动机、方案、试验记录（按日期）、结论。日报会列出 `trying` 和超过 14 天没动的 `planned`。

## 状态的含义

- `draft`：自动写入或刚记下，还没人看过。
- `review` / `validated`：本人看过 / 验证过。
- `outdated`：内容可能过时，等重写。
- `superseded`：已被 `supersedes` 指向它的新条目替代，保留不删。
