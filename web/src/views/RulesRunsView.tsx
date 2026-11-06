import { useCallback, useEffect, useMemo, useState } from 'react'
import { History, Pause, Pencil, Play, Plus, RotateCcw } from 'lucide-react'
import {
  hub, HubApiError, type AuditEntry, type ControlAction, type ControlOptions, type Fact, type HubLine, type HubRun, type HubState,
  type Pause as HubPause, type Rule, type RulesDoc, type WriteResult,
} from '../api/hub'
import {
  BY, EVENT, FACT, ago, cronInfo, dur, hm, money, namesOf, sameDay, stateInk, stateTone, tr, until, useHubState, when, STATE,
} from '../lib/hub'
import { Drawer, KV, Live, Problem, Section, btn, field } from '../components/k/HubBits'
import RuleEditor from '../components/k/RuleEditor'
import { Empty } from './DailyView'

const pct = (min: number) => `${(min / 1440) * 100}%`
const minuteOf = (iso: string) => { const d = new Date(iso); return d.getHours() * 60 + d.getMinutes() + d.getSeconds() / 60 }
const trigger = (s: HubState, name: string, types: string[], source: string) => {
  const cron = s.schedules[name]
  if (cron) return cronInfo(cron).text
  const src = s.sources.find((x) => x.id === source)?.name ?? source
  return `${src}：${types.map((t) => tr(EVENT, t)).join('、')}后`
}

function Pill({ state }: { state: string }) {
  return <span className="k-chip" style={stateTone(state)}>{tr(STATE, state)}</span>
}

type OnControl = (name: string, action: ControlAction, opts?: ControlOptions) => void
const pausesOf = (l: HubLine) => l.paused ?? []
/** 你 · 今天 14:05 起 · 到 今天 18:00：单独暂停 / 能力 egress */
const pauseWords = (p: HubPause) =>
  [p.kind === 'rule' ? '单独暂停' : `能力 ${p.name} 暂停`, p.reason, `${tr(BY, p.by)} ${when(p.since)} 起`, p.until ? `到 ${when(p.until)}` : '']
    .filter(Boolean).join(' · ')

function PausedChip({ pauses }: { pauses: HubPause[] }) {
  if (!pauses.length) return null
  return <span className="k-chip shrink-0" style={stateTone('queued')} title={pauses.map(pauseWords).join('\n')}>已暂停</span>
}

const SPANS = [['', '一直到恢复'], ['1h', '1 小时'], ['4h', '4 小时'], ['today', '到今天结束']] as const
function untilOf(span: string) {
  if (!span) return undefined
  if (span === 'today') { const d = new Date(); d.setHours(23, 59, 0, 0); return d.toISOString() }
  return new Date(Date.now() + Number(span.slice(0, -1)) * 3600_000).toISOString()
}

/** Asks why and for how long before a pause: the reason is what the next reader of the page sees. */
function PauseForm({ what, busy, onConfirm, onCancel }: {
  what: string; busy: boolean; onConfirm: (opts: ControlOptions) => void; onCancel: () => void
}) {
  const [reason, setReason] = useState('')
  const [span, setSpan] = useState('')
  return (
    <form className="mt-2 flex flex-wrap items-center gap-2 rounded-tile border border-k-line bg-k-sunk px-3 py-2.5"
      onSubmit={(e) => { e.preventDefault(); onConfirm({ reason: reason.trim(), until: untilOf(span) }) }}>
      <span className="text-[13px] text-k-ink3">暂停{what}：</span>
      <input className={`${field} min-w-[160px] flex-1 py-1.5`} autoFocus maxLength={500} value={reason}
        onChange={(e) => setReason(e.target.value)} placeholder="原因（会显示给看这页的人）" aria-label="暂停原因" />
      <select className={`${field} w-auto py-1.5`} value={span} onChange={(e) => setSpan(e.target.value)} aria-label="暂停多久">
        {SPANS.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
      </select>
      <button className={btn} type="submit" disabled={busy}><Pause className="h-3.5 w-3.5" />确认暂停</button>
      <button className="px-1 text-[13px] text-k-ink4 hover:text-k-ink" type="button" onClick={onCancel}>取消</button>
    </form>
  )
}

/** Every capability some rule requires, paused or not, with a switch for all of its rules at once. */
function Capabilities({ s, onControl, busy }: { s: HubState; onControl: OnControl; busy: string }) {
  const [asking, setAsking] = useState('')
  const caps = [...new Set(s.lines.flatMap((l) => l.requires ?? []))].sort()
  if (!caps.length) {
    return <p className="text-[13px] text-k-ink4">还没有规则写 requires。给依赖同一能力（比如连外网）的规则写上 <code className="k-mono">"requires": ["egress"]</code>，出问题时就能在这里一起暂停。</p>
  }
  return (
    <div className="k-card divide-y divide-k-line-soft">
      {caps.map((cap) => {
        const pause = s.pauses?.find((p) => p.kind === 'requires' && p.name === cap)
        const rules = s.lines.filter((l) => l.requires?.includes(cap)).map((l) => l.name)
        const key = `requires:${cap}`
        return (
          <div key={cap} className="px-5 py-3">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
              <span className="k-mono text-[14px] font-medium text-k-ink">{cap}</span>
              {pause ? <span className="k-chip" style={stateTone('queued')}>已暂停</span> : <span className="k-chip" style={stateTone('done')}>正常</span>}
              <span className="min-w-0 truncate text-[12.5px] text-k-ink4">{rules.length} 条规则：{rules.join('、')}</span>
              <span className="ml-auto flex gap-1.5">
                {pause
                  ? <button className={btn} disabled={busy === key} onClick={() => onControl(cap, 'resume', { scope: 'requires' })}><Play className="h-3.5 w-3.5" />恢复这项能力</button>
                  : <button className={btn} disabled={busy === key || !s.control.available} onClick={() => setAsking(cap)}><Pause className="h-3.5 w-3.5" />一起暂停</button>}
              </span>
            </div>
            {pause && <div className="mt-1 text-[12.5px] text-k-amber-ink">{pauseWords(pause)}</div>}
            {asking === cap && !pause && (
              <PauseForm what={`写了 ${cap} 的 ${rules.length} 条规则`} busy={busy === key}
                onCancel={() => setAsking('')}
                onConfirm={(opts) => { setAsking(''); onControl(cap, 'pause', { ...opts, scope: 'requires' }) }} />
            )}
          </div>
        )
      })}
    </div>
  )
}

/** One lane per rule across today: the planned times as ticks, each run as a dot coloured by its outcome. */
function DayChart({ s, onRun }: { s: HubState; onRun: (id: number) => void }) {
  const now = new Date(), nowMin = now.getHours() * 60 + now.getMinutes()
  const today = s.runs.filter((r) => sameDay(new Date(r.createdAt), now))
  return (
    <div className="k-card overflow-hidden px-4 pb-4 pt-3 sm:px-6">
      <div className="grid grid-cols-1 gap-x-4 sm:grid-cols-[minmax(0,200px)_minmax(0,1fr)]">
        <div className="hidden sm:block" />
        <div className="relative h-6 text-[11.5px] text-k-ink4">
          {[0, 3, 6, 9, 12, 15, 18, 21, 24].map((h) => (
            <span key={h} className="k-mono absolute -translate-x-1/2" style={{ left: pct(h * 60) }}>{String(h).padStart(2, '0')}</span>
          ))}
        </div>
        {s.lines.map((l) => {
          const cron = s.schedules[l.name] ? cronInfo(s.schedules[l.name]) : null
          return (
            <div key={l.id} className="contents">
              <div className="min-w-0 pt-3 sm:py-2">
                <div className="flex items-center gap-2 truncate text-[14px] font-medium text-k-ink">
                  <span className="truncate">{l.name}</span>
                  <PausedChip pauses={pausesOf(l)} />
                </div>
                <div className="truncate text-[12px] text-k-ink4">{trigger(s, l.name, l.types, l.source)}</div>
              </div>
              <div className="relative my-1.5 h-8 rounded-control border border-k-line-soft bg-k-sunk sm:my-2">
                {[3, 6, 9, 12, 15, 18, 21].map((h) => <span key={h} className="absolute inset-y-0 w-px bg-k-line-soft" style={{ left: pct(h * 60) }} />)}
                {cron?.minutes.map((m) => (
                  <span key={m} title={`计划 ${cron.text}`} className="absolute inset-y-1 w-[3px] -translate-x-1/2 rounded-full"
                    style={{ left: pct(m), background: 'color-mix(in srgb, var(--k-ink-4) 55%, transparent)' }} />
                ))}
                <span className="absolute inset-y-0 w-0 border-l-[1.5px] border-dashed" style={{ left: pct(nowMin), borderColor: 'var(--k-hot)' }} />
                {today.filter((r) => r.rule === l.name).map((r) => (
                  <button key={r.id} onClick={() => onRun(r.id)} title={`#${r.id} ${hm(r.createdAt)} ${tr(STATE, r.state)}`}
                    aria-label={`第 ${r.id} 轮 ${hm(r.createdAt)} ${tr(STATE, r.state)}`}
                    className="absolute top-1/2 h-3.5 w-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full ring-2 ring-k-surface transition-transform hover:scale-125"
                    style={{ left: pct(minuteOf(r.createdAt)), background: stateInk(r.state) }} />
                ))}
              </div>
            </div>
          )
        })}
      </div>
      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-k-ink4">
        <span className="inline-flex items-center gap-1.5"><i className="inline-block h-3 w-[3px] rounded-full" style={{ background: 'color-mix(in srgb, var(--k-ink-4) 55%, transparent)' }} />计划时间</span>
        {(['done', 'claimed', 'nothing', 'failed'] as const).map((st) => (
          <span key={st} className="inline-flex items-center gap-1.5"><i className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: stateInk(st) }} />{tr(STATE, st)}</span>
        ))}
        <span className="inline-flex items-center gap-1.5"><i className="inline-block h-3 w-0 border-l-[1.5px] border-dashed" style={{ borderColor: 'var(--k-hot)' }} />现在</span>
      </div>
    </div>
  )
}

function RulesList({ s, doc, onEdit, onControl, busy }: {
  s: HubState; doc: RulesDoc | null; onEdit: (r: Rule) => void; onControl: OnControl; busy: string
}) {
  const [asking, setAsking] = useState('')
  const latest = (name: string) => s.runs.find((r) => r.rule === name)
  const ruleNamed = (name: string) => doc?.data?.rules.find((r) => r.name === name)
  const ready = s.control.available
  const why = ready ? undefined : s.control.reason ?? undefined
  return (
    <div className="k-card divide-y divide-k-line-soft">
      <div className="hidden grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)_minmax(0,1.3fr)_80px_340px] gap-4 px-5 py-2.5 text-[12.5px] text-k-ink4 lg:grid">
        <span>规则 · 触发方式</span><span>下次运行</span><span>最近一轮</span><span className="text-right">累计花费</span><span />
      </div>
      {s.lines.map((l) => {
        const run = latest(l.name)
        const pauses = pausesOf(l)
        const own = pauses.find((p) => p.kind === 'rule')
        const rule = ruleNamed(l.name)
        const scheduled = !!s.schedules[l.name]
        const requires = l.requires ?? rule?.requires ?? []
        return (
          <div key={l.id} className="grid grid-cols-1 gap-x-4 gap-y-1.5 px-5 py-3 lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)_minmax(0,1.3fr)_80px_340px] lg:items-center">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span className="truncate text-[14.5px] font-medium text-k-ink">{l.name}</span>
                <span className="k-chip plain shrink-0" style={scheduled ? undefined : { ['--c' as string]: 'var(--k-violet)', ['--cs' as string]: 'var(--k-violet-soft)' }}>
                  {scheduled ? '定时' : '事件'}
                </span>
                <PausedChip pauses={pauses} />
              </div>
              <div className="truncate text-[12.5px] text-k-ink4">{trigger(s, l.name, l.types, l.source)} · {l.agent}</div>
              {requires.length > 0 && (
                <div className="mt-1 flex flex-wrap gap-1">
                  {requires.map((c) => <span key={c} className="k-chip plain k-mono text-[11.5px]" title="这条规则依赖的能力（requires）">需要 {c}</span>)}
                </div>
              )}
              {pauses.map((p) => <div key={`${p.kind}:${p.name}`} className="mt-1 text-[12.5px] text-k-amber-ink">{pauseWords(p)}</div>)}
            </div>
            <div className="text-[13.5px] text-k-ink2">
              <span className="text-k-ink4 lg:hidden">下次：</span>
              {pauses.length && l.next ? <span className="text-k-ink4">暂停中，到点跳过</span>
                : l.next ? <>{when(l.next)} <span className="text-k-ink4">（{until(l.next)}）</span></>
                  : <span className="text-k-ink4">{pauses.length ? '暂停中，事件不接' : '等事件'}</span>}
            </div>
            <div className="flex min-w-0 items-center gap-2 text-[13.5px]">
              <span className="text-k-ink4 lg:hidden">最近：</span>
              {run ? <><Pill state={run.state} /><span className="truncate text-k-ink3">#{run.id} · {when(run.createdAt)}</span></> : <span className="text-k-ink4">还没跑过</span>}
            </div>
            <div className="k-num text-[13.5px] text-k-ink2 lg:text-right"><span className="text-k-ink4 lg:hidden">累计花费：</span>{money(l.totals.usage.cost)}</div>
            <div className="flex flex-wrap gap-1.5 lg:flex-nowrap lg:justify-end">
              <button className={btn} disabled={!rule} onClick={() => rule && onEdit(rule)} title={rule ? undefined : 'rules.json 里没找到这条'}>
                <Pencil className="h-3.5 w-3.5" />编辑
              </button>
              {own
                ? <button className={btn} disabled={busy === l.name} onClick={() => onControl(l.name, 'resume')} title="恢复后不补跑暂停期间错过的时间"><Play className="h-3.5 w-3.5" />恢复</button>
                : <button className={btn} disabled={busy === l.name || !ready} onClick={() => setAsking(l.name)} title={why}><Pause className="h-3.5 w-3.5" />暂停</button>}
              <button className={btn} disabled={busy === l.name || !ready || pauses.length > 0} onClick={() => onControl(l.name, 'run')}
                title={why ?? (pauses.length ? '暂停中的规则不能立即运行，先恢复' : scheduled ? '现在跑一轮，照常排队' : '用一条手动事件跑一轮')}>
                <Play className="h-3.5 w-3.5" />立即运行
              </button>
            </div>
            {asking === l.name && !own && (
              <div className="lg:col-span-5">
                <PauseForm what={`「${l.name}」`} busy={busy === l.name} onCancel={() => setAsking('')}
                  onConfirm={(opts) => { setAsking(''); onControl(l.name, 'pause', opts) }} />
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

function RunsList({ runs, onRun }: { runs: HubRun[]; onRun: (id: number) => void }) {
  return (
    <div className="k-card divide-y divide-k-line-soft">
      {runs.slice(0, 20).map((r) => {
        const text = r.error || r.summary || r.events.map((e) => e.summary).filter(Boolean).join('；') || '—'
        const meta = [when(r.createdAt), dur(r.claimedAt, r.finishedAt), r.usage?.cost ? money(r.usage.cost) : ''].filter(Boolean).join(' · ')
        return (
          <button key={r.id} onClick={() => onRun(r.id)} className="grid w-full grid-cols-[40px_minmax(0,1fr)_auto] items-start gap-3 px-4 py-3 text-left hover:bg-k-sunk sm:px-5">
            <span className="k-mono pt-0.5 text-[12.5px] text-k-ink4">#{r.id}</span>
            <span className="min-w-0">
              <span className="flex items-center gap-2">
                <span className="truncate text-[14.5px] font-medium text-k-ink">{r.rule}</span>
                {r.manual && <span className="k-chip plain shrink-0 text-[11.5px]" title="本人点了立即运行">手动</span>}
              </span>
              <span className={`line-clamp-2 text-[13px] ${r.error ? 'text-k-hot' : 'text-k-ink3'}`}>{text}</span>
            </span>
            <span className="flex flex-col items-end gap-1">
              <Pill state={r.state} />
              <span className="k-num whitespace-nowrap text-[12px] text-k-ink4">{meta}</span>
            </span>
          </button>
        )
      })}
      {!runs.length && <p className="px-5 py-6 text-center text-[14px] text-k-ink4">还没有运行记录</p>}
    </div>
  )
}

function RunDrawer({ id, s, onClose }: { id: number; s: HubState; onClose: () => void }) {
  const [facts, setFacts] = useState<Fact[] | null>(null)
  const [problem, setProblem] = useState<HubApiError | null>(null)
  const r = s.runs.find((x) => x.id === id)
  const names = namesOf(s)
  useEffect(() => {
    setFacts(null)
    hub.why(id).then(setFacts).catch((e) => setProblem(e instanceof HubApiError ? e : new HubApiError(0, String(e))))
  }, [id])
  const steps = (facts ?? []).filter((f) => f.type !== 'rules' && f.type !== 'heartbeat')
  const beats = (facts ?? []).filter((f) => f.type === 'heartbeat').length
  return (
    <Drawer title={`第 ${id} 轮${r ? ` · ${r.rule}` : ''}`} sub={r ? when(r.createdAt) : undefined} onClose={onClose}>
      {r && (
        <>
          <KV rows={[
            ['状态', <span className="inline-flex items-center gap-2"><Pill state={r.state} />{r.manual && <span className="k-chip plain">手动触发</span>}</span>],
            ['谁做的', names[r.agent] ?? r.agent],
            ['时间', <span className="k-num">{when(r.createdAt)}{r.finishedAt && ` → ${when(r.finishedAt)}（${dur(r.claimedAt, r.finishedAt)}）`}</span>],
            ['花费', r.usage?.cost ? money(r.usage.cost) : null],
            ['重试', r.attempts ? `${r.attempts} 次` : null],
            ['因为', r.events.length ? r.events.map((e, i) => <div key={i}>{names[e.source] ?? e.source}：{tr(EVENT, e.type)}{e.summary && `（${e.summary}）`}</div>) : null],
            ['会话', r.link ? <a href={r.link} className="break-all text-k-accent">{r.session ?? r.link}</a> : null],
          ]} />
          {r.summary && <><h3 className="mb-1.5 mt-5 text-[13px] font-semibold text-k-ink3">结论</h3><pre className="whitespace-pre-wrap break-words rounded-tile bg-k-sunk p-3 text-[13.5px] leading-[1.7] text-k-ink2">{r.summary}</pre></>}
          {r.error && <><h3 className="mb-1.5 mt-5 text-[13px] font-semibold text-k-hot">出错</h3><pre className="whitespace-pre-wrap break-words rounded-tile p-3 text-[13.5px] leading-[1.7] text-k-hot" style={{ background: 'var(--k-hot-soft)' }}>{r.error}</pre></>}
        </>
      )}
      <h3 className="mb-2 mt-6 text-[13px] font-semibold text-k-ink3">经过</h3>
      {problem && <Problem message={problem.message} fromHub={problem.fromHub} />}
      {!facts && !problem && <p className="text-[14px] text-k-ink4">读取中…</p>}
      {facts && (
        <ol className="relative ml-1 border-l border-k-line-strong pl-5">
          {steps.map((f) => (
            <li key={f.seq} className="relative py-1.5 text-[14px] text-k-ink2">
              <span className="absolute -left-[25px] top-[13px] h-2 w-2 rounded-full ring-4 ring-k-bg" style={{ background: ['finish', 'started'].includes(f.type) ? 'var(--k-accent)' : 'var(--k-line-strong)' }} />
              <time className="k-mono mr-3 text-[12.5px] text-k-ink4">{new Date(f.at).toLocaleTimeString('zh-CN', { hour12: false })}</time>{tr(FACT, f.type)}
            </li>
          ))}
        </ol>
      )}
      {beats > 0 && <p className="mt-2 text-[13px] text-k-ink4">运行中报了 {beats} 次心跳</p>}
      {facts && (
        <details className="mt-4">
          <summary className="cursor-pointer text-[13px] text-k-ink4">原始记录（JSON）</summary>
          <pre className="k-mono mt-2 max-h-[420px] overflow-auto rounded-tile border border-k-line bg-k-sunk p-3 text-[12px] leading-[1.55] text-k-ink2">{JSON.stringify(facts, null, 1)}</pre>
        </details>
      )}
    </Drawer>
  )
}

function HistoryDrawer({ doc, onRolledBack, onClose }: { doc: RulesDoc; onRolledBack: (r: WriteResult) => void; onClose: () => void }) {
  const [entries, setEntries] = useState<AuditEntry[] | null>(null)
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<HubApiError | null>(null)
  useEffect(() => { hub.audit().then((r) => setEntries(r.entries)).catch(() => setEntries([])) }, [])
  const roll = async (backup: string) => {
    if (confirm !== backup) { setConfirm(backup); return }
    setBusy(true)
    setProblem(null)
    try { onRolledBack(await hub.rollback(doc.version, backup)) } catch (e) {
      setProblem(e instanceof HubApiError ? e : new HubApiError(0, String(e)))
    } finally { setBusy(false); setConfirm('') }
  }
  const previous = doc.backups.find((b) => b.ours)?.name
  const label = (name: string) => name.replace(/^rules\.json\.bak-/, '').replace(/^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})/, '$1-$2-$3 $4:$5:$6')
  return (
    <Drawer wide title="改动记录与回滚" sub={doc.file} onClose={onClose}>
      {problem && <div className="mb-4"><Problem message={problem.message} issues={problem.issues} /></div>}
      <h3 className="text-[15px] font-[650] text-k-ink">备份</h3>
      <p className="mt-1 text-[13px] text-k-ink4">回滚同样先校验；回滚前的文件也会再备份一份，所以回滚本身也能撤销。</p>
      <div className="mt-3 divide-y divide-k-line-soft rounded-tile border border-k-line">
        {doc.backups.map((b) => (
          <div key={b.name} className="flex flex-wrap items-center gap-3 px-4 py-2.5">
            <span className="k-mono text-[13px] text-k-ink2">{label(b.name)}</span>
            {b.ours && b.name === previous && <span className="k-chip plain">上一版</span>}
            {!b.ours && <span className="k-chip plain" style={stateTone('nothing')}>不是网页留的</span>}
            <span className="text-[12px] text-k-ink4">{(b.size / 1024).toFixed(1)} KB</span>
            <button className={`${btn} ml-auto`} disabled={busy} onClick={() => roll(b.name)}>
              <RotateCcw className="h-3.5 w-3.5" />{confirm === b.name ? '再点一次确认回滚' : '回滚到这一版'}
            </button>
          </div>
        ))}
        {!doc.backups.length && <p className="px-4 py-4 text-[13.5px] text-k-ink4">还没有备份：第一次保存时会生成。</p>}
      </div>

      <h3 className="mt-8 text-[15px] font-[650] text-k-ink">改动记录</h3>
      {!entries && <p className="mt-2 text-[14px] text-k-ink4">读取中…</p>}
      {entries && !entries.length && <p className="mt-2 text-[14px] text-k-ink4">还没有通过网页改过规则。</p>}
      <div className="mt-3 space-y-3">
        {entries?.map((e, i) => {
          const c = e.changes
          const what = [
            c.added.length ? `新增 ${c.added.join('、')}` : '',
            c.removed.length ? `删除 ${c.removed.join('、')}` : '',
            ...Object.entries(c.changed).map(([n, fs]) => `「${n}」改了 ${fs.join('、')}`),
            c.sections.length ? `另改了 ${c.sections.join('、')}` : '',
          ].filter(Boolean).join('；') || '内容没变'
          return (
            <div key={i} className="rounded-tile border border-k-line bg-k-surface px-4 py-3">
              <div className="flex flex-wrap items-baseline gap-x-3 text-[13px] text-k-ink4">
                <span className="k-mono">{new Date(e.at).toLocaleString('zh-CN', { hour12: false })}</span>
                <span>{e.by}</span><span className="k-chip plain">{e.note || e.action}</span>
              </div>
              <div className="mt-1 text-[14px] text-k-ink2">{what}</div>
              {e.diff && (
                <details className="mt-1.5">
                  <summary className="cursor-pointer text-[12.5px] text-k-ink4">看 diff · 备份 {e.backup}</summary>
                  <pre className="k-mono mt-2 max-h-[360px] overflow-auto rounded-tile bg-k-sunk p-3 text-[12px] leading-[1.55]">
                    {e.diff.split('\n').map((ln, j) => (
                      <div key={j} style={{ color: ln.startsWith('+') && !ln.startsWith('+++') ? 'var(--k-ok)' : ln.startsWith('-') && !ln.startsWith('---') ? 'var(--k-hot)' : 'var(--k-ink-3)' }}>{ln || ' '}</div>
                    ))}
                  </pre>
                </details>
              )}
            </div>
          )
        })}
      </div>
    </Drawer>
  )
}

export default function RulesRunsView() {
  const { state: s, error, at, refresh } = useHubState()
  const [doc, setDoc] = useState<RulesDoc | null>(null)
  const [docError, setDocError] = useState('')
  const [run, setRun] = useState<number | null>(null)
  const [editing, setEditing] = useState<{ rule: Rule | null } | null>(null)
  const [history, setHistory] = useState(false)
  const [notice, setNotice] = useState<{ ok: boolean; text: string; issues?: string[]; fromHub?: boolean } | null>(null)
  const [busy, setBusy] = useState('')

  const loadDoc = useCallback(() => hub.rules().then((d) => { setDoc(d); setDocError('') }).catch((e) => setDocError(String(e.message ?? e))), [])
  useEffect(() => { loadDoc() }, [loadDoc])

  const saved = (r: WriteResult, what: string) => {
    setEditing(null)
    setHistory(false)
    setNotice({ ok: true, text: `${what}。旧文件备份为 ${r.backup}，中台会自己读到新规则。` })
    loadDoc()
    setTimeout(refresh, 1500)
  }
  const control: OnControl = async (name, action, opts = {}) => {
    const cap = opts.scope === 'requires'
    setBusy(cap ? `requires:${name}` : name)
    try {
      const r = await hub.control(name, action, opts)
      const what = cap ? `能力 ${name}` : `「${name}」`
      const still = (r.stillPaused as string[] | undefined)?.length ? `；还停着（单独暂停的）：${(r.stillPaused as string[]).join('、')}` : ''
      const held = !cap && action === 'resume' && (r.paused as HubPause[] | undefined)?.length ? '；它还被能力暂停着' : ''
      const text = action === 'run' ? `已排上「${name}」第 ${r.run} 轮（手动）`
        : action === 'pause' ? `已暂停${what}${cap ? `（${(r.rules as string[]).join('、')}）` : ''}`
          : `已恢复${what}，错过的不补跑${still}${held}`
      setNotice({ ok: true, text })
      refresh()
    } catch (e) {
      const err = e instanceof HubApiError ? e : new HubApiError(0, String(e))
      setNotice({ ok: false, text: err.message, issues: err.issues, fromHub: err.fromHub })
    } finally { setBusy('') }
  }

  const agentsOnline = useMemo(() => s ? s.agents.filter((a) => a.online).length : 0, [s])

  if (!s) {
    return (
      <div className="mx-auto w-full max-w-[1180px] pb-16">
        <h1 className="text-[28px] font-[650] text-k-ink">规则与运行</h1>
        {error ? <div className="mt-6"><Problem message={`读不到中台：${error}`} /></div> : <Empty>读取中…</Empty>}
      </div>
    )
  }
  const sp = s.spending
  return (
    <div className="mx-auto w-full max-w-[1180px] pb-16">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-2">
        <h1 className="text-[28px] font-[650] tracking-[0.01em] text-k-ink">规则与运行</h1>
        <span className="text-[15px] text-k-ink3">{s.lines.length} 条规则 · 今天做完 {s.totals.doneToday} 轮</span>
        <span className="ml-auto"><Live at={at} error={error} /></span>
      </div>

      {notice && <div className="mt-5">{notice.ok
        ? <div className="flex items-center gap-2 rounded-tile px-4 py-2.5 text-[14px] text-k-ok" style={{ background: 'var(--k-ok-soft)' }}>{notice.text}<button className="ml-auto text-[12.5px] text-k-ink4" onClick={() => setNotice(null)}>关闭</button></div>
        : <Problem message={notice.text} issues={notice.issues} fromHub={notice.fromHub} onClose={() => setNotice(null)} />}
      </div>}

      <Section title="24 小时运行图" aside="每条规则一道：竖线是计划时间，圆点是实际运行，点开看这一轮">
        <DayChart s={s} onRun={setRun} />
      </Section>

      {!s.control.available && s.control.reason && (
        <div className="mt-5 rounded-tile border border-k-line px-4 py-2.5 text-[13.5px] text-k-ink3">{s.control.reason}</div>
      )}

      <Section title="全部规则" aside={
        <span className="flex flex-wrap gap-2">
          <button className={btn} disabled={!doc?.data} onClick={() => setEditing({ rule: null })}><Plus className="h-3.5 w-3.5" />新建规则</button>
          <button className={btn} disabled={!doc} onClick={() => setHistory(true)}><History className="h-3.5 w-3.5" />改动记录与回滚{doc?.backups.length ? <small className="k-mono text-k-ink4">{doc.backups.length}</small> : null}</button>
        </span>
      }>
        {docError && <div className="mb-3"><Problem message={`读不到规则文件：${docError}`} /></div>}
        <RulesList s={s} doc={doc} onEdit={(rule) => setEditing({ rule })} onControl={control} busy={busy} />
        {s.elsewhere.length > 0 && (
          <p className="mt-2 text-[12.5px] text-k-ink4">别处定时跑的：{s.elsewhere.map((e) => `${e.name}（${e.where}，${e.every}）`).join('；')}</p>
        )}
      </Section>

      <Section title="按能力暂停" aside="暂停一项能力，写了它的规则一起停；和单条暂停分开算，恢复能力不会恢复单独暂停的规则">
        <Capabilities s={s} onControl={control} busy={busy} />
        {!!s.skipped?.length && (
          <p className="mt-2 text-[12.5px] text-k-ink4">暂停期间没接的事件（最近）：{s.skipped.slice(0, 5).map((e) => `${when(e.receivedAt)} ${tr(EVENT, e.type)} → ${e.rules.join('、')}`).join('；')}</p>
        )}
      </Section>

      <div className="grid gap-x-8 lg:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
        <Section title="最近几轮" aside={`共显示 ${Math.min(20, s.runs.length)} 轮`}>
          <RunsList runs={s.runs} onRun={setRun} />
        </Section>
        <div className="min-w-0">
          <Section title="Agent 和通道" aside={`${agentsOnline}/${s.agents.length} 在线`}>
            <div className="k-card grid grid-cols-1 gap-x-4 px-5 py-2 sm:grid-cols-2 lg:grid-cols-1 xl:grid-cols-2">
              {[...s.agents.map((a) => ({ ...a, kind: a.use })), ...s.channels.map((c) => ({ ...c, kind: '通道' }))].map((a) => (
                <div key={`${a.kind}:${a.id}`} className="flex min-w-0 items-center gap-2.5 py-2" title={a.seenAt ? `上次联系 ${ago(a.seenAt)}` : '还没联系过'}>
                  <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: a.online ? 'var(--k-ok)' : 'var(--k-ink-4)' }} />
                  <span className="truncate text-[14px] text-k-ink2">{a.name}</span>
                  <span className="ml-auto shrink-0 text-[12px] text-k-ink4">{a.online ? '在线' : a.seenAt ? ago(a.seenAt) : '未连接'}</span>
                </div>
              ))}
            </div>
          </Section>
          <Section title="花费">
            <div className="k-card grid grid-cols-2 gap-4 px-5 py-4">
              {([['今天', sp.today], ['近 7 天', sp.week]] as const).map(([k, v]) => (
                <div key={k}>
                  <div className="text-[12.5px] text-k-ink4">{k}</div>
                  <div className="k-num mt-1 text-[22px] font-bold text-k-ink">{money(v.cost)}</div>
                  <div className="text-[12px] text-k-ink4">{v.rounds} 轮 · {(v.tokens / 1000).toFixed(1)}k tokens{v.missingCost ? ` · ${v.missingCost} 轮没报` : ''}</div>
                </div>
              ))}
            </div>
          </Section>
        </div>
      </div>

      {run !== null && <RunDrawer id={run} s={s} onClose={() => setRun(null)} />}
      {editing && doc && (
        <RuleEditor doc={doc} original={editing.rule} onClose={() => setEditing(null)}
          onSaved={(r) => saved(r, editing.rule ? `已保存「${editing.rule.name}」` : '已新建规则')} />
      )}
      {history && doc && <HistoryDrawer doc={doc} onClose={() => setHistory(false)} onRolledBack={(r) => saved(r, `已回滚（${r.audit.note}）`)} />}
    </div>
  )
}
