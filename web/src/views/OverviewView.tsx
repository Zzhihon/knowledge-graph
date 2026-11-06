// 总览: the first screen. From the top: what needs you, the health strip, today, in progress and
// recent artifacts. Every block comes from /api/overview and fails on its own, with its reason.
import type { CSSProperties, ReactNode } from 'react'
import { ArrowRight, ArrowUpRight } from 'lucide-react'
import { Section } from '../components/k/HubBits'
import { Empty } from './DailyView'
import { money } from '../lib/hub'
import {
  agoUnix, fetchOverviewPage, issueUrl, usePoll,
  type ArtifactCard, type AttentionItem, type Block, type Failed, type Overview, type Pill, type ProjectCard,
} from '../lib/overview'

type Nav = (id: string) => void
const vars = (c: string, cs: string): CSSProperties => ({ ['--c' as string]: `var(${c})`, ['--cs' as string]: `var(${cs})` })

const URGENCY: Record<AttentionItem['urgency'], { label: string; c: string; cs: string }> = {
  critical: { label: '严重事故', c: '--k-hot', cs: '--k-hot-soft' },
  card: { label: '待处理卡片', c: '--k-accent', cs: '--k-accent-soft' },
  warning: { label: '事故', c: '--k-amber-ink', cs: '--k-amber-soft' },
  info: { label: '提示', c: '--k-ink-4', cs: '--k-line-soft' },
  session: { label: '等你回答', c: '--k-violet', cs: '--k-violet-soft' },
}
const SOURCE: Record<string, string> = { hub: '中台', incidents: '状态库', sessions: '会话看板' }

function Reason({ children }: { children: ReactNode }) {
  return (
    <div role="alert" className="rounded-tile border px-4 py-2.5 text-[13.5px] text-k-hot"
      style={{ background: 'var(--k-hot-soft)', borderColor: 'color-mix(in srgb, var(--k-hot) 30%, transparent)' }}>
      {children}
    </div>
  )
}

export function PlannedNote({ issue, children }: { issue: number; children?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1 rounded-card border border-dashed border-k-line-strong px-5 py-4 text-[14px] text-k-ink4">
      <a href={issueUrl(issue)} target="_blank" rel="noopener noreferrer"
        className="k-chip hover:opacity-80" style={vars('--k-ink-4', '--k-line-soft')}>规划中 #{issue}</a>
      {children}
    </div>
  )
}

const failed = <T,>(b: Block<T>): b is Failed => !b.ok

function Attention({ data, onNavigate }: { data: Overview['attention']; onNavigate: Nav }) {
  const open = (x: AttentionItem) => onNavigate(x.kind === 'card' ? 'hub' : x.kind === 'incident' ? 'incidents' : 'inprogress')
  return (
    <Section title="需要你处理" aside={data.count ? `${data.count} 件` : undefined} className="!mt-6">
      <div className="space-y-2">
        {Object.entries(data.errors).map(([k, v]) => <Reason key={k}>读不到{SOURCE[k] ?? k}：{v}</Reason>)}
        {data.items.length > 0 ? (
          <ol className="k-card divide-y divide-k-line-soft overflow-hidden">
            {data.items.slice(0, 8).map((x, i) => {
              const u = URGENCY[x.urgency]
              return (
                <li key={`${x.kind}:${x.id}`}>
                  <button onClick={() => open(x)}
                    className="relative grid w-full grid-cols-[22px_minmax(0,1fr)_auto] items-baseline gap-x-3 px-4 py-3 text-left hover:bg-k-sunk sm:px-5">
                    <span aria-hidden className="absolute inset-y-0 left-0 w-[3px]" style={{ background: `var(${u.c})` }} />
                    <span className="k-num text-[13px] text-k-ink4">{i + 1}</span>
                    <span className="min-w-0">
                      <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                        <span className="k-chip" style={vars(u.c, u.cs)}>{x.kind === 'card' ? (x.ask ? '要你选' : '要你留意') : u.label}</span>
                        <span className="min-w-0 truncate text-[15px] font-[600] text-k-ink">{x.title}</span>
                      </span>
                      <span className="mt-0.5 block truncate text-[12.5px] text-k-ink4">
                        {x.kind === 'card' ? `#${x.id} · ${x.sub}` : x.sub}
                      </span>
                    </span>
                    <span className="whitespace-nowrap text-[12.5px] text-k-ink4">{agoUnix(x.ts)}</span>
                  </button>
                </li>
              )
            })}
          </ol>
        ) : !Object.keys(data.errors).length && (
          <p className="k-card px-6 py-6 text-center text-[14.5px] text-k-ink4">没有要你处理的事。</p>
        )}
        {data.count > 8 && (
          <button onClick={() => onNavigate('hub')} className="inline-flex items-center gap-1 text-[13px] text-k-ink3 hover:text-k-accent">
            另外 {data.count - 8} 件<ArrowRight className="h-3.5 w-3.5" />
          </button>
        )}
      </div>
    </Section>
  )
}

const PILL_TONE: Record<Pill['status'], [string, string]> = {
  up: ['--k-ok', '--k-ok-soft'],
  down: ['--k-hot', '--k-hot-soft'],
  asleep: ['--k-violet', '--k-violet-soft'],
  unknown: ['--k-ink-4', '--k-line-soft'],
  planned: ['--k-ink-4', '--k-line-soft'],
  stale: ['--k-amber-ink', '--k-amber-soft'],
}
const PILL_PAGE: Record<string, string> = { egress: 'egress', services: 'services', hub: 'hub', index: 'library' }

function HealthPill({ p, onNavigate }: { p: Pill; onNavigate: Nav }) {
  const [c, cs] = PILL_TONE[p.status]
  const av = p.availability
  return (
    <button onClick={() => onNavigate(PILL_PAGE[p.id] ?? 'overview')}
      className="k-card k-card-hover flex min-w-0 flex-col gap-1.5 px-4 py-3 text-left">
      <span className="flex items-center gap-2">
        <span className="truncate text-[14px] font-[600] text-k-ink">{p.label}</span>
        <span className="k-chip ml-auto shrink-0" style={vars(c, cs)}>
          {p.text}
        </span>
      </span>
      <span className="text-[12.5px] text-k-ink4">
        {p.status === 'planned' ? '模块还没上线' : p.note ? p.note
          : p.id === 'index' ? <>上次成功 <span className="k-num text-k-ink2">{agoUnix(p.last) || '—'}</span>
            {' · '}待更新 <span className="k-num text-k-ink2">{p.pending ?? '—'}</span> 个文件</>
          : <>24 小时 <span className="k-num text-k-ink2">{av === null || av === undefined ? '—' : `${(av * 100).toFixed(av === 1 ? 0 : 1)}%`}</span>
            {p.count ? ` · ${p.count} 项` : ''}</>}
      </span>
      {p.down && p.down.length > 0 && <span className="truncate text-[12.5px] text-k-hot" title={p.down.join('、')}>{p.down.join('、')}</span>}
      {p.reason && <span className="line-clamp-2 break-words text-[12.5px] text-k-ink4" title={p.reason}>{p.reason}</span>}
      {p.stale && <span className="text-[12.5px] font-medium text-k-amber-ink">数据可能过期{p.last ? `（上次 ${agoUnix(p.last)}）` : ''}</span>}
    </button>
  )
}

function Health({ data, onNavigate }: { data: Overview['health']; onNavigate: Nav }) {
  return (
    <Section title="健康" aside="出口、本地服务、索引、自动化中台">
      {failed(data) ? <Reason>读不到状态库：{data.error}</Reason> : (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {data.pills.map((p) => <HealthPill key={p.id} p={p} onNavigate={onNavigate} />)}
        </div>
      )}
    </Section>
  )
}

function Tile({ label, onClick, children }: { label: string; onClick: () => void; children: ReactNode }) {
  return (
    <button onClick={onClick} className="k-card k-card-hover min-w-0 px-5 py-4 text-left">
      <div className="text-[12.5px] text-k-ink4">{label}</div>
      <div className="mt-1.5">{children}</div>
    </button>
  )
}
const Big = ({ children, unit }: { children: ReactNode; unit?: string }) => (
  <span className="inline-flex items-baseline gap-1.5">
    <span className="k-num text-[24px] font-bold leading-none tracking-[-0.02em] text-k-ink">{children}</span>
    {unit && <span className="text-[13px] text-k-ink4">{unit}</span>}
  </span>
)
const Bad = ({ why }: { why: string }) => <span className="line-clamp-2 break-words text-[13px] text-k-hot" title={why}>{why}</span>

function Today({ o, onNavigate }: { o: Overview; onNavigate: Nav }) {
  const { daily, automation, fresh } = o
  return (
    <Section title="今天">
      <div className="grid gap-3 md:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,1fr)]">
        <Tile label={failed(daily) || !daily.date || daily.today ? '今天的日报' : `今天的日报还没出 · 最近一份 ${daily.date}`} onClick={() => onNavigate('daily')}>
          {failed(daily) ? <Bad why={`读不到日报：${daily.error}`} />
            : <p className="line-clamp-3 text-[15px] leading-[1.7] text-k-ink2">{daily.lead || (daily.date ? '这份日报没有一句话摘要' : '还没有日报')}</p>}
        </Tile>
        <Tile label="自动化今天" onClick={() => onNavigate('rules')}>
          {failed(automation) ? <Bad why={automation.error} /> : (
            <span className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
              <Big unit="轮">{automation.rounds}</Big>
              <Big unit="花费">{money(automation.cost)}</Big>
              {automation.running > 0 && <span className="text-[12.5px] text-k-accent">{automation.running} 轮在跑</span>}
            </span>
          )}
        </Tile>
        <Tile label="今天新沉淀" onClick={() => onNavigate('library')}>
          {failed(fresh) ? <Bad why={`数不了：${fresh.error}`} /> : <Big unit="条">{fresh.count}</Big>}
        </Tile>
      </div>
    </Section>
  )
}

const STAGE_TONE: Record<string, [string, string]> = {
  waiting: ['--k-violet', '--k-violet-soft'],
  active: ['--k-ok', '--k-ok-soft'],
  stalled: ['--k-amber-ink', '--k-amber-soft'],
}

function ProjectRow({ p, onNavigate }: { p: ProjectCard; onNavigate: Nav }) {
  const [c, cs] = STAGE_TONE[p.stage] ?? ['--k-ink-4', '--k-line-soft']
  return (
    <li>
      <button onClick={() => onNavigate('inprogress')} title={p.reason}
        className="flex w-full min-w-0 flex-col gap-1 px-4 py-3 text-left hover:bg-k-sunk sm:px-5">
        <span className="flex min-w-0 items-center gap-2">
          <span className="min-w-0 truncate text-[15px] font-[600] text-k-ink">{p.name}</span>
          <span className="k-chip shrink-0" style={vars(c, cs)}>{p.stage_label || p.stage}</span>
          <span className="ml-auto shrink-0 whitespace-nowrap text-[12.5px] text-k-ink4">{agoUnix(p.last_action)}</span>
        </span>
        <span className="line-clamp-2 break-words text-[13.5px] leading-[1.6] text-k-ink2">
          {p.conclusion || <span className="text-k-ink4">还没有结论</span>}
        </span>
        <span className="flex flex-wrap gap-x-3 gap-y-0.5 text-[12.5px] text-k-ink4">
          {p.dirty > 0 ? <span className="text-k-amber-ink">{p.dirty} 处未提交改动</span> : <span>改动都已提交</span>}
          {!!p.open_prs && <span>{p.open_prs} 个未合并 PR</span>}
          {p.waiting > 0 && <span className="text-k-violet">{p.waiting} 个会话等你</span>}
        </span>
      </button>
    </li>
  )
}

function InProgress({ data, onNavigate }: { data: Overview['inprogress']; onNavigate: Nav }) {
  return (
    <Section title="进行中" aside={failed(data) ? undefined : (
      <>共 {data.count} 个项目{data.stale && <span className="ml-2 font-medium text-k-amber-ink">
        数据可能过期{data.collected_at ? `（上次 ${agoUnix(data.collected_at)}）` : ''}</span>}</>
    )}>
      {failed(data) ? <Reason>读不到会话看板：{data.error}</Reason>
        : data.items.length === 0 ? <p className="k-card px-6 py-6 text-center text-[14.5px] text-k-ink4">最近没有活跃的项目。</p>
          : (
            <ol className="k-card divide-y divide-k-line-soft overflow-hidden">
              {data.items.map((p) => <ProjectRow key={p.id} p={p} onNavigate={onNavigate} />)}
            </ol>
          )}
    </Section>
  )
}

const VISIBILITY: Record<string, { label: string; c: string; cs: string }> = {
  public: { label: '公开', c: '--k-hot', cs: '--k-hot-soft' },
  private: { label: '私有', c: '--k-violet', cs: '--k-violet-soft' },
  local: { label: '仅本机', c: '--k-ink-3', cs: '--k-sunk' },
}
const ADAPTER: Record<string, string> = { orca: 'Orca', claude: 'Claude Artifact', local: '本地页' }

function ArtifactRow({ a, onNavigate }: { a: ArtifactCard; onNavigate: Nav }) {
  const vis = a.visibility ? VISIBILITY[a.visibility] : undefined
  // #artifacts&open=<id> makes the artifact page open this one; App keeps the hash until it renders.
  const open = () => { history.replaceState(null, '', `#artifacts&open=${encodeURIComponent(a.id)}`); onNavigate('artifacts') }
  const flagged = a.expired || a.public_sensitive
  return (
    <li>
      <button onClick={open} className="relative flex w-full min-w-0 flex-col gap-1 px-4 py-3 text-left hover:bg-k-sunk sm:px-5">
        {(flagged || a.sensitive || a.expiring) && <span aria-hidden className="absolute inset-y-0 left-0 w-[3px]"
          style={{ background: flagged ? 'var(--k-hot)' : 'var(--k-amber)' }} />}
        <span className="flex min-w-0 items-center gap-2">
          <span className="min-w-0 truncate text-[15px] font-[600] text-k-ink" title={a.title}>{a.title}</span>
          <span className="ml-auto shrink-0 whitespace-nowrap text-[12.5px] text-k-ink4">{agoUnix(a.updated)}</span>
        </span>
        <span className="flex flex-wrap items-center gap-1.5 text-[12.5px] text-k-ink4">
          {vis && <span className="k-chip" style={vars(vis.c, vis.cs)}>{vis.label}</span>}
          {a.expired && <span className="k-chip" style={vars('--k-hot', '--k-hot-soft')}>已过期</span>}
          {a.expiring && <span className="k-chip" style={vars('--k-amber-ink', '--k-amber-soft')}>即将过期</span>}
          {a.sensitive && <span className="k-chip" style={a.public_sensitive ? vars('--k-hot', '--k-hot-soft') : vars('--k-amber-ink', '--k-amber-soft')}>
            {a.public_sensitive ? '公开且敏感' : '敏感'}</span>}
          {a.incidents > 0 && <span className="k-chip" style={vars('--k-hot', '--k-hot-soft')}>{a.incidents} 个事故</span>}
          <span className="min-w-0 truncate">{[a.project, a.adapter && (ADAPTER[a.adapter] ?? a.adapter)].filter(Boolean).join(' · ')}</span>
        </span>
      </button>
    </li>
  )
}

function Artifacts({ data, onNavigate }: { data: Overview['artifacts']; onNavigate: Nav }) {
  const marks = failed(data) ? [] : ([
    [data.expired, '个已过期', 'text-k-hot'], [data.expiring, '个即将过期', 'text-k-amber-ink'],
    [data.public_sensitive, '个公开且敏感', 'text-k-hot'],
  ] as const).filter(([n]) => n > 0)
  return (
    <Section title="最近工件" aside={failed(data) ? undefined : (
      <>共 {data.count} 个{marks.map(([n, t, cls]) => <span key={t} className={`ml-2 font-medium ${cls}`}>{n} {t}</span>)}</>
    )}>
      {failed(data) ? <Reason>读不到工件：{data.error}</Reason>
        : data.items.length === 0 ? <p className="k-card px-6 py-6 text-center text-[14.5px] text-k-ink4">还没有采集到工件。</p>
          : (
            <ol className="k-card divide-y divide-k-line-soft overflow-hidden">
              {data.items.map((a) => <ArtifactRow key={a.id} a={a} onNavigate={onNavigate} />)}
            </ol>
          )}
    </Section>
  )
}

export default function OverviewView({ onNavigate }: { onNavigate: Nav }) {
  const { data, error, at } = usePoll(fetchOverviewPage)
  const d = new Date()
  return (
    <div className="mx-auto w-full max-w-[1180px] pb-16">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 pr-0 md:pr-28">
        <h1 className="text-[28px] font-[650] tracking-[0.01em] text-k-ink">总览</h1>
        <span className="text-[15px] text-k-ink3">{d.getMonth() + 1}月{d.getDate()}日 星期{'日一二三四五六'[d.getDay()]}</span>
        <span className="inline-flex items-center gap-2 text-[13px] text-k-ink4" title={error || undefined}>
          <span className="h-2 w-2 rounded-full" style={{ background: error ? 'var(--k-hot)' : at ? 'var(--k-ok)' : 'var(--k-ink-4)' }} />
          {error ? `总览接口读不到：${error}` : at ? `更新于 ${at.toLocaleTimeString('zh-CN', { hour12: false })} · 每 30 秒刷新` : '读取中…'}
        </span>
      </div>
      {!data ? (error ? <div className="mt-6"><Reason>总览接口读不到：{error}（/api/overview 注册了吗？）</Reason></div> : <Empty>读取中…</Empty>) : (
        <>
          <Attention data={data.attention} onNavigate={onNavigate} />
          <Health data={data.health} onNavigate={onNavigate} />
          <Today o={data} onNavigate={onNavigate} />
          <div className="grid gap-x-8 md:grid-cols-2">
            <InProgress data={data.inprogress} onNavigate={onNavigate} />
            <Artifacts data={data.artifacts} onNavigate={onNavigate} />
          </div>
          <div className="mt-8 text-right">
            <a href={issueUrl(11)} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-0.5 text-[12.5px] text-k-ink4 hover:text-k-accent">
              这一页的规划<ArrowUpRight className="h-3.5 w-3.5" />
            </a>
          </div>
        </>
      )}
    </div>
  )
}
