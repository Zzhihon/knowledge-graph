# 本地管控面：状态库、采集器、异常规则

管控面回答两件事：**本机有哪些东西**，**它们现在和过去的状态**。本地服务、代理出口、工件、项目与会话、自动化规则都写进同一个状态库。看板、告警、MCP 都从这里读，不再各自读原始数据，也不再各自定义"异常"。

```
agent-autoloop 定时  ─▶  kg plane collect <采集器> --autoloop
                          │ 采集器只读本机，返回 resources + signals
                          ▼
                     run_collector（一个事务）
                          ├─ upsert resource，这轮没看到的记 gone_at
                          ├─ 追加 signal
                          ├─ 跑这类资源的异常规则 ─▶ 开 / 关 incident ─▶ 回执事件 plane.incident.*
                          └─ 30 天前的 signal 按小时折叠，1 年前的删掉
                          ▼
              <vault>/.kg/plane/state.db  ─▶  /api/plane/*  ─▶  kg-web、MCP
```

采集器是固定脚本，不调用模型。状态库只用标准库 `sqlite3`，WAL 模式，采集时 API 照常能读。状态库在 `.kg/` 下，不进 git。

## 资源 id 规则

告警、事故、知识条目都按 id 引用资源。**id 一旦写进状态库就不能改。** 规则写在 `kg/plane/ids.py`：

```
<kind>:<来源>:<本地 id>        service:launchd:com.example.web
                               service:port:8765
                               artifact:orca:<工件 id>
                               session:claude:<会话 uuid>
<kind>:<本地 id>               project:<slug>、egress:<线路名>（来源本身就唯一时）
```

- `kind` 只能是 `service`、`egress`、`artifact`、`project`、`session`、`rule`，小写。
- 后面一段或多段，用 `:` 分隔。第一段写来源，最后一段原样写来源里的 id（launchd label、端口号、工件 id、会话 uuid）。
- 每段不能为空，不能含 `:`、空白、控制字符。其他字符原样保留，包括大小写、点号和中文。
- 总长不超过 256 字符。

用 `make_id(kind, *parts)` 生成 id。不合规的分段直接报错，不做清洗：一旦清洗规则改了，同一个东西就会得到另一个 id。来源里的 id 本身不稳定时（例如每次启动都变的应用实例 label），不要把它记成资源。

## 表结构

迁移按 `PRAGMA user_version` 记版本，打开状态库时自动执行（`kg/plane/store.py` 的 `_MIGRATIONS`）。新增迁移只能往列表末尾追加，不能改已有的迁移。代码认识的版本比库里的旧时，打开会报错，不会降级。

| 表 | 列 | 说明 |
|---|---|---|
| `resource` | `id` 主键、`kind`、`name`、`source`、`attrs` JSON、`first_seen`、`last_seen`、`gone_at` | `source` 是写它的采集器名。资源不删，只记 `gone_at`；再次出现时清空 |
| `signal` | `resource_id`、`ts`、`status`、`value`、`unit`、`detail` JSON、`samples` | `status` 取 `up` / `down` / `degraded` / `unknown` / `asleep`。`samples = 0` 是原始读数，`samples = N` 是 N 条读数折成的小时汇总 |
| `incident` | `id`、`resource_id`、`rule`、`opened_at`、`closed_at`、`severity`、`summary`、`evidence` JSON、`entry_id` | `rule` 是开它的规则名。同一 (resource, rule) 最多一条未关闭，靠部分唯一索引保证。`entry_id` 留给事故沉淀成知识条目后回填（#9） |

索引：`signal(resource_id, ts)`、`signal(ts)`、`resource(kind)`、`resource(source)`、`incident(resource_id, opened_at)`，以及上面的 `incident_open_once`。

时间一律是整数 Unix 秒。

## 保留期

- 原始 signal 保留 30 天（`RAW_DAYS`）。
- 更早的按资源、按整点小时折成一条：`status` 取这一小时最多的状态，票数相同时取更坏的（down > degraded > unknown > asleep > up）；`detail.statuses` 记各状态的条数；`value` 是均值，`detail.min` / `detail.max` 记范围。
- 超过 1 年（`KEEP_DAYS`）的删掉。

`compact(conn, now=...)` 每次采集结束时执行，可以重复执行。只折叠已经整个落在 30 天以外的小时，所以再执行一次不会改变任何东西。

## 写一个采集器

在 `kg/plane/collectors/` 下新建一个模块，不用改任何列表，`load_all()` 会导入这个目录下的全部模块。

```python
from kg.plane.collectors import Collection, CollectContext, CommandError, register
from kg.plane.ids import make_id
from kg.plane.store import Resource, Signal


@register
class EgressCollector:
    name = "egress"  # kg plane collect egress；也是这些资源的 source

    def collect(self, ctx: CollectContext) -> Collection:
        out = Collection()
        for line in read_probe_log(ctx.vault):  # 解析写成纯函数，用样例文本测
            rid = make_id("egress", line.name)
            out.resources.append(Resource(rid, "egress", line.name, self.name, {"url": line.url}))
            out.signals.append(Signal(rid, ctx.now, line.status, line.latency_ms, "ms",
                                      {"exit": line.exit_code}))
        return out
```

约定：

- 采集器只读本机，不打开状态库。写库、记 `gone_at`、跑规则、折叠旧数据都由 `run_collector` 在一个事务里完成。
- 执行外部命令走 `ctx.run(argv, timeout=...)`，失败时抛 `CommandError`。测试里给 `CollectContext(run=假的函数)` 喂样例输出。
- 只能给本轮上报的资源写 signal，否则 `run_collector` 报错。
- `Collection.scopes` 写本轮**完整看过**的 id 前缀。某个数据源读失败时，不要把它的前缀写进去，再在 `notes` 里写原因。这样这部分资源不会被误记 `gone_at`。不写 `scopes`（`None`）表示这个采集器的全部资源都看过了。
- 需要本地配置时，读 `<vault>/.kg/plane/config.yaml`，在仓库根目录的 `plane.yaml.example` 补模板。仓库是公开的，代码和模板里不写主机名、IP、账号、端口号、凭据。

采集器 `services`（`kg/plane/collectors/services.py`，#3）只读本机：`launchctl list`、每个已加载任务的 `launchctl print gui/<uid>/<label>`（`runs` 启动次数、plist 路径）、`~/Library/LaunchAgents/*.plist`、`ps -axo pid=,ppid=,etime=`（启动时间、父进程）、`lsof -nP -iTCP -sTCP:LISTEN`，以及本人登记 `<vault>/.kg/plane/services.yaml`（模板 `services.yaml.example`）。

- launchd 任务的 id 是 `service:launchd:<label>`：已加载的、`~/Library/LaunchAgents` 里没加载的、登记了但哪里都找不到的，都各记一个。
- 任务按 plist 分三种：`keepalive`（`KeepAlive` 为 true，必须常驻）、`scheduled`（`StartInterval` / `StartCalendarInterval`）、`ondemand`（其余，包括 `KeepAlive` 是条件字典的）。

  | 情况 | 状态 |
  |---|---|
  | 有 PID | `up`；登记的端口不在、或健康检查失败时 `degraded` |
  | 没 PID，上次被 SIGTERM / SIGKILL 结束 | `unknown`（用户退出、注销、内存回收，判断不了） |
  | 没 PID，`keepalive` | `down`，退出码是 0 也算 |
  | 没 PID，`scheduled` / `ondemand`，上次退出码 0 | `asleep` |
  | 没 PID，`scheduled` / `ondemand`，上次退出码非 0 | `down`（定时任务上次跑失败） |
  | 登记了但没加载 | `down` |
  | 没登记、只有 plist、没加载 | `unknown` |

- 监听端口的 id 是 `service:port:<端口>`。顺着父进程找到所属的 launchd 任务，记在 `attrs.owner`。没登记的端口不再监听时记 `gone_at`；登记成 `port:<n>` 的端口不在时照样上报，记 `down`。`attrs.exposed` 表示监听在回环地址以外。
- 分组 `mine`（本人的）/ `tool`（工具的）/ `system`（系统的）：登记里写的 `group` 优先，其次 `groups` 里的 label 前缀；都没有时 `~/Library/LaunchAgents` 里的算 `tool`，其他算 `system`。端口跟所属任务同组；不属于任何任务、进程名是 node / python 之类的算 `mine`（`attrs.inferred`，多半是自己起的开发服务）。
- 只有 `mine` 默认告警，登记里的 `alert` 可以单独打开或关掉。信号的 `detail.alert` 为 false 时所有规则都判"应该关"，所以关掉告警也会关掉它已经开着的事故。
- 健康检查：登记了 `health` 的，每轮用 `curl --noproxy '*'` 打一次，只打 `127.0.0.1` / `[::1]`，记状态码和延迟（`value`，单位 ms）。2xx / 3xx 算健康，`expect` 里列的状态码也算（例如要钥匙的接口回 401）。
- 默认忽略 `com.apple.` 和 `application.`（应用实例，每次启动 label 都变）开头的 label。额外的前缀写在 `services.yaml` 的 `ignore.labels`（`config.yaml` 的 `services.ignore_prefixes` 仍然有效）。登记过的 label 不受忽略前缀影响。

服务的异常规则（都在 `services.py` 里，合成序列测试在 `tests/test_plane_services.py`）：

| 规则 | 开 | 关 |
|---|---|---|
| `service.down` | 连续 2 次 `down`。`keepalive` 和端口是 critical，定时 / 按需任务是 warning | 一次 `up` 或 `degraded` |
| `service.port_missing` | 进程在，登记的端口连续 2 次不在 | 端口回来 |
| `service.health` | 进程在，健康检查连续 2 次失败 | 一次成功 |
| `service.flapping` | `keepalive` 任务 10 分钟内退出 ≥ 3 次（按 `runs` 的增量，没有时按 PID 变化），"反复重启" | 最近 10 分钟（至少 5 分钟的读数）没再退出 |
| `service.exposed` | `mine` 的端口监听在回环以外，且不在 `ignore.exposed` 里，安全提醒 | 改回只听本机，或加进忽略 |

资源消失（`gone_at`）后不会再有信号，`run_collector` 会把它未关闭的事故一并关掉，`evidence.closed.reason` 记 `gone`。

日志末尾：`GET /api/plane/services/{id}/log?lines=20` 只读这个任务 plist 里登记的 `StandardOutPath` / `StandardErrorPath`，每行先过 `kg/plane/redact.py`（基于 `kg/eval/retrieval.py` 的 `_SECRETISH`）再返回；只接受 Host 是本机的请求。

## 加一条异常规则

规则是纯函数：输入这个资源最近 `window` 条 signal（从旧到新），输出 `Verdict`。

```python
from kg.plane.rules import Verdict, rule


@rule("service", "service.down", window=2)
def service_down(history):
    last = history[-1]
    if last.status == "up":
        return Verdict(False, summary="恢复运行")           # 应该关
    if len(history) >= 2 and all(s.status == "down" for s in history[-2:]):
        return Verdict(True, "critical", "连续 2 次没在运行",  # 应该开
                       {"since": history[-2].ts})
    return None                                             # 判断不了，保持现状
```

- `firing=True` 表示应该有一条未关闭的事故，`firing=False` 表示应该关闭，`None` 表示保持现状。已经开着时再报 `True` 不会重复开；没开时报 `False` 也不做任何事。
- 规则名写进 `incident.rule`，和资源 id 一样，触发过就不能改名。两个不同的函数注册同一个名字会报错。
- 规则放在对应采集器的模块里，或者放在一个被它导入的模块里。这样 `load_all()` 导入采集器时规则也注册上了。
- `severity` 取 `info` / `warning` / `critical`。`evidence` 写判断依据，关闭时的依据记在 `evidence.closed`。
- 每条新 signal 单独判断一次。线上的 `evaluate` 和回放用的 `replay(rule, signals)` 走同一段逻辑，开、关时间完全一致。开的时间是触发那条 signal 的 `ts`。
- 测试写法：构造一段合成序列，断言 `replay` 返回的 `[("open", ts), ("close", ts)]`。见 `tests/test_plane.py` 的 `test_service_down_*`。有真实历史时（例如出口探测记录），用同样方法回放，验证该报的报了、休眠唤醒没有误报。

## 命令

```
kg plane collect services             # 采集一次，写进状态库
kg plane collect services --dry-run   # 完整跑一遍再回滚，看会开、关哪些事故、哪些资源会消失；没有状态库时不会创建
kg plane collect services --autoloop  # agent-autoloop 用的输出
kg plane status                       # 每个采集器的资源数、各状态数、上次采集时间，以及未关闭的事故
```

`--autoloop` 的输出和 `kg capture scan --autoloop` 一样，先写一行结论。没有事故开或关时，这一行以 `没事可做：` 开头，agent-autoloop 不发通知。有变化时，后面跟一个回执块，每个变化一条事件：

```agent-autoloop
{"events": [{"id": "plane-incident-12-opened", "type": "plane.incident.opened",
             "subject": "service:launchd:com.example.web", "summary": "出事：…"}]}
```

关闭时 `type` 为 `plane.incident.closed`。事件 `id` 带事故编号和动作，重复投递会被去重。

## 定时：接到 agent-autoloop

在 `~/.agent-autoloop/rules.json` 里加一个 `command` agent 和一条定时规则。路径是占位，换成自己的仓库目录：

```json
{
  "agents": {
    "kg-plane-services": {
      "name": "采集本地服务",
      "worker": "<worker>",
      "use": "command",
      "command": ["{cwd}/.venv/bin/kg", "plane", "collect", "services", "--autoloop"]
    }
  },
  "rules": [
    {
      "name": "采集本地服务",
      "when": { "cron": "*/5 * * * *" },
      "to": "kg-plane-services",
      "cwd": "/path/to/knowledge-graph",
      "group": "kg-plane",
      "tell": "needed",
      "emits": { "source": "kg", "types": ["plane.incident.opened", "plane.incident.closed"] }
    }
  ]
}
```

"服务挂掉 5 分钟内出卡片"由两部分配合实现：每 5 分钟采集一次，规则要求连续 2 次 down。如果要更快，把 cron 调成 `*/2`。收到 `plane.incident.*` 事件后发卡片还是自愈，由后续规则决定（#9）。

## API

`kg serve` 默认只监听 127.0.0.1。路由在 `agents/api_routes/plane.py`。

| 端点 | 参数 | 返回 |
|---|---|---|
| `GET /api/plane/resources` | `kind`、`source`、`gone=1`（含已消失的） | `{"items": [...], "count": n}`，每项带最新一条 signal 的 `status`、`status_ts`、`value`、`unit` |
| `GET /api/plane/resources/{id}` | | 单个资源；不存在时返回 404 |
| `GET /api/plane/signals` | `resource`（必填）、`since`（默认 24 小时前）、`until`、`limit`（默认 2000） | 按时间从旧到新；条数超过 `limit` 时只返回最新的 `limit` 条 |
| `GET /api/plane/incidents` | `open=1`、`resource`、`limit` | 按开的时间从新到旧 |
| `GET /api/plane/services` | | 本地服务页用：`groups.mine/tool/system`，组内有事故的、状态更坏的排前面；端口挂在所属任务下面（`ports`），每项带未关闭事故、最近一次重启（`last_restart`）、健康检查 |
| `GET /api/plane/services/{id}/log` | `lines`（默认 20，最多 200） | 日志末尾，已脱敏；路径只来自 plist（`agents/api_routes/services.py`） |

还没有状态库时，所有列表都返回空，不会创建库文件。状态库路径由 `plane_db_path` 依赖提供，测试用 `app.dependency_overrides` 换成临时文件。

## 出口采集器（egress）

配置 `.kg/plane/egress.yaml`（模板 `egress.yaml.example`）。每分钟对每条线路做分段探测：本地代理端口 TCP → 代理内核控制接口（当前节点、节点延迟）→ 经代理请求 trace（出口 IP / 地区 / 机房）→ 经代理对目标 API 域名做无凭据 HEAD。每次探测是 `egress:<线路>` 的一条 signal，`detail` 里是整个观测，规则据此重建观测再判断，所以逐次采集和 `kg plane egress replay` 整段回放开、关事故的时间一致（`tests/test_plane_egress_live.py` 验证）。

- 探测请求只经 `kg/plane/egress_probe.py` 的 `curl_argv` 发出：`curl -q`（不读 ~/.curlrc）、干净环境变量、除固定 User-Agent 外不带任何请求头。控制接口的密钥只发给回环地址。
- 休眠：和上次探测的空档超过 2.5 个周期，或 `kern.waketime` 显示刚醒，这次失败就是"唤醒后"。唤醒后的第一次失败在本轮内隔 `recheck_delay_s`（默认 75 秒）再探一次，两次都写 signal，规则据此判断是唤醒首包还是断线的开始。
- 规则：`egress.outage`（warning，1 分钟周期下连续 3 分钟失败）、`egress.identity`（critical，第一次出口不符就开）、`egress.purity`（info，纯净度检查变差时提醒）。
- 纯净度（每小时 + 出口 IP 变化时）写在 `egress:<线路>:purity`：IP 画像、DNS 泄漏、直连泄漏抽样、时区 / 语言提示；出口 IP 24 小时稳定性由接口从探测 signal 计算。每项带 `checked_at`。
- 旧探测记录：`kg plane egress import <file.ndjson> [--db 路径] [--line 名字]`，导入后逐条跑状态库规则，并和整段回放比对，不一致时退出码 1。
- 接口：`GET /api/plane/egress?hours=24[&now=Unix秒]`。看板"出口"页（`web/src/views/EgressView.tsx`）。

## 进行中看板：`sessions` 采集器

`kg plane collect sessions` 回答"现在有哪些项目在推进、最新的会话做到哪一步、哪里在等我"。代码在 `kg/plane/collectors/sessions.py`，判断都是 `kg/plane/progress.py` 里的纯函数。

```
.kg/capture/config.yaml 的 allow / deny ─┐
Claude / Codex 会话文件 ─▶ session_reader（按 mtime / 大小增量读，缓存 .kg/plane/sessions-cursor.json）
orca worktree ps / project list --json ─┤
git status / log / remote ──────────────┼─▶ judge_project ─▶ project:<名字>、session:<来源>:<id>
GitHub 汇总快照（只读文件）──────────────┤
07-Projects/proj-*.md 的 Current State ─┘
```

- **会话**：和夜间沉淀用同一套解析和跳过规则（子 agent、无头运行、导入副本、agent-autoloop 派的活），另外跳过 `codex exec` 和不在白名单里的目录。每个文件记读到的字节位置、大小和 mtime；文件没变就不打开，变长了只读新增的完整行，变短了从头读。文本先脱敏（链接、`kg eval mine` 的密钥规则、`token:` 后面的值、IPv4）再截断，缓存里也只有脱敏后的内容。只聊了一句"hi"、没有回复的会话不展示。
- **会话状态**：`waiting`（Agent 最后一步在问你，或者挂着 AskUserQuestion 这类提问工具）、`working`（10 分钟内文件有变化）、`done`（停在一条普通回复上）、`interrupted`（停在你的话或工具调用上）。怎么算"在问你"见 `question_reason`，每条都写明依据。
- **项目阶段**，按顺序判断：等你（3 天内有会话在等你）→ 活跃（有会话在写，或 1 小时内有会话、提交、Orca 终端输出）→ 停滞（3 天没动，但有未提交改动或未合并 PR）→ 已收尾（没有未提交改动和未合并 PR）→ 歇着（其余）。信号状态：活跃 `up`，等你、停滞 `degraded`，歇着、已收尾 `asleep`。
- **规则 `project.stalled`**：项目停滞时开一条 info 事故，不再停滞就关，`--autoloop` 会发 `plane.incident.*` 事件。
- **Orca** 读不到时 `orca` 记 `null`（未知），不报错；不在白名单里的工作区只计数，不写进状态库。
- **未合并 PR** 只读 `sessions.github_snapshot` 指向的快照里本人的 PR，按 GitHub 仓库对到项目；快照没覆盖的仓库记 `null`。几个本地仓库指向同一个 GitHub 仓库时，PR 算在和仓库同名的项目上。

接口（`agents/api_routes/sessions.py`）：

| 端点 | 参数 | 返回 |
|---|---|---|
| `GET /api/plane/projects` | | 项目列表（等你、活跃、停滞、歇着、已收尾的顺序），`attention.waiting`（3 天内在等你的会话）、`attention.stalled`，`collected_at`、`stale`（超过两个周期没采集） |
| `GET /api/plane/sessions` | `project`、`state`、`limit` | 最近 7 天的会话，新的在前；每条带 `resume` 恢复命令 |

"在等你"的判断要用真实会话人工抽查：`python -m kg.plane.waiting_review export` 抽 20 个（一半判成在等、一半判成不在等）写到 `.kg/plane/waiting-review.jsonl`（不进 git），逐行把 `human` 填成 true / false，再 `python -m kg.plane.waiting_review score` 看一致率。`--days 30 --exclude <上一轮文件>` 用来抽一轮没见过的会话。
