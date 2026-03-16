# RSS 自动摄取系统 - 完整实现

## ✅ 已完成

### 核心组件

1. **Source Adapter 架构** (`agents/sources/`)
   - `base.py` — `SourceDocument` 统一中间格式 + `BaseAdapter` 基类
   - `rss.py` — RSS/Atom feed 适配器
   - `state.py` — Watermark 状态管理（`.kg/source_state.yaml`）

2. **CLI 命令** (`agents/cli.py`)
   - `kg pull rss` — RSS 拉取命令
   - 支持 `--since`, `--dry-run`, `--workers`, `--quality-check` 选项

3. **配置文件**
   - `feeds.yaml` — 18 个预配置优质技术博客源
   - `feeds-test.yaml` — 测试用简化配置（2 个 feed）

4. **自动化脚本**
   - `scripts/kg-pull-rss.sh` — Cron 定时任务脚本
   - 日志管理（`.kg/logs/rss-pull-YYYYMMDD.log`）

5. **文档**
   - `docs/RSS-INGESTION.md` — 完整使用指南

### 依赖安装

```bash
# 已添加到 pyproject.toml
feedparser>=6.0      # RSS/Atom 解析
trafilatura>=1.12    # 正文提取
html2text>=2024.2    # HTML → Markdown
httpx>=0.27          # HTTP 客户端

# 安装
source .venv/bin/activate
pip install -e .
```

---

## 🚀 快速开始

### 1. 测试拉取（dry-run）

```bash
source .venv/bin/activate

# 使用测试配置（只有 2 个 feed）
kg pull rss --feeds feeds-test.yaml --since 30 --dry-run

# 使用完整配置（18 个 feed）
kg pull rss --since 7 --dry-run
```

### 2. 实际拉取

```bash
# 拉取最近 7 天的文章
kg pull rss --since 7

# 增量拉取（基于 watermark）
kg pull rss

# 同步索引
kg sync
```

### 3. 设置 cron 自动化

```bash
# 编辑 crontab
crontab -e

# 每天早上 9 点拉取
0 9 * * * /Users/bt1q/Github-Projects/knowledge-graph/scripts/kg-pull-rss.sh

# 查看日志
tail -f .kg/logs/rss-pull-$(date +%Y%m%d).log
```

---

## 📊 工作流程

```
feeds.yaml (18 个优质源)
       ↓
kg pull rss --since 7
       ↓
并行拉取 (5 workers)
  ├─ feedparser 解析 RSS/Atom
  ├─ trafilatura 提取正文（去广告）
  └─ html2text 转 Markdown
       ↓
SourceDocument (统一格式)
  - source_type: "rss_article"
  - source_id: article_url
  - content: markdown
  - domain: golang/cloud-native/...
  - tags: [...]
       ↓
ingest_file_with_quality()
  ├─ Claude 提取知识条目
  ├─ 质量评估 (novelty + quality)
  └─ 去重检测
       ↓
写入 vault (01-05 目录)
       ↓
更新 watermark (.kg/source_state.yaml)
       ↓
kg sync (更新 Qdrant + SurrealDB)
```

---

## 🎯 预配置的 18 个优质源

### Go 生态 (3)
- Go Official Blog
- Dave Cheney
- Ardan Labs

### 云原生 (3)
- Cloudflare Engineering
- Kubernetes Blog
- CNCF Blog

### 分布式系统 (3)
- Netflix Tech Blog
- Jepsen
- Uber Engineering

### 架构 (2)
- Martin Fowler
- AWS Architecture Blog

### 性能 (1)
- Brendan Gregg

### 大厂博客 (4)
- Meta Engineering
- Stripe Engineering
- GitHub Engineering
- Dropbox Tech

### 理论研究 (2)
- Papers We Love
- Google Research Blog

---

## 🔧 配置说明

### feeds.yaml 结构

```yaml
feeds:
  - url: <RSS/Atom URL>
    name: <显示名称>
    domain: <知识域>
    tags: [<标签列表>]
    quality_weight: <0.0-1.0>

config:
  max_age_days: 30              # 只拉取最近 N 天
  min_content_length: 500       # 最小正文长度
  parallel_workers: 5           # 并行数
  novelty_threshold: 0.3        # 新颖度阈值
  quality_threshold: 0.4        # 质量阈值
```

### Watermark 状态

`.kg/source_state.yaml`:

```yaml
rss:
  https://go.dev/blog/feed.atom:
    last_published: "2026-03-15T10:00:00+00:00"
    last_checked: "2026-03-16T09:00:00+00:00"
```

---

## 📝 使用示例

### 场景 1: 首次拉取

```bash
# 只拉取最近 7 天，避免一次性太多
kg pull rss --since 7

# 查看结果
kg stats
kg review --domain golang
```

### 场景 2: 日常增量拉取

```bash
# 基于 watermark 自动增量
kg pull rss

# 同步索引
kg sync
```

### 场景 3: 测试新 feed

```bash
# 创建 test-feeds.yaml，只包含新 feed
kg pull rss --feeds test-feeds.yaml --since 7 --dry-run

# 确认无误后实际拉取
kg pull rss --feeds test-feeds.yaml --since 7
```

### 场景 4: 定期维护

```bash
# 每周运行
kg review --domain golang --gaps
kg distill --discover --threshold 0.80
kg radar --domain cloud-native
```

---

## 🐛 故障排查

### 问题 1: Feed 解析失败

```bash
# 查看详细错误
kg pull rss --dry-run 2>&1 | grep "Failed"

# 单独测试某个 feed
# 创建只包含该 feed 的 yaml
kg pull rss --feeds single-feed.yaml --dry-run
```

### 问题 2: Claude API 超时

编辑 `agents/sources/rss.py`，增加超时时间：

```python
client = anthropic.Anthropic(
    http_client=httpx.Client(timeout=httpx.Timeout(120.0, connect=30.0)),
)
```

### 问题 3: 内容提取失败

某些网站可能有反爬虫机制，`trafilatura` 无法提取。查看日志：

```bash
kg pull rss --dry-run 2>&1 | grep "Skipping"
```

---

## 🔮 下一步扩展

### Phase 2: GitHub PR Comments

```python
# agents/sources/github.py
class GitHubAdapter(BaseAdapter):
    def fetch(self, since=None):
        # gh api repos/{owner}/{repo}/pulls/{pr}/comments
        # 聚合 PR thread
        # 保留 diff context
        ...
```

```bash
kg pull github --repo aicoin/frontend --since 7d
```

### Phase 3: Slack Discussions

```python
# agents/sources/slack.py
class SlackAdapter(BaseAdapter):
    def fetch(self, since=None):
        # Slack API conversations.history
        # 按 thread 聚合
        # reactions 作为质量信号
        ...
```

```bash
kg pull slack --channel tech-discussion --since 7d
```

### Phase 4: Webhook 实时推送

```python
# agents/api_routes/webhook.py
@router.post("/webhook/github")
async def github_webhook(payload: dict):
    # 实时接收 PR comment 事件
    # 异步处理 ingest
    ...
```

---

## 📚 参考文档

- `docs/RSS-INGESTION.md` — 完整使用指南
- `feeds.yaml` — 订阅配置
- `scripts/kg-pull-rss.sh` — Cron 脚本
- `agents/sources/` — Source adapter 实现

---

## ✨ 核心优势

1. **零配置起步** — 18 个优质源预配置，开箱即用
2. **增量拉取** — Watermark 机制，只拉取新文章
3. **智能去重** — 质量评估 + 相似度检测
4. **并行高效** — 5 workers 并行拉取
5. **可扩展架构** — Source Adapter 模式，轻松添加新源
6. **自动化友好** — Cron 脚本 + 日志管理
7. **复用现有能力** — 完全复用 `ingest_file_with_quality()` pipeline

---

## 🎉 总结

RSS 自动摄取系统已完整实现，可以：

✅ 从 18 个优质技术博客自动拉取文章
✅ 智能提取知识条目并写入 vault
✅ 增量拉取 + 去重 + 质量评估
✅ Cron 自动化 + 日志管理
✅ 为 GitHub/Slack 扩展预留架构

**立即开始**: `kg pull rss --since 7 --dry-run`
