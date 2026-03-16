# RSS 自动摄取系统使用指南

## 快速开始

### 1. 安装依赖

```bash
cd /Users/bt1q/Github-Projects/knowledge-graph
pip install -e .
```

新增依赖:
- `feedparser` — RSS/Atom 解析
- `trafilatura` — 正文提取（去广告/导航）
- `html2text` — HTML → Markdown 转换
- `httpx` — HTTP 客户端

### 2. 配置订阅源

编辑 `feeds.yaml`（已预配置 18 个优质源）：

```yaml
feeds:
  - url: https://go.dev/blog/feed.atom
    name: Go Official Blog
    domain: golang
    tags: [official, language-design]
    quality_weight: 1.0
```

### 3. 手动拉取测试

```bash
# 拉取所有 feeds（增量，基于 watermark）
kg pull rss

# 只拉取最近 7 天的文章
kg pull rss --since 7

# 预览模式（不实际写入）
kg pull rss --dry-run

# 指定自定义 feeds.yaml
kg pull rss --feeds /path/to/custom-feeds.yaml
```

### 4. 设置 cron 自动拉取

```bash
# 编辑 crontab
crontab -e

# 添加以下行（每天早上 9 点拉取）
0 9 * * * /Users/bt1q/Github-Projects/knowledge-graph/scripts/kg-pull-rss.sh

# 或者每 6 小时拉取一次
0 */6 * * * /Users/bt1q/Github-Projects/knowledge-graph/scripts/kg-pull-rss.sh
```

查看 cron 日志：

```bash
tail -f .kg/logs/rss-pull-$(date +%Y%m%d).log
```

---

## 工作流程

```
feeds.yaml 配置
       ↓
kg pull rss (CLI 命令)
       ↓
并行拉取 18 个 RSS feeds
       ↓
trafilatura 提取正文 → html2text 转 Markdown
       ↓
生成 SourceDocument (统一中间格式)
       ↓
ingest_file_with_quality() pipeline
       ↓
Claude 提取知识条目 + 质量评估 + 去重
       ↓
写入 vault (01-05 目录)
       ↓
kg sync (更新 Qdrant + SurrealDB 索引)
```

---

## 增量拉取机制

### Watermark 管理

系统自动维护每个 feed 的 watermark（上次拉取的最新文章时间戳），存储在 `.kg/source_state.yaml`：

```yaml
rss:
  https://go.dev/blog/feed.atom:
    last_published: "2026-03-15T10:00:00+00:00"
    last_checked: "2026-03-16T09:00:00+00:00"
  https://blog.cloudflare.com/rss/:
    last_published: "2026-03-14T15:30:00+00:00"
    last_checked: "2026-03-16T09:00:00+00:00"
```

**行为**:
- 首次拉取：拉取所有文章（受 `max_age_days` 限制）
- 后续拉取：只拉取 `last_published` 之后的新文章
- `--since N` 覆盖 watermark，强制拉取最近 N 天

### 去重机制

1. **Source ID 去重**: 每篇文章的 URL 作为 `source_id`，写入 Qdrant payload
2. **质量评估去重**: `ingest_file_with_quality()` 会检测与现有条目的相似度
   - 相似度 < 0.3 → 创建新条目
   - 相似度 >= 0.3 → 合并到现有条目
   - 质量分 < 0.4 → 跳过

---

## 配置详解

### feeds.yaml 结构

```yaml
feeds:
  - url: <RSS/Atom feed URL>
    name: <人类可读名称>
    domain: <知识域，对应 config.yaml 中的 domain>
    tags: [<标签列表>]
    quality_weight: <质量权重 0.0-1.0>

config:
  max_age_days: 30              # 只拉取最近 N 天的文章
  min_content_length: 500       # 最小正文长度（字符）
  parallel_workers: 5           # 并行拉取数
  user_agent: "KG-Vault/0.2"    # User-Agent 标识
  novelty_threshold: 0.3        # 新颖度阈值（低于此值合并）
  quality_threshold: 0.4        # 质量阈值（低于此值跳过）
```

### 推荐配置

**高频更新源**（每天拉取）:
- Go Official Blog
- Cloudflare Engineering
- Netflix Tech Blog
- Jepsen

**低频更新源**（每周拉取）:
- Martin Fowler
- Papers We Love
- Brendan Gregg

**调整策略**:
- `quality_weight: 1.0` — 顶级源（Go 官方、大厂博客）
- `quality_weight: 0.8-0.9` — 优质个人博客
- `quality_weight: 0.7` — 社区聚合源

---

## 常见问题

### Q: 拉取失败怎么办？

```bash
# 查看详细日志
kg pull rss --dry-run

# 单独测试某个 feed
# 临时创建只包含一个 feed 的 test-feeds.yaml
kg pull rss --feeds test-feeds.yaml --dry-run
```

### Q: 如何过滤低质量文章？

调整 `feeds.yaml` 中的阈值：

```yaml
config:
  min_content_length: 1000      # 提高最小长度
  quality_threshold: 0.5        # 提高质量门槛
```

### Q: 如何添加新的 RSS 源？

1. 找到博客的 RSS/Atom feed URL（通常在页面底部或 `/feed`、`/rss`、`/atom.xml`）
2. 添加到 `feeds.yaml`：

```yaml
feeds:
  - url: https://example.com/feed
    name: Example Blog
    domain: golang  # 选择合适的 domain
    tags: [performance, concurrency]
    quality_weight: 0.85
```

3. 测试拉取：

```bash
kg pull rss --since 7 --dry-run
```

### Q: 如何查看拉取历史？

```bash
# 查看 watermark 状态
cat .kg/source_state.yaml

# 查看 cron 日志
ls -lh .kg/logs/rss-pull-*.log
tail -100 .kg/logs/rss-pull-$(date +%Y%m%d).log
```

### Q: 如何重置某个 feed 的 watermark？

编辑 `.kg/source_state.yaml`，删除对应 feed 的条目，或修改 `last_published` 时间戳。

---

## 性能优化

### 并行拉取

默认 5 个 worker 并行拉取。调整 `parallel_workers`：

```yaml
config:
  parallel_workers: 10  # 更快，但可能触发 rate limit
```

### 内容提取优化

`trafilatura` 已经很高效，但可以调整：

```python
# agents/sources/rss.py
clean_text = extract(
    content_html,
    include_comments=False,  # 不包含评论
    include_tables=True,     # 包含表格
    include_images=False,    # 不包含图片（减少 token）
)
```

### 质量评估优化

如果 Claude API 调用太慢，可以禁用质量评估：

```bash
kg pull rss --no-quality-check
```

但这会导致更多低质量条目和重复条目。

---

## 扩展到其他源

### GitHub PR Comments（未来）

```bash
kg pull github --repo aicoin/frontend --since 7d
```

### Slack Discussions（未来）

```bash
kg pull slack --channel tech-discussion --since 7d
```

架构已预留扩展点，只需实现对应的 `Adapter` 类。

---

## 故障排查

### 依赖安装失败

```bash
# 单独安装 RSS 相关依赖
pip install feedparser trafilatura html2text httpx
```

### Feed 解析失败

某些 feed 可能格式不标准，查看错误日志：

```bash
kg pull rss --dry-run 2>&1 | grep "Failed to parse"
```

### Claude API 超时

增加超时时间（需修改 `agents/sources/rss.py`）：

```python
client = anthropic.Anthropic(
    http_client=httpx.Client(timeout=httpx.Timeout(120.0, connect=30.0)),
)
```

---

## 最佳实践

1. **首次拉取**: 使用 `--since 7` 限制范围，避免一次性拉取太多
2. **定期审查**: 每周运行 `kg review --domain <d>` 检查知识质量
3. **手动精选**: 对于重要文章，手动添加到 `related_topics` 和 `tags`
4. **定期蒸馏**: 运行 `kg distill --discover` 合并相似条目
5. **监控日志**: 定期检查 `.kg/logs/` 确保拉取正常

---

## 下一步

- [ ] 添加 GitHub PR comments 拉取
- [ ] 添加 Slack discussions 拉取
- [ ] 实现 webhook 实时推送模式
- [ ] 添加通知机制（新知识条目摘要邮件）
- [ ] 支持 OPML 导入/导出订阅列表
