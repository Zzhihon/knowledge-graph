# RSS 自动摄取系统 - 完整实现与模型配置

## ✅ 已完成的功能

### 1. 核心架构
- **Source Adapter 模式** (`agents/sources/`)
  - `base.py` — 统一的 `SourceDocument` 中间格式
  - `rss.py` — RSS/Atom 适配器（feedparser + trafilatura + html2text）
  - `state.py` — Watermark 状态管理

### 2. CLI 命令
```bash
kg pull rss                    # 增量拉取
kg pull rss --since 7          # 拉取最近 7 天
kg pull rss --dry-run          # 预览模式
kg pull rss --workers 10       # 调整并行数
```

### 3. 配置文件
- `feeds.yaml` — 18 个优质技术博客源
- `feeds-test.yaml` — 测试配置（2 个 feed）
- `config.yaml` — 模型配置

### 4. 自动化
- `scripts/kg-pull-rss.sh` — Cron 脚本
- 日志管理（`.kg/logs/`）

---

## 🤖 LLM 模型配置

### 当前配置

**模型**: `claude-opus-4-6`
**调用方式**: 直接调用 Anthropic API（不复用 Claude Code）
**配置位置**: `config.yaml`

```yaml
agent:
  model: "claude-opus-4-6"
  embedding_model: "all-MiniLM-L6-v2"
```

**API 配置**（环境变量）:
```bash
ANTHROPIC_BASE_URL=https://your-proxy-url.example.com
ANTHROPIC_AUTH_TOKEN=YOUR_API_KEY_HERE
```

### 模型对比

| 特性 | claude-sonnet-4 | claude-opus-4-6 |
|------|----------------|-----------------|
| **提取质量** | 高 | 极高 |
| **速度** | 快（~10s/文章） | 慢（~30s/文章） |
| **Token 消耗** | 中等 | 高 |
| **成本** | 低 | 高（10倍） |
| **稳定性** | 极好 | 一般（偶尔超时） |
| **适用场景** | 批量自动化 | 高质量单篇分析 |

### 测试结果

**Sonnet 测试**（3 篇文章）:
- ✅ 提取 9 个知识条目
- ✅ 创建 7 个，合并 2 个
- ✅ 无错误，速度快

**Opus 测试**（3 篇文章）:
- ✅ 前 2 篇成功，提取 5 个条目
- ❌ 第 3 篇遇到连接错误
- ⚠️ 速度较慢，偶尔超时

---

## 🔧 已实现的优化

### 1. 增加 Token 限制
```python
max_tokens=16384  # 从 8192 增加到 16384
```

### 2. 超时配置
```python
timeout=httpx.Timeout(180.0, connect=30.0)  # 3 分钟超时
```

### 3. 重试机制
```python
max_retries=3  # 失败后自动重试 3 次
backoff=2^attempt  # 指数退避: 1s, 2s, 4s
```

### 4. 截断处理
```python
if message.stop_reason == "max_tokens":
    console.print("[yellow]警告: 响应被截断[/]")
    return []  # 跳过这篇文章
```

### 5. JSON 解析增强
- 修复未转义引号
- 处理截断的 JSON
- 智能闭合数组

---

## 💡 推荐配置

### 方案 A: Opus（当前配置）

**适用场景**:
- 高质量知识提取
- 单篇深度分析
- 不在意成本和速度

**配置**:
```yaml
agent:
  model: "claude-opus-4-6"
```

**优点**: 提取质量最高，洞察最深
**缺点**: 速度慢，成本高，偶尔超时

### 方案 B: Sonnet（推荐用于自动化）

**适用场景**:
- 批量自动摄取
- Cron 定时任务
- 成本敏感场景

**配置**:
```yaml
agent:
  model: "claude-sonnet-4-20250514"
```

**优点**: 速度快，成本低，稳定性好
**缺点**: 提取质量略低于 Opus（但仍然很高）

### 方案 C: 混合策略

```python
# 根据文章长度动态选择模型
if len(content) > 10000:
    model = "claude-opus-4-6"  # 长文章用 Opus
else:
    model = "claude-sonnet-4-20250514"  # 短文章用 Sonnet
```

---

## 🎯 我的建议

### 立即行动

**保持 Opus**，因为：
1. ✅ 已添加重试机制和容错处理
2. ✅ 提取质量确实更高
3. ✅ 你的代理支持（虽然偶尔超时）
4. ✅ 对于知识积累，质量 > 速度

**如果遇到频繁超时**，可以随时切换回 Sonnet：
```bash
# 一行命令切换
sed -i '' 's/claude-opus-4-6/claude-sonnet-4-20250514/' config.yaml
```

---

## 📊 成本估算

### 每天运行（18 个 feed，假设 10 篇新文章）

**Opus**:
- 输入: ~40K tokens
- 输出: ~20K tokens
- 成本: ~$0.90/天（官方价格）

**Sonnet**:
- 输入: ~40K tokens
- 输出: ~15K tokens
- 成本: ~$0.09/天（官方价格）

**你的代理**: 成本可能不同，需要咨询代理商

---

## 🚀 使用指南

### 测试拉取
```bash
# 使用当前配置（Opus）
kg pull rss --feeds feeds-test.yaml --since 30 --dry-run

# 如果成功，实际拉取
kg pull rss --since 7

# 同步索引
kg sync
```

### 设置自动化
```bash
# 编辑 crontab
crontab -e

# 每天早上 9 点拉取
0 9 * * * /Users/bt1q/Github-Projects/knowledge-graph/scripts/kg-pull-rss.sh
```

### 切换模型
```bash
# 切换到 Sonnet
sed -i '' 's/claude-opus-4-6/claude-sonnet-4-20250514/' config.yaml

# 切换回 Opus
sed -i '' 's/claude-sonnet-4-20250514/claude-opus-4-6/' config.yaml
```

---

## 📚 相关文档

- `docs/RSS-INGESTION.md` — 完整使用指南
- `docs/RSS-IMPLEMENTATION.md` — 实现总结
- `feeds.yaml` — 18 个优质源配置
- `config.yaml` — 模型配置

---

## ✨ 总结

RSS 自动摄取系统已完整实现，支持：

✅ 18 个优质技术博客自动拉取
✅ 智能知识提取（Opus 或 Sonnet）
✅ 增量拉取 + 去重 + 质量评估
✅ 重试机制 + 容错处理
✅ Cron 自动化 + 日志管理
✅ 灵活的模型配置

**当前配置**: `claude-opus-4-6`（高质量）
**备选方案**: `claude-sonnet-4-20250514`（高性能）

立即开始: `kg pull rss --since 7`
