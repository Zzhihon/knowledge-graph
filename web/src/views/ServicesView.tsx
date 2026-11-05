// 本地服务: every LaunchAgent and listening port the services collector saw, grouped 本人的 /
// 工具的 / 系统的, with status, last restart, ports, open incidents and a redacted log tail.
import { useCallback, useEffect, useMemo, useState, type CSSProperties } from 'react'
import { RefreshCw, Search } from 'lucide-react'
import { servicesApi, type Service, type ServiceGroup, type ServiceIncident, type ServiceLog, type ServicesOverview, type ServiceStatus } from '../api/services'
import { Drawer, KV, Problem, Section, btn } from '../components/k/HubBits'
import { Empty } from './DailyView'

const GROUP: Record<ServiceGroup, [string, string]> = {
  mine: ['本人的', '自己写的服务和定时任务；挂了会出事故'],
  tool: ['工具的', '数据库、代理、包管理器装的后台；只展示，可在登记里打开告警'],
  system: ['系统的', '应用自带的后台和第三方端口；只展示'],
}
const STATUS: Record<ServiceStatus, string> = { up: '在跑', degraded: '不太好', down: '挂了', asleep: '休眠', unknown: '不确定' }
const TONE: Record<ServiceStatus, [string, string]> = {
  up: ['--k-ok', '--k-ok-soft'],
  degraded: ['--k-amber-ink', '--k-amber-soft'],
  down: ['--k-hot', '--k-hot-soft'],
  asleep: ['--k-ink-4', '--k-line-soft'],
  unknown: ['--k-ink-3', '--k-line-soft'],
}
const MODE: Record<string, string> = { keepalive: '常驻', scheduled: '定时', ondemand: '按需' }
const RULE: Record<string, string> = {
  'service.down': '没在运行', 'service.port_missing': '端口不在', 'service.health': '健康检查失败',
  'service.flapping': '反复重启', 'service.exposed': '安全提醒',
}
const POLL_MS = 30_000

const tone = (s: ServiceStatus | null): CSSProperties => {
  const [c, cs] = TONE[s ?? 'unknown']
  return { ['--c' as string]: `var(${c})`, ['--cs' as string]: `var(${cs})` }
}
const hot: CSSProperties = { ['--c' as string]: 'var(--k-hot)', ['--cs' as string]: 'var(--k-hot-soft)' }
const amber: CSSProperties = { ['--c' as string]: 'var(--k-amber-ink)', ['--cs' as string]: 'var(--k-amber-soft)' }

function ago(ts: number | null | undefined) {
  if (!ts) return ''
  const s = Date.now() / 1000 - ts
  if (s < 90) return '刚刚'
  if (s < 3600) return `${Math.round(s / 60)} 分钟前`
  if (s < 86400) return `${Math.round(s / 3600)} 小时前`
  return `${Math.round(s / 86400)} 天前`
}
const clock = (ts: number | null | undefined) => (ts ? new Date(ts * 1000).toLocaleString('zh-CN', { hour12: false, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '')

function Pill({ status }: { status: ServiceStatus | null }) {
  return <span className="k-chip" style={tone(status)}>{STATUS[status ?? 'unknown']}</span>
}

function PortChips({ s }: { s: Service }) {
  const ports = s.manager === 'port'
    ? [{ port: s.port ?? 0, exposed: !!s.exposed, addresses: s.addresses ?? [] }]
    : s.ports
  if (!ports.length) return null
  return (
    <span className="inline-flex flex-wrap gap-1">
      {ports.map((p) => (
        <span key={p.port} className="k-mono rounded-full border px-2 text-[12px] leading-[19px]"
          title={p.addresses.join(' ')}
          style={p.exposed
            ? { color: 'var(--k-hot)', borderColor: 'color-mix(in srgb, var(--k-hot) 35%, transparent)' }
            : { color: 'var(--k-ink-3)', borderColor: 'var(--k-line)' }}>
          {p.exposed ? `${p.addresses.find((a) => !a.startsWith('127.') && a !== '[::1]') ?? '*'}:` : ':'}{p.port}
        </span>
      ))}
    </span>
  )
}

function sub(s: Service) {
  const bits: string[] = []
  if (s.manager === 'launchd') {
    if (s.mode) bits.push(MODE[s.mode] ?? s.mode)
    if (s.loaded === false) bits.push('没加载')
    else if (s.pid === null && s.last_exit !== null && s.last_exit !== 0) bits.push(`上次退出码 ${s.last_exit}`)
  } else {
    bits.push(s.inferred ? '端口 · 推断是你起的' : '端口')
  }
  if (!s.registered) bits.push('未登记')
  if (s.last_restart && s.status !== 'asleep') bits.push(`${ago(s.last_restart)}启动`)
  if (s.health) bits.push(s.health.ok ? `健康 ${s.health.ms ?? '?'}ms` : `健康检查 ${s.health.code || '连不上'}`)
  return bits.join(' · ')
}

function Row({ s, onOpen }: { s: Service; onOpen: () => void }) {
  const title = s.about || s.label || s.name
  return (
    <button onClick={onOpen} className="flex w-full min-w-0 flex-wrap items-center gap-x-3 gap-y-1.5 px-4 py-3 text-left hover:bg-k-sunk sm:flex-nowrap sm:px-5">
      <span className="w-[58px] shrink-0"><Pill status={s.status} /></span>
      <span className="min-w-0 flex-1 basis-[200px]">
        <span className="block truncate text-[14.5px] font-medium text-k-ink">{title}</span>
        <span className="block truncate text-[12.5px] text-k-ink4">
          {s.about && s.label && <span className="k-mono mr-1.5">{s.label}</span>}{sub(s)}
        </span>
      </span>
      <span className="flex shrink-0 flex-wrap items-center gap-1.5 pl-[70px] sm:pl-0">
        <PortChips s={s} />
        {s.incidents.length > 0 && (
          <span className="k-chip" style={s.incidents.some((i) => i.severity === 'critical') ? hot : amber}>
            {s.incidents.length === 1 ? RULE[s.incidents[0].rule] ?? '事故' : `${s.incidents.length} 个事故`}
          </span>
        )}
      </span>
    </button>
  )
}

function Incidents({ items }: { items: ServiceIncident[] }) {
  if (!items.length) return null
  return (
    <div className="mt-5 space-y-2">
      {items.map((i) => (
        <div key={i.id} className="rounded-tile border px-4 py-2.5 text-[14px]"
          style={{ background: i.severity === 'critical' ? 'var(--k-hot-soft)' : 'var(--k-amber-soft)', borderColor: 'color-mix(in srgb, var(--k-hot) 22%, transparent)' }}>
          <div className="flex flex-wrap items-baseline gap-x-2">
            <span className="font-semibold" style={{ color: i.severity === 'critical' ? 'var(--k-hot)' : 'var(--k-amber-ink)' }}>{RULE[i.rule] ?? i.rule}</span>
            <span className="k-mono text-[12px] text-k-ink4">#{i.id} · {ago(i.opened_at)}开</span>
          </div>
          <div className="mt-0.5 text-k-ink2">{i.summary}</div>
        </div>
      ))}
    </div>
  )
}

function LogTail({ id }: { id: string }) {
  const [log, setLog] = useState<ServiceLog | null>(null)
  const [error, setError] = useState('')
  const [lines, setLines] = useState(20)
  const load = useCallback(() => {
    setError('')
    servicesApi.log(id, lines).then(setLog).catch((e) => setError(String(e.message ?? e)))
  }, [id, lines])
  useEffect(load, [load])
  if (error) return <Problem message={`读不到日志：${error}`} />
  if (!log) return <p className="text-[13.5px] text-k-ink4">读取中…</p>
  if (!log.files.length) return <p className="text-[13.5px] text-k-ink4">plist 里没登记日志路径。</p>
  return (
    <div className="space-y-4">
      {log.files.map((f) => (
        <div key={f.path} className="min-w-0">
          <div className="mb-1.5 flex flex-wrap items-center gap-2 text-[12.5px] text-k-ink4">
            <span className="k-mono min-w-0 break-all">{f.path}</span>
            {f.modified && <span className="whitespace-nowrap">· 写于 {ago(f.modified)}</span>}
          </div>
          {f.error
            ? <p className="text-[13.5px] text-k-ink3">{f.error}</p>
            : f.lines.length
              ? <pre className="k-mono max-h-[46vh] overflow-auto whitespace-pre-wrap break-all rounded-tile border border-k-line-soft bg-k-sunk px-3 py-2.5 text-[12px] leading-[1.6] text-k-ink2">{f.lines.join('\n')}</pre>
              : <p className="text-[13.5px] text-k-ink4">文件是空的。</p>}
        </div>
      ))}
      <div className="flex items-center gap-2 text-[12.5px] text-k-ink4">
        <span>疑似密钥、令牌和外部链接已遮住。</span>
        <span className="ml-auto" />
        {[20, 100].map((n) => (
          <button key={n} onClick={() => setLines(n)} className={`${btn} !px-2.5 !py-0.5 !text-[12px]`} aria-pressed={lines === n}>{n} 行</button>
        ))}
        <button onClick={load} className={`${btn} !px-2.5 !py-0.5 !text-[12px]`} aria-label="重新读日志"><RefreshCw className="h-3.5 w-3.5" /></button>
      </div>
    </div>
  )
}

function Detail({ s, onClose }: { s: Service; onClose: () => void }) {
  const health = s.health && (
    <span>
      <span style={{ color: s.health.ok ? 'var(--k-ok)' : 'var(--k-hot)' }}>{s.health.ok ? '正常' : '失败'}</span>
      {' '}· HTTP {s.health.code || '无响应'}{s.health.ms !== null && ` · ${s.health.ms}ms`}
      <span className="k-mono block break-all text-[12.5px] text-k-ink4">{s.health.url}</span>
    </span>
  )
  const ports = s.manager === 'port'
    ? (s.addresses ?? []).map((a) => `${a}:${s.port}`).join('  ')
    : s.ports.map((p) => p.addresses.map((a) => `${a}:${p.port}`).join('  ')).join('  ')
  return (
    <Drawer title={s.about || s.label || s.name} sub={<span className="k-mono">{s.id}</span>} onClose={onClose} wide>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Pill status={s.status} />
        <span className="text-[13px] text-k-ink4">{GROUP[s.group][0]} · {s.registered ? '已登记' : '未登记'} · {s.alert ? '会告警' : '只展示'}{s.status_ts && ` · 采集于 ${ago(s.status_ts)}`}</span>
      </div>
      <KV rows={[
        ['label', s.label && <span className="k-mono">{s.label}</span>],
        ['类型', s.manager === 'launchd' ? `${MODE[s.mode ?? ''] ?? '按需'}任务${s.loaded === false ? '（没加载）' : ''}` : `端口${s.owner ? `（属于 ${s.owner}）` : ''}${s.inferred ? '，推断是你起的' : ''}`],
        ['仓库', s.repo],
        ['程序', s.command && <span className="k-mono break-all text-[12.5px]">{s.command}</span>],
        ['PID', s.pid ?? (s.pids?.length ? s.pids.join(', ') : null)],
        ['上次退出', s.last_exit !== null && s.pid === null ? `退出码 ${s.last_exit}${s.last_exit === -15 ? '（SIGTERM）' : s.last_exit === -9 ? '（SIGKILL）' : ''}` : null],
        ['启动次数', s.runs !== null ? `launchd 共启动 ${s.runs} 次` : null],
        ['最近启动', s.last_restart && `${clock(s.last_restart)}（${ago(s.last_restart)}）`],
        ['端口', ports || (s.expected_port ? `应在 ${s.expected_port}，没在监听` : null)],
        ['健康', health],
      ]} />
      <Incidents items={s.incidents} />
      <h3 className="mb-2 mt-7 text-[15px] font-[650] text-k-ink">日志末尾</h3>
      {s.has_log ? <LogTail id={s.id} /> : <p className="text-[13.5px] text-k-ink4">{s.manager === 'port' ? '端口没有对应的 plist，没有日志可看。' : 'plist 里没登记日志路径。'}</p>}
    </Drawer>
  )
}

export default function ServicesView() {
  const [data, setData] = useState<ServicesOverview | null>(null)
  const [error, setError] = useState('')
  const [open, setOpen] = useState<Service | null>(null)
  const [q, setQ] = useState('')
  const [onlyTrouble, setOnlyTrouble] = useState(false)
  const [showAll, setShowAll] = useState<Record<string, boolean>>({})

  const refresh = useCallback(() => {
    servicesApi.overview().then((d) => { setData(d); setError('') }).catch((e) => setError(String(e.message ?? e)))
  }, [])
  useEffect(() => {
    refresh()
    const t = setInterval(refresh, POLL_MS)
    return () => clearInterval(t)
  }, [refresh])

  const groups = useMemo(() => {
    if (!data) return null
    const needle = q.trim().toLowerCase()
    const keep = (s: Service) =>
      (!onlyTrouble || s.incidents.length > 0 || s.status === 'down' || s.status === 'degraded') &&
      (!needle || [s.name, s.label, s.about, s.repo, s.command, String(s.port ?? ''), ...s.ports.map((p) => String(p.port))]
        .some((x) => x && x.toLowerCase().includes(needle)))
    return (Object.keys(GROUP) as ServiceGroup[]).map((g) => [g, data.groups[g].filter(keep)] as const)
  }, [data, q, onlyTrouble])

  if (!data) {
    return (
      <div className="mx-auto w-full max-w-[1180px] pb-16">
        <h1 className="text-[28px] font-[650] text-k-ink">本地服务</h1>
        {error ? <div className="mt-6"><Problem message={`读不到服务清单：${error}`} /></div> : <Empty>读取中…</Empty>}
      </div>
    )
  }
  const st = data.statuses

  return (
    <div className="mx-auto w-full max-w-[1180px] pb-16">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-2">
        <h1 className="text-[28px] font-[650] tracking-[0.01em] text-k-ink">本地服务</h1>
        <span className="text-[15px] text-k-ink3">LaunchAgent 和监听端口 · {data.count} 个</span>
        <span className="ml-auto inline-flex items-center gap-2 text-[13px] text-k-ink4" title={error || undefined}>
          <span className="h-2 w-2 rounded-full" style={{ background: error ? 'var(--k-hot)' : 'var(--k-ok)' }} />
          {error ? `刷新失败：${error}` : data.collected_at ? `采集于 ${ago(data.collected_at)}` : '还没采集过'}
        </span>
      </div>

      <div className="mt-5 grid grid-cols-3 gap-x-6 gap-y-3 border-y border-k-line-strong py-3 sm:flex sm:flex-wrap sm:items-baseline sm:gap-x-8">
        {([
          [data.incidents, '个未关闭事故', data.incidents ? 'var(--k-hot)' : undefined],
          [st.up, '在跑'],
          [st.down, '挂了', st.down ? 'var(--k-hot)' : undefined],
          [st.degraded, '不太好', st.degraded ? 'var(--k-amber-ink)' : undefined],
          [st.asleep, '休眠'],
          [st.unknown, '不确定'],
        ] as [number, string, string?][]).map(([n, unit, color]) => (
          <span key={unit} className="inline-flex items-baseline gap-1.5">
            <span className="k-num text-[23px] font-bold leading-none tracking-[-0.02em] text-k-ink" style={color ? { color } : undefined}>{n}</span>
            <span className="text-[12.5px] text-k-ink4">{unit}</span>
          </span>
        ))}
      </div>

      <div className="mt-5 flex flex-wrap items-center gap-3">
        <label className="k-search min-w-0 flex-1 basis-[240px]">
          <Search className="h-4 w-4 shrink-0" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="找 label、说明、端口、仓库" aria-label="筛选服务" />
        </label>
        <div className="k-seg" role="group" aria-label="范围">
          <button aria-pressed={!onlyTrouble} onClick={() => setOnlyTrouble(false)}>全部</button>
          <button aria-pressed={onlyTrouble} onClick={() => setOnlyTrouble(true)}>只看有事的</button>
        </div>
      </div>

      {data.count === 0 && (
        <p className="k-card mt-8 px-6 py-8 text-center text-[14.5px] text-k-ink4">
          还没有采集结果。先跑一次 <span className="k-mono">kg plane collect services</span>。
        </p>
      )}

      {groups?.map(([g, items]) => {
        const all = data.groups[g].length
        if (!all) return null
        const limit = g === 'mine' || showAll[g] || q || onlyTrouble ? items.length : 8
        const trouble = data.groups[g].filter((s) => s.incidents.length).length
        return (
          <Section key={g} title={GROUP[g][0]} aside={<>{all} 个{trouble ? <span className="text-k-hot"> · {trouble} 个有事故</span> : ''} · {GROUP[g][1]}</>}>
            {items.length ? (
              <div className="k-card divide-y divide-k-line-soft overflow-hidden">
                {items.slice(0, limit).map((s) => <Row key={s.id} s={s} onOpen={() => setOpen(s)} />)}
                {items.length > limit && (
                  <button onClick={() => setShowAll((v) => ({ ...v, [g]: true }))} className="w-full px-5 py-2.5 text-center text-[13.5px] text-k-ink3 hover:bg-k-sunk hover:text-k-accent">
                    展开其余 {items.length - limit} 个
                  </button>
                )}
              </div>
            ) : <p className="k-card px-6 py-5 text-center text-[14px] text-k-ink4">没有符合条件的</p>}
          </Section>
        )
      })}

      {open && <Detail s={open} onClose={() => setOpen(null)} />}
    </div>
  )
}
