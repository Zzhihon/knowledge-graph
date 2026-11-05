// 出口: proxy egress lines from the control-plane store — 24-hour status bar, latency, exit history,
// purity checks with their check times, availability / error budget, open incidents.
// Data: GET /api/plane/egress, refreshed every 30 s. `?egress_at=<unix s>` looks at a past moment.
import { useCallback, useEffect, useMemo, useState, type CSSProperties, type MouseEvent, type ReactNode } from 'react'
import { fetchEgress, type EgressLine, type EgressPoint, type EgressSpan, type EgressState, type PurityCheck } from '../api/egress'
import { Section } from '../components/k/HubBits'
import { Empty } from './DailyView'

const REFRESH_MS = 30_000

const STATUS: Record<string, { label: string; ink: string; soft: string }> = {
  up: { label: '正常', ink: '--k-ok', soft: '--k-ok-soft' },
  down: { label: '断线', ink: '--k-hot', soft: '--k-hot-soft' },
  asleep: { label: '休眠', ink: '--k-violet', soft: '--k-violet-soft' },
  degraded: { label: '降级', ink: '--k-amber-ink', soft: '--k-amber-soft' },
  unknown: { label: '未知', ink: '--k-ink-4', soft: '--k-line-soft' },
  nodata: { label: '无数据', ink: '--k-line-strong', soft: '--k-line-soft' },
}
const statusOf = (s: string | null | undefined) => STATUS[s ?? 'unknown'] ?? STATUS.unknown
const tone = (s: string | null | undefined): CSSProperties => {
  const t = statusOf(s)
  return { ['--c' as string]: `var(${t.ink})`, ['--cs' as string]: `var(${t.soft})` }
}

const CHECK: Record<string, { label: string; ink: string; soft: string }> = {
  ok: { label: '通过', ink: '--k-ok', soft: '--k-ok-soft' },
  warn: { label: '要留意', ink: '--k-hot', soft: '--k-hot-soft' },
  hint: { label: '提示', ink: '--k-amber-ink', soft: '--k-amber-soft' },
  unknown: { label: '没测出', ink: '--k-ink-4', soft: '--k-line-soft' },
  skipped: { label: '未启用', ink: '--k-ink-4', soft: '--k-line-soft' },
}
const CHECK_NAMES: [string, string][] = [
  ['ip_stability', '出口 IP 稳定性'], ['ip_profile', 'IP 画像'], ['dns_leak', 'DNS 泄漏'],
  ['direct_leak', '直连泄漏'], ['locale', '时区 / 语言'],
]
const SEGMENTS: [string, string][] = [['local_port', '本地端口'], ['proxy_api', '内核接口'], ['identity', '出口身份'], ['target', '目标服务']]
const FAULT: Record<string, string> = {
  proxy_process: '本地代理进程不在', proxy_api: '代理内核接口异常', upstream: '上游断了',
  identity_mismatch: '出口身份不符', target: '目标服务不可达',
}
const RULE: Record<string, string> = { 'egress.outage': '断线', 'egress.identity': '出口身份', 'egress.purity': '纯净度' }
const SEVERITY: Record<string, [string, string, string]> = {
  critical: ['紧急', '--k-hot', '--k-hot-soft'], warning: ['警告', '--k-amber-ink', '--k-amber-soft'], info: ['提醒', '--k-accent', '--k-accent-soft'],
}

const pad = (n: number) => String(n).padStart(2, '0')
const hm = (ts: number) => { const d = new Date(ts * 1000); return `${pad(d.getHours())}:${pad(d.getMinutes())}` }
function stamp(ts: number | null | undefined, now: number) {
  if (!ts) return '从未'
  const d = new Date(ts * 1000), n = new Date(now * 1000)
  const y = new Date(n); y.setDate(n.getDate() - 1)
  const day = d.toDateString() === n.toDateString() ? '今天' : d.toDateString() === y.toDateString() ? '昨天' : `${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
  return `${day} ${hm(ts)}`
}
function ago(ts: number | null | undefined, now: number) {
  if (!ts) return '从未'
  const s = now - ts
  if (s < 90) return '刚刚'
  if (s < 3600) return `${Math.round(s / 60)} 分钟前`
  if (s < 86400) return `${Math.round(s / 3600)} 小时前`
  return `${Math.round(s / 86400)} 天前`
}
const minutes = (s: number) => (s < 3600 ? `${Math.round(s / 60)} 分钟` : `${(s / 3600).toFixed(1)} 小时`)
const pct = (r: number | null | undefined, digits = 2) => (r === null || r === undefined ? '—' : `${(r * 100).toFixed(digits)}%`)

function Chip({ style, children }: { style: CSSProperties; children: ReactNode }) {
  return <span className="k-chip" style={style}>{children}</span>
}

// ── 24-hour status bar ────────────────────────────────────────────────────

function StatusBar({ bar, start, end }: { bar: EgressSpan[]; start: number; end: number }) {
  const span = Math.max(1, end - start)
  const ticks = [0, 6, 12, 18, 24].map((h) => start + (h / 24) * span)
  const totals = bar.reduce<Record<string, number>>((acc, b) => { acc[b.status] = (acc[b.status] ?? 0) + (b.to - b.from); return acc }, {})
  return (
    <div>
      <div className="flex h-7 w-full overflow-hidden rounded-[6px] border border-k-line-soft bg-k-sunk" role="img"
        aria-label={`过去 24 小时：${Object.entries(totals).map(([k, v]) => `${statusOf(k).label} ${minutes(v)}`).join('，')}`}>
        {bar.map((b, i) => (
          <div key={i} title={`${statusOf(b.status).label} ${hm(b.from)}–${hm(b.to)}（${minutes(b.to - b.from)}）`}
            className="h-full"
            style={{ width: `${((b.to - b.from) / span) * 100}%`, background: b.status === 'nodata' ? 'transparent' : `var(${statusOf(b.status).ink})`, opacity: b.status === 'asleep' ? 0.55 : 1 }} />
        ))}
      </div>
      <div className="relative mt-1 h-4 text-[11.5px] text-k-ink4">
        {ticks.map((t, i) => (
          <span key={i} className="k-num absolute -translate-x-1/2 first:translate-x-0 last:-translate-x-full" style={{ left: `${(i / 4) * 100}%` }}>{hm(t)}</span>
        ))}
      </div>
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[12.5px] text-k-ink3">
        {(['up', 'down', 'asleep', 'degraded', 'nodata'] as const).filter((k) => k !== 'degraded' || totals[k]).map((k) => (
          <span key={k} className="inline-flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded-[3px] border border-k-line-soft" style={{ background: k === 'nodata' ? 'transparent' : `var(${statusOf(k).ink})`, opacity: k === 'asleep' ? 0.55 : 1 }} />
            {statusOf(k).label}<span className="k-num text-k-ink4">{minutes(totals[k] ?? 0)}</span>
          </span>
        ))}
      </div>
    </div>
  )
}

// ── latency chart (plain SVG) ─────────────────────────────────────────────

const W = 720, H = 180, PAD_L = 44, PAD_R = 10, PAD_T = 10, PAD_B = 22

function niceMax(v: number) {
  const steps = [200, 500, 1000, 1500, 2000, 3000, 5000, 10000]
  return steps.find((s) => s >= v) ?? Math.ceil(v / 5000) * 5000
}

function LatencyChart({ points, start, end, interval, stats }: {
  points: EgressPoint[]; start: number; end: number; interval: number; stats: EgressLine['latency_stats']
}) {
  const [hover, setHover] = useState<EgressPoint | null>(null)
  const up = points.filter((p) => p.status === 'up' && p.total_ms)
  const top = niceMax(Math.max(400, ...up.map((p) => p.total_ms ?? 0)))
  const x = (ts: number) => PAD_L + ((ts - start) / Math.max(1, end - start)) * (W - PAD_L - PAD_R)
  const y = (ms: number) => PAD_T + (1 - Math.min(ms, top) / top) * (H - PAD_T - PAD_B)
  // Break the line wherever a probe failed or the gap is longer than 2.5 intervals.
  const paths = useMemo(() => {
    const out: string[] = []
    let cur = ''
    let prev: EgressPoint | null = null
    for (const p of points) {
      const ok = p.status === 'up' && p.total_ms
      if (!ok || (prev && p.ts - prev.ts > interval * 2.5)) { if (cur) out.push(cur); cur = '' }
      if (ok) cur += `${cur ? 'L' : 'M'}${x(p.ts).toFixed(1)},${y(p.total_ms!).toFixed(1)}`
      prev = p
    }
    if (cur) out.push(cur)
    return out
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [points, start, end, top, interval])
  const nodePath = useMemo(() => points.filter((p) => p.node_ms).map((p, i) => `${i ? 'L' : 'M'}${x(p.ts).toFixed(1)},${y(p.node_ms!).toFixed(1)}`).join(''),
  // eslint-disable-next-line react-hooks/exhaustive-deps
    [points, start, end, top])
  const grid = [0, top / 2, top]
  const fails = points.filter((p) => p.status === 'down')

  const onMove = (e: MouseEvent<SVGSVGElement>) => {
    const box = e.currentTarget.getBoundingClientRect()
    const ts = start + ((e.clientX - box.left) / box.width * W - PAD_L) / (W - PAD_L - PAD_R) * (end - start)
    let best: EgressPoint | null = null
    for (const p of points) if (!best || Math.abs(p.ts - ts) < Math.abs(best.ts - ts)) best = p
    setHover(best && Math.abs(best.ts - ts) < interval * 3 ? best : null)
  }

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-baseline gap-x-4 gap-y-1 text-[12.5px] text-k-ink4">
        <span>p50 <b className="k-num font-semibold text-k-ink2">{stats.p50 ? `${Math.round(stats.p50)} ms` : '—'}</b></span>
        <span>p95 <b className="k-num font-semibold text-k-ink2">{stats.p95 ? `${Math.round(stats.p95)} ms` : '—'}</b></span>
        <span>最大 <b className="k-num font-semibold text-k-ink2">{stats.max ? `${stats.max} ms` : '—'}</b></span>
        <span className="ml-auto text-k-ink3">
          {hover ? `${hm(hover.ts)} · ${hover.status === 'up' ? `${hover.total_ms} ms` : statusOf(hover.status).label}${hover.tls_ms ? ` · TLS ${hover.tls_ms} ms` : ''}` : '经代理请求 trace 的总耗时'}
        </span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="block h-auto w-full touch-none select-none" role="img" aria-label="延迟曲线" onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        {grid.map((g) => (
          <g key={g}>
            <line x1={PAD_L} x2={W - PAD_R} y1={y(g)} y2={y(g)} stroke="var(--k-line-soft)" strokeWidth={1} />
            <text x={PAD_L - 6} y={y(g) + 4} textAnchor="end" fontSize={11} fill="var(--k-ink-4)" className="k-num">{g >= 1000 ? `${g / 1000}s` : g ? `${g}ms` : '0'}</text>
          </g>
        ))}
        {[0, 6, 12, 18, 24].map((h) => {
          const t = start + (h / 24) * (end - start)
          return <text key={h} x={x(t)} y={H - 6} fontSize={11} fill="var(--k-ink-4)" textAnchor={h === 0 ? 'start' : h === 24 ? 'end' : 'middle'} className="k-num">{hm(t)}</text>
        })}
        {nodePath && <path d={nodePath} fill="none" stroke="var(--k-violet)" strokeWidth={1.2} strokeDasharray="3 3" opacity={0.8} />}
        {paths.map((d, i) => <path key={i} d={d} fill="none" stroke="var(--k-accent)" strokeWidth={1.6} strokeLinejoin="round" strokeLinecap="round" />)}
        {fails.map((p) => <line key={p.ts} x1={x(p.ts)} x2={x(p.ts)} y1={H - PAD_B - 8} y2={H - PAD_B} stroke="var(--k-hot)" strokeWidth={2} />)}
        {hover && (
          <g>
            <line x1={x(hover.ts)} x2={x(hover.ts)} y1={PAD_T} y2={H - PAD_B} stroke="var(--k-ink-4)" strokeWidth={1} strokeDasharray="2 3" />
            {hover.status === 'up' && hover.total_ms && <circle cx={x(hover.ts)} cy={y(hover.total_ms)} r={3.5} fill="var(--k-accent)" stroke="var(--k-surface)" strokeWidth={1.5} />}
          </g>
        )}
      </svg>
      <div className="mt-1 flex flex-wrap gap-x-4 text-[12px] text-k-ink4">
        <span className="inline-flex items-center gap-1.5"><span className="h-[2px] w-4 bg-k-accent" />总耗时</span>
        {nodePath && <span className="inline-flex items-center gap-1.5"><span className="h-0 w-4 border-t border-dashed" style={{ borderColor: 'var(--k-violet)' }} />节点延迟（内核测）</span>}
        {fails.length > 0 && <span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-[2px] bg-k-hot" />探测失败 {fails.length} 次</span>}
      </div>
    </div>
  )
}

// ── pieces ────────────────────────────────────────────────────────────────

function Stat({ value, unit, color }: { value: ReactNode; unit: string; color?: string }) {
  return (
    <span className="inline-flex items-baseline gap-1.5">
      <span className="k-num text-[23px] font-bold leading-none tracking-[-0.02em] text-k-ink" style={color ? { color } : undefined}>{value}</span>
      <span className="text-[12.5px] text-k-ink4">{unit}</span>
    </span>
  )
}

function PurityCard({ name, check, now }: { name: string; check?: PurityCheck; now: number }) {
  const t = CHECK[check?.status ?? 'unknown'] ?? CHECK.unknown
  return (
    <article className="k-card relative min-w-0 overflow-hidden px-4 pb-3 pt-3">
      <span aria-hidden className="absolute inset-y-0 left-0 w-[3px]" style={{ background: `var(${t.ink})` }} />
      <header className="flex items-center gap-2">
        <h3 className="min-w-0 truncate text-[14.5px] font-[650] text-k-ink">{name}</h3>
        <span className="ml-auto"><Chip style={{ ['--c' as string]: `var(${t.ink})`, ['--cs' as string]: `var(${t.soft})` }}>{check ? t.label : '还没查'}</Chip></span>
      </header>
      <p className="mt-1.5 break-words text-[13.5px] leading-[1.65] text-k-ink3">{check?.summary ?? '等下一次纯净度检查（每小时一次，出口 IP 变化时立即）'}</p>
      <p className="mt-2 text-[12px] text-k-ink4">上次检查 <span className="k-num">{stamp(check?.checked_at, now)}</span>{check?.checked_at ? `（${ago(check.checked_at, now)}）` : ''}</p>
    </article>
  )
}

function LineCard({ line, now, hours }: { line: EgressLine; now: number; hours: number }) {
  const start = now - hours * 3600
  const a = line.availability, a24 = line.availability_24h
  const left = a.budget_left_ratio
  const st = statusOf(line.status)
  const slept = line.bar.filter((b) => b.status === 'asleep').reduce((n, b) => n + b.to - b.from, 0)
  return (
    <section className="mt-6 min-w-0">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <h2 className="text-[21px] font-[650] text-k-ink">{line.name}</h2>
        <Chip style={tone(line.status)}>{st.label}</Chip>
        {line.status === 'down' && line.fault && <span className="text-[14px] font-medium text-k-hot">{FAULT[line.fault] ?? line.fault}</span>}
        {line.reason && line.status !== 'up' && <span className="min-w-0 text-[13.5px] text-k-ink3">{line.reason}</span>}
        <span className="ml-auto text-[12.5px] text-k-ink4">
          {line.node && <>节点 <span className="text-k-ink3">{line.node}</span> · </>}上次探测 {ago(line.last_ts, now)}
        </span>
      </div>

      <div className="mt-4 grid grid-cols-2 gap-x-8 gap-y-3 border-y border-k-line-strong py-3 sm:flex sm:flex-wrap sm:items-baseline">
        <Stat value={pct(a.ratio)} unit={`30 天可用性（目标 ${pct(a.target, 1)}）`} color={a.ratio !== null && a.ratio < a.target ? 'var(--k-hot)' : undefined} />
        {left !== null && left < 0
          ? <Stat value={minutes(-a.budget_left_s)} unit={`错误预算已超支（30 天只允许 ${minutes(a.budget_s)}）`} color="var(--k-hot)" />
          : <Stat value={left === null ? '—' : `${Math.round(left * 100)}%`} unit={`错误预算剩余（${minutes(Math.max(0, a.budget_left_s))}）`} color={left !== null && left < 0.25 ? 'var(--k-amber-ink)' : undefined} />}
        <Stat value={pct(a24.ratio)} unit="近 24 小时" />
        <Stat value={minutes(slept)} unit="近 24 小时休眠，不计入" />
      </div>

      {line.segments && (
        <div className="mt-3 flex flex-wrap gap-2 text-[12.5px]">
          {SEGMENTS.filter(([k]) => line.segments?.[k]).map(([k, label]) => {
            const s = line.segments![k]
            const ink = statusOf(['up', 'down', 'degraded'].includes(s.status) ? s.status : 'unknown').ink
            return (
              <span key={k} className="inline-flex items-center gap-1.5 rounded-full border border-k-line-soft bg-k-surface px-2.5 py-1 text-k-ink3" title={s.reason}>
                <span className="h-2 w-2 rounded-full" style={{ background: `var(${ink})` }} />
                {label}{s.ms ? <span className="k-num text-k-ink4">{s.ms} ms</span> : null}{s.status !== 'up' && s.reason ? <span className="text-k-ink4">· {s.reason}</span> : null}
              </span>
            )
          })}
        </div>
      )}

      {line.incidents.length > 0 && (
        <div className="mt-4 space-y-2">
          {line.incidents.map((i) => {
            const [label, ink, soft] = SEVERITY[i.severity] ?? SEVERITY.warning
            const since = typeof i.evidence?.started_at === 'number' ? (i.evidence.started_at as number) : i.opened_at
            return (
              <div key={i.id} role="alert" className="flex flex-wrap items-baseline gap-x-2 gap-y-1 rounded-tile border px-4 py-2.5 text-[14px]"
                style={{ background: `var(${soft})`, borderColor: `color-mix(in srgb, var(${ink}) 30%, transparent)` }}>
                <Chip style={{ ['--c' as string]: `var(${ink})`, ['--cs' as string]: 'var(--k-surface)' }}>{label}</Chip>
                <span className="font-semibold text-k-ink">{RULE[i.rule] ?? i.rule}</span>
                <span className="min-w-0 break-words text-k-ink2">{i.summary}</span>
                <span className="ml-auto whitespace-nowrap text-[12.5px] text-k-ink4">从 {stamp(since, now)} 起 · {ago(i.opened_at, now)}开</span>
              </div>
            )
          })}
        </div>
      )}

      <Section title={`过去 ${hours} 小时`} aside={`每 ${line.interval_s >= 120 ? `${Math.round(line.interval_s / 60)} 分钟` : `${line.interval_s} 秒`}探测一次；空档按休眠算`} className="!mt-6">
        <div className="k-card px-4 pb-3 pt-4 sm:px-5"><StatusBar bar={line.bar} start={start} end={now} /></div>
      </Section>

      <Section title="延迟" className="!mt-6">
        <div className="k-card px-3 pb-3 pt-3 sm:px-5">
          <LatencyChart points={line.latency} start={start} end={now} interval={line.interval_s} stats={line.latency_stats} />
        </div>
      </Section>

      <Section title="出口 IP 与地区" aside="只在本机显示" className="!mt-6">
        <div className="k-card divide-y divide-k-line-soft">
          {[...line.exits].reverse().map((e) => (
            <div key={`${e.exit_ip}-${e.from}`} className="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-3 gap-y-0.5 px-4 py-2.5 sm:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)_auto] sm:px-5">
              <span className="k-mono min-w-0 truncate text-[13.5px] text-k-ink">{e.exit_ip}</span>
              <span className="text-[13px] text-k-ink3 sm:order-none">{[e.loc, e.colo].filter(Boolean).join(' · ') || '—'}</span>
              <span className="col-span-2 text-[12.5px] text-k-ink4 sm:col-span-1 sm:text-right">
                <span className="k-num">{stamp(e.from, now)} – {e.to >= now - line.interval_s * 3 ? '现在' : stamp(e.to, now)}</span> · {e.probes} 次
              </span>
            </div>
          ))}
          {!line.exits.length && <p className="px-5 py-5 text-center text-[13.5px] text-k-ink4">这段时间没有成功的出口探测</p>}
        </div>
      </Section>

      <Section title="纯净度" aside={line.purity.checked_at ? `上次整轮检查 ${stamp(line.purity.checked_at, now)}` : '还没检查过'} className="!mt-6">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {CHECK_NAMES.map(([k, label]) => <PurityCard key={k} name={label} check={line.purity.checks[k]} now={now} />)}
        </div>
      </Section>
    </section>
  )
}

export default function EgressView() {
  const at = useMemo(() => {
    const v = new URLSearchParams(window.location.search).get('egress_at')
    return v && /^\d+$/.test(v) ? Number(v) : undefined
  }, [])
  const [state, setState] = useState<EgressState | null>(null)
  const [error, setError] = useState('')
  const [loadedAt, setLoadedAt] = useState<Date | null>(null)

  const load = useCallback(() => {
    fetchEgress({ now: at }).then((s) => { setState(s); setError(''); setLoadedAt(new Date()) }).catch((e) => setError(String(e?.message ?? e)))
  }, [at])
  useEffect(() => {
    load()
    if (at) return
    const t = window.setInterval(load, REFRESH_MS)
    return () => window.clearInterval(t)
  }, [load, at])

  const now = state?.now ?? Math.floor(Date.now() / 1000)
  const d = new Date(now * 1000)
  const lines = state?.lines ?? []
  const down = lines.filter((l) => l.status === 'down').length
  const open = lines.reduce((n, l) => n + l.incidents.length, 0)

  return (
    <div className="mx-auto w-full max-w-[1180px] pb-16">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-2">
        <h1 className="text-[28px] font-[650] tracking-[0.01em] text-k-ink">出口</h1>
        <span className="text-[15px] text-k-ink3">
          {d.getMonth() + 1}月{d.getDate()}日{at ? ` ${hm(now)}（回看）` : ''} · {lines.length} 条线路{down ? ` · ${down} 条断线` : ''}{open ? ` · ${open} 个未关闭事故` : ''}
        </span>
        <span className="ml-auto inline-flex items-center gap-2 text-[13px] text-k-ink4" title={error || undefined}>
          <span className="h-2 w-2 rounded-full" style={{ background: !error && loadedAt ? 'var(--k-ok)' : 'var(--k-hot)' }} />
          {error ? `读不到状态库：${error}` : loadedAt ? `更新于 ${loadedAt.toLocaleTimeString('zh-CN', { hour12: false })}` : '读取中…'}
        </span>
      </div>
      {state && !lines.length && (
        <Empty>
          还没有出口数据。把仓库里的 egress.yaml.example 复制到 .kg/plane/egress.yaml 填好，
          再运行 <code className="k-mono">kg plane collect egress</code>（或用 <code className="k-mono">kg plane egress import</code> 导入旧探测记录）。
        </Empty>
      )}
      {!state && !error && <Empty>读取中…</Empty>}
      {lines.map((l) => <LineCard key={l.id} line={l} now={now} hours={state!.hours} />)}
    </div>
  )
}
