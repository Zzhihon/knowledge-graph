# 自动沉淀与每日日报

不用再在会话里手动敲 `/sc:kg`：每晚把 Claude Code 和 Codex 的会话读一遍，整理进知识库，早上出一份精简日报。调度交给 [agent-autoloop](https://github.com/KKKKhazix/agent-autoloop)，读会话和收资料是固定脚本、不花 Token，只有整理和写日报两步调用模型。

```
02:30  kg capture scan --autoloop   读会话 → .kg/capture/inbox/<批次>/   ──capture.ready──▶  Claude + kg-distill  → 写入各板块，result.md 结案
09:15  kg brief collect --autoloop  收资料 → .kg/brief/<日期>/input.json ──brief.ready────▶  Claude + kg-daily    → 00-Index/Daily/<日期>.md + 桌面通知
```

板块和条目格式见 [knowledge-schema.md](knowledge-schema.md)。

## 读会话：`kg capture scan`

- **只读白名单。** 会话的 cwd 落在 `.kg/capture/config.yaml` 的 `allow` 里、且不在 `deny` 里才读（模板见 `capture.yaml.example`）。
- **自动跳过的会话：**
  - Claude 子 agent（`subagents/`）和无头运行（`entrypoint: sdk-cli`）
  - Codex 子线程
  - Codex 从 Claude 导入的副本
  - agent-autoloop 自己派的活
  - 用户只说了一两句的新会话
- **压缩。** 摘录只保留四样东西：
  - 本人原话（大段粘贴折成"粘贴 N 字：开头…"）
  - 每段交流里 Agent 最后一条完整回复
  - 前面的进展说明，截到 300 字
  - 一行操作摘要（改了哪些文件、跑了几条命令）

  定时心跳触发的自动化运行只留一行。实测 14 天的会话从约 2400 万字压到约 105 万字。
- **限量。** `budget` 控制每批多少字、单个会话一批读多少字。读不完的会话下一批从断点接着读。没整理完的批次达到 `max_pending_batches` 时停止读新的，订阅额度不会被积压的批次一次用光。
- **断点续读。** `state.json` 按文件记录读到的字节位置。只有整批写完，才会移动进度。写到一半的行（会话还开着）留到下次再读。

`kg capture scan --dry-run` 预览下一批会读哪些会话；`kg capture status` 查看进度和待整理的批次。

## 日报：`kg brief collect`

收集这些资料：

- 上次日报以来整理完的批次
- 更新过的项目页
- 全部实验条目和新写的经验条目
- `brief.extra` 里列的本机文件，比如 GitHub 每日汇总，全文复制进来，因为日报 Agent 只能读知识库目录
- AIHOT 公开 API（`/api/v1`）的最新日报，以及 24 小时内的精选条目

AIHOT 读取失败只记录原因，不影响其余部分。

## 在 agent-autoloop 里接上

规则在 `~/.agent-autoloop/rules.json`。两个 Skill 的源文件在 `integrations/autoloop/skills/`，软链到后台 Claude 配置目录的 `skills/` 下。后台用哪个 Claude 账号、走哪个代理，由 `~/.agent-autoloop/agent-env.sh` 决定。
