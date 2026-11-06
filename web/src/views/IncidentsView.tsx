// 事故: open and recently closed control-plane incidents, each with its signal timeline, what
// self-healing did (with an undo button where it can be undone) and the retro entry link.
import { useCallback, useEffect, useState, type CSSProperties } from 'react'
import { RefreshCw, Undo2 } from 'lucide-react'
import { incidentsApi, type HealAction, type Incident, type IncidentBoard, type TimelineStep } from '../api/incidents'
import { Problem, Section, btn } from '../components/k/HubBits'
import { Empty } from './DailyView'

const POLL_MS = 30_000
const RULE: Record<string, string> = {
  'egress.outage': '出口断线', 'egress.identity': '出口不符', 'egress.purity': '纯净度变差',
  'service.down': '服务没在运行', 'service.port_missing': '端口不在', 'service.health': '健康检查失败',
  'service.flapping': '反复重启', 'service.exposed': '端口暴露',
  'artifact.public_sensitive': '公开工件含敏感内容', 'artifact.expiring': '工件快过期', 'project.stalled': '项目停滞',
}
const ACTION: Record<string, string> = {
  pause_capability: '暂停依赖出口的规则', resume_capability: '恢复规则', run_rule: '补跑', kickstart: '重启服务',
  index_stale: '标记索引过期', index_fresh: '索引标回正常', silence: '静默一周', unsilence: '取消静默',
}
const RESULT: Record<string, string> = { done: '已执行', suggested: '只建议', 'suggest-only': '只能建议', skipped: '跳过', failed: '失败' }
const MODE: Record<string, string> = { auto: '自动', suggest: '建议', owner: '你' }
const STEP: Record<string, string> = { up: 'var(--k-ok)', degraded: 'var(--k-amber-ink)', down: 'var(--k-hot)', asleep: 'var(--k-ink-4)', unknown: 'var(--k-ink-3)' }

const sevTone = (s: string): CSSProperties => s === 'critical'
  ? { ['--c' as string]: 'var(--k-hot)', ['--cs' as string]: 'var(--k-hot-soft)' }
  : s === 'warning'
    ? { ['--c' as string]: 'var(--k-amber-ink)', ['--cs' as string]: 'var(--k-amber-soft)' }
    : { ['--c' as string]: 'var(--k-ink-3)', ['--cs' as string]: 'var(--k-line-soft)' }
const SEV: Record<string, string> = { critical: '严重', warning: '警告', info: '提醒' }

const clock = (ts: number | null | undefined) => (ts ? new Date(ts * 1000).toLocaleString('zh-CN', { hour12: false, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '')
const hm = (iso: string) => new Date(iso).toLocaleTimeString('zh-CN', { hour12: false, hour: '2-digit', minute: '2-digit' })
const span = (m: number) => (m < 90 ? `${m} 分钟` : m < 2880 ? `${Math.round(m / 60)} 小时` : `${Math.round(m / 1440)} 天`)

function Timeline({ steps }: { steps: TimelineStep[] }) {
  if (!steps.length) return <p className="text-[13px] text-k-ink4">这段时间没有信号。</p>
  const total = steps.reduce((n, s) => n + s.count, 0)
  return (
    <div>
      <div className="flex h-2.5 w-full overflow-hidden rounded-full bg-k-sunk" role="img" aria-label="信号时间线">
        {steps.map((s, i) => (
          <span key={i} title={`${hm(s.from)}–${hm(s.to)} ${s.status} ×${s.count}`}
            style={{ width: `${(s.count / total) * 100}%`, background: STEP[s.status] ?? 'var(--k-ink-4)' }} />
        ))}
      </div>
      <ol className="mt-2 space-y-1 text-[13px] text-k-ink3">
        {steps.map((s, i) => (
          <li key={i} className="flex min-w-0 gap-2">
            <span className="k-mono shrink-0 text-k-ink4">{hm(s.from)}{s.to !== s.from && `–${hm(s.to)}`}</span>
            <span className="shrink-0 font-medium" style={{ color: STEP[s.status] }}>{s.status}</span>
            <span className="shrink-0 text-k-ink4">×{s.count}</span>
            {s.reasons.length > 0 && <span className="min-w-0 truncate">{s.reasons.join('；')}</span>}
          </li>
        ))}
      </ol>
    </div>
  )
}

function ActionRow({ a, onUndo, busy }: { a: HealAction; onUndo: (a: HealAction) => void; busy: boolean }) {
  const failed = a.result === 'failed'
  return (
    <li className="flex flex-wrap items-center gap-x-2 gap-y-1 py-1.5 text-[13.5px]">
      <span className="k-mono text-[12px] text-k-ink4">{clock(a.at)}</span>
      <span className="text-k-ink">{ACTION[a.action] ?? a.action}</span>
      {a.target && <span className="k-mono min-w-0 truncate text-[12.5px] text-k-ink3">{a.target}</span>}
      <span className="k-chip" style={failed ? sevTone('critical') : sevTone('info')}>{MODE[a.mode] ?? a.mode} · {RESULT[a.result] ?? a.result}</span>
      {a.undone && <span className="text-[12.5px] text-k-ink4">已撤销</span>}
      {typeof a.detail.why === 'string' && <span className="basis-full text-[12.5px] text-k-ink4">{a.detail.why}</span>}
      {a.undoable && (
        <button className={`${btn} ml-auto`} disabled={busy} onClick={() => onUndo(a)} title={a.undo}>
          <Undo2 size={14} />撤销
        </button>
      )}
    </li>
  )
}

function Card({ i, onUndo, busy }: { i: Incident; onUndo: (a: HealAction) => void; busy: boolean }) {
  const open = i.closed_at === null
  return (
    <article className="rounded-tile border border-k-line bg-k-surface px-4 py-4 sm:px-5">
      <header className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <span className="k-chip" style={sevTone(i.severity)}>{SEV[i.severity] ?? i.severity}</span>
        <h3 className="text-[15.5px] font-[650] text-k-ink">{RULE[i.rule] ?? i.rule}</h3>
        <span className="k-mono text-[12px] text-k-ink4">#{i.id}</span>
        <span className="ml-auto text-[13px]" style={{ color: open ? 'var(--k-hot)' : 'var(--k-ink-4)' }}>
          {open ? `已持续 ${span(i.minutes)}` : `持续 ${span(i.minutes)} · ${i.closed_reason === 'gone' ? '资源消失' : '已恢复'}`}
        </span>
      </header>
      <p className="mt-1 text-[14px] text-k-ink2">{i.summary}</p>
      <p className="k-mono mt-0.5 break-all text-[12.5px] text-k-ink4">
        {i.resource_name && i.resource_name !== i.resource_id && <span className="mr-1.5 font-sans text-k-ink3">{i.resource_name}</span>}
        {i.resource_id} · {clock(i.opened_at)}开{i.closed_at && ` · ${clock(i.closed_at)}关`}
      </p>
      <div className="mt-3"><Timeline steps={i.timeline} /></div>
      {i.actions.length > 0 && (
        <ul className="mt-3 divide-y divide-k-line-soft border-t border-k-line-soft">
          {i.actions.map((a) => <ActionRow key={a.id} a={a} onUndo={onUndo} busy={busy} />)}
        </ul>
      )}
      {i.entry_id && (
        <p className="mt-3 text-[13.5px] text-k-ink3">
          复盘：<a className="text-k-accent hover:underline" href={i.entry_path ? `#read=${encodeURIComponent(i.entry_path)}` : undefined}>{i.entry_id}</a>
        </p>
      )}
    </article>
  )
}

export default function IncidentsView() {
  const [data, setData] = useState<IncidentBoard | null>(null)
  const [error, setError] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)

  const refresh = useCallback(() => {
    incidentsApi.board().then((d) => { setData(d); setError('') }).catch((e) => setError(String(e.message ?? e)))
  }, [])
  useEffect(() => {
    refresh()
    const t = setInterval(refresh, POLL_MS)
    return () => clearInterval(t)
  }, [refresh])

  const onUndo = (a: HealAction) => {
    if (!window.confirm(`撤销「${ACTION[a.action] ?? a.action}」？${a.undo}`)) return
    setBusy(true)
    incidentsApi.undo(a.id)
      .then((r) => { setNote(r.text); refresh() })
      .catch((e) => setNote(String(e.message ?? e)))
      .finally(() => setBusy(false))
  }

  return (
    <div className="mx-auto w-full max-w-[1180px] pb-16">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-2">
        <h1 className="text-[28px] font-[650] tracking-[0.01em] text-k-ink">事故</h1>
        <span className="text-[15px] text-k-ink3">管控面的异常、自愈动作和复盘 · 最近 7 天</span>
        <button className={`${btn} ml-auto`} onClick={refresh}><RefreshCw size={14} />刷新</button>
      </div>
      {error && <div className="mt-5"><Problem message={`读不到事故：${error}`} /></div>}
      {note && <div className="mt-4 rounded-tile border border-k-line bg-k-sunk px-4 py-2.5 text-[14px] text-k-ink2">{note}</div>}
      {!data ? (!error && <Empty>读取中…</Empty>) : (
        <>
          <div className="mt-5 flex flex-wrap gap-x-8 gap-y-3 border-y border-k-line-strong py-3">
            {([[data.open.length, '个未关闭', data.open.length ? 'var(--k-hot)' : undefined],
              [data.closed.length, '个最近关闭'], [data.silenced.length, '个静默中']] as [number, string, string?][]).map(([n, unit, color]) => (
              <span key={unit} className="inline-flex items-baseline gap-1.5">
                <span className="k-num text-[23px] font-bold leading-none text-k-ink" style={color ? { color } : undefined}>{n}</span>
                <span className="text-[12.5px] text-k-ink4">{unit}</span>
              </span>
            ))}
            {data.index.stale && <span className="text-[13px]" style={{ color: 'var(--k-amber-ink)' }}>索引过期：{data.index.reason}</span>}
          </div>
          <Section title="未关闭" className="mt-6">
            {data.open.length ? <div className="space-y-3">{data.open.map((i) => <Card key={i.id} i={i} onUndo={onUndo} busy={busy} />)}</div>
              : <Empty>现在没有开着的事故。</Empty>}
          </Section>
          <Section title="最近关闭" className="mt-8">
            {data.closed.length ? <div className="space-y-3">{data.closed.map((i) => <Card key={i.id} i={i} onUndo={onUndo} busy={busy} />)}</div>
              : <Empty>最近 7 天没有关闭的事故。</Empty>}
          </Section>
          {data.silenced.length > 0 && (
            <Section title="静默中" className="mt-8">
              <ul className="space-y-1 text-[13.5px] text-k-ink3">
                {data.silenced.map((s) => (
                  <li key={`${s.rule}|${s.resource}`}><span className="text-k-ink">{RULE[s.rule] ?? s.rule}</span> <span className="k-mono text-[12.5px]">{s.resource}</span> · 到 {clock(s.until)}</li>
                ))}
              </ul>
            </Section>
          )}
        </>
      )}
    </div>
  )
}
