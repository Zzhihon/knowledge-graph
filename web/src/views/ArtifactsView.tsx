// 工件：Agent 产出的页面（Orca 分享、Claude Artifact、本地 HTML 页）放在一处看。
// 数据来自 `kg plane collect artifacts` 写进状态库的 artifact 资源；本地文件用沙箱 iframe 预览。
import { useCallback, useEffect, useMemo, useState, type CSSProperties, type ReactNode } from 'react'
import { ArrowUpRight, Copy, FileCode, Globe, HardDrive, Hourglass, RefreshCw, ShieldAlert, Sparkles } from 'lucide-react'
import { artifacts, type Artifact, type ArtifactList, type Visibility } from '../api/artifacts'
import { Drawer, KV, Section, btn } from '../components/k/HubBits'
import { Empty } from './DailyView'

const DAY = 86400
const tone = (c: string, cs: string): CSSProperties => ({ ['--c' as string]: `var(${c})`, ['--cs' as string]: `var(${cs})` })

const VIS: Record<Visibility, { label: string; style: CSSProperties }> = {
  public: { label: '公开', style: tone('--k-hot', '--k-hot-soft') },
  private: { label: '私有', style: tone('--k-violet', '--k-violet-soft') },
  local: { label: '仅本机', style: tone('--k-ink-3', '--k-sunk') },
}
const ADAPTER: Record<string, string> = { orca: 'Orca', claude: 'Claude Artifact', local: '本地页' }

function SourceIcon({ a }: { a: Artifact }) {
  const cls = 'h-[18px] w-[18px]'
  const icon = a.attrs.adapter === 'orca' ? <Globe className={cls} />
    : a.attrs.adapter === 'claude' ? <Sparkles className={cls} />
      : a.attrs.origin === 'local' ? <HardDrive className={cls} /> : <FileCode className={cls} />
  return (
    <span className="grid h-9 w-9 shrink-0 place-items-center rounded-[10px] bg-k-sunk text-k-ink3" title={`${ADAPTER[a.attrs.adapter] ?? a.attrs.adapter} · ${a.attrs.origin}`}>
      {icon}
    </span>
  )
}

function left(expires: number | null, now: number): { text: string; ink: string } | null {
  if (!expires) return null
  const s = expires - now
  if (s <= 0) return { text: '已过期', ink: 'var(--k-hot)' }
  const d = s / DAY
  const text = d < 1 ? `${Math.max(1, Math.round(s / 3600))} 小时后过期` : `${Math.ceil(d)} 天后过期`
  return { text, ink: d <= 3 ? 'var(--k-amber-ink)' : 'var(--k-ink-4)' }
}

const pad = (n: number) => String(n).padStart(2, '0')
function stamp(ts: number | null | undefined) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}
function shortWhen(ts: number | null | undefined) {
  if (!ts) return ''
  const d = new Date(ts * 1000), now = new Date()
  return d.toDateString() === now.toDateString() ? `今天 ${pad(d.getHours())}:${pad(d.getMinutes())}` : `${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}
const updated = (a: Artifact) => a.attrs.updated_at ?? a.attrs.created_at ?? a.first_seen

function timeBucket(ts: number): string {
  const d = new Date(ts * 1000), now = new Date()
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() / 1000
  if (ts >= start) return '今天'
  if (ts >= start - DAY) return '昨天'
  if (ts >= start - 6 * DAY) return '这周'
  if (d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth()) return '本月'
  return `${d.getFullYear()} 年 ${d.getMonth() + 1} 月`
}

function Card({ a, now, onOpen }: { a: Artifact; now: number; onOpen: () => void }) {
  const exp = left(a.attrs.expires_at, now)
  const critical = a.incidents.some((i) => i.severity === 'critical')
  const vis = VIS[a.attrs.visibility] ?? VIS.local
  return (
    <article className="k-card k-card-hover relative flex min-w-0 flex-col overflow-hidden px-4 pb-3.5 pt-4">
      {(critical || a.sensitive) && <span aria-hidden className="absolute inset-y-0 left-0 w-[3px]" style={{ background: critical ? 'var(--k-hot)' : 'var(--k-amber)' }} />}
      <div className="flex min-w-0 items-start gap-3">
        <SourceIcon a={a} />
        <div className="min-w-0 flex-1">
          <button onClick={onOpen} className="block w-full text-left">
            <h3 className="line-clamp-2 break-words text-[15.5px] font-[650] leading-[1.45] text-k-ink hover:text-k-accent">{a.name}</h3>
          </button>
          <div className="mt-1 flex min-w-0 items-center gap-2 text-[12.5px] text-k-ink4">
            <span className="k-chip shrink-0" style={vis.style}>{vis.label}</span>
            <span className="min-w-0 flex-1 truncate">{a.attrs.origin}</span>
            <span className="shrink-0 whitespace-nowrap">{shortWhen(updated(a))}</span>
          </div>
        </div>
      </div>
      {(a.sensitive || exp || a.attrs.same_file?.length || a.gone_at) && (
        <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-[12.5px]">
          {a.sensitive && (
            <span className="inline-flex items-center gap-1 font-semibold" style={{ color: critical ? 'var(--k-hot)' : 'var(--k-amber-ink)' }}
              title={a.hits.map((h) => `${h.label}：${h.samples.join('、')}`).join('\n')}>
              <ShieldAlert className="h-3.5 w-3.5" />敏感 · {a.hits.map((h) => `${h.label} ×${h.count}`).join('、')}
            </span>
          )}
          {exp && <span className="inline-flex items-center gap-1" style={{ color: exp.ink }}><Hourglass className="h-3.5 w-3.5" />{exp.text}</span>}
          {!!a.attrs.same_file?.length && <span className="text-k-ink4">另有 {a.attrs.same_file.length} 处发布了同一文件</span>}
          {a.gone_at && <span className="text-k-ink4">已不在来源里</span>}
        </div>
      )}
      <div className="mt-auto flex flex-wrap items-center gap-2 pt-3">
        <button onClick={onOpen} className={btn}>{a.previewable ? '预览' : '详情'}</button>
        {a.attrs.url && (
          <a href={a.attrs.url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-0.5 px-1 text-[13px] text-k-ink4 hover:text-k-accent">
            打开链接<ArrowUpRight className="h-3.5 w-3.5" />
          </a>
        )}
      </div>
    </article>
  )
}

function Detail({ a, now, onClose }: { a: Artifact; now: number; onClose: () => void }) {
  const exp = left(a.attrs.expires_at, now)
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    if (!a.attrs.local_file) return
    try { await navigator.clipboard.writeText(a.attrs.local_file); setCopied(true) } catch { /* clipboard blocked */ }
  }
  return (
    <Drawer wide title={a.name} sub={`${ADAPTER[a.attrs.adapter] ?? a.attrs.adapter} · ${a.attrs.origin}`} onClose={onClose}>
      {a.incidents.length > 0 && (
        <div className="mb-5 space-y-2">
          {a.incidents.map((i) => (
            <div key={i.id} className="rounded-tile border px-4 py-2.5 text-[14px]" style={i.severity === 'critical'
              ? { background: 'var(--k-hot-soft)', borderColor: 'color-mix(in srgb, var(--k-hot) 30%, transparent)', color: 'var(--k-hot)' }
              : { background: 'var(--k-amber-soft)', borderColor: 'color-mix(in srgb, var(--k-amber) 30%, transparent)', color: 'var(--k-amber-ink)' }}>
              <span className="font-semibold">{i.summary}</span>
              <span className="ml-2 text-[12.5px] opacity-80">#{i.id} · {stamp(i.opened_at)}</span>
            </div>
          ))}
        </div>
      )}
      <KV rows={[
        ['可见性', (VIS[a.attrs.visibility] ?? VIS.local).label],
        ['链接', a.attrs.url && <a href={a.attrs.url} target="_blank" rel="noopener noreferrer" className="break-all text-k-accent">{a.attrs.url}</a>],
        ['过期', exp && <span style={{ color: exp.ink }}>{stamp(a.attrs.expires_at)}（{exp.text}）</span>],
        ['项目', a.attrs.project],
        ['会话', a.attrs.session && <span className="k-mono text-[13px]">{a.attrs.session}</span>],
        ['更新', stamp(updated(a))],
        ['创建', stamp(a.attrs.created_at)],
        ['敏感', a.sensitive === null ? '没有本地正文，没扫描' : a.sensitive ? a.hits.map((h) => `${h.label} ×${h.count}（${h.samples.join('、')}）`).join('；') : '没发现'],
        ['本地文件', a.attrs.local_file && (
          <span className="inline-flex max-w-full items-start gap-1.5">
            <span className="k-mono break-all text-[12.5px]">{a.attrs.local_file}</span>
            <button onClick={copy} className="shrink-0 text-k-ink4 hover:text-k-accent" aria-label="复制路径"><Copy className="h-3.5 w-3.5" /></button>
            {copied && <span className="shrink-0 text-[12px] text-k-ok">已复制</span>}
          </span>
        )],
        ['资源 id', <span className="k-mono text-[12.5px]">{a.id}</span>],
      ]} />
      <div className="mt-6">
        {a.previewable ? (
          <iframe title={`预览：${a.name}`} src={artifacts.previewUrl(a.id)} sandbox="" referrerPolicy="no-referrer"
            className="h-[70vh] w-full rounded-tile border border-k-line bg-white" />
        ) : (
          <p className="k-card px-5 py-6 text-center text-[14px] text-k-ink4">
            {a.attrs.local_file ? '本地文件不在登记目录里，不提供预览（在 artifacts.yaml 的 preview_roots 里加上它所在的目录）。' : '没有本地文件可以预览。'}
          </p>
        )}
      </div>
    </Drawer>
  )
}

type Group = 'project' | 'time'
type Filter = 'all' | 'attention' | 'public'

export default function ArtifactsView() {
  const [data, setData] = useState<ArtifactList | null>(null)
  const [error, setError] = useState('')
  const [group, setGroup] = useState<Group>('project')
  const [filter, setFilter] = useState<Filter>('all')
  const [open, setOpen] = useState<Artifact | null>(null)
  const [now, setNow] = useState(() => Date.now() / 1000)
  // #artifacts&open=<resource id> opens that artifact, so the home page can link straight to one.
  const [wanted, setWanted] = useState(() => decodeURIComponent(location.hash.match(/[#&]open=([^&]+)/)?.[1] ?? ''))

  const refresh = useCallback(async () => {
    try {
      setData(await artifacts.list())
      setError('')
      setNow(Date.now() / 1000)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [])
  useEffect(() => {
    refresh()
    const timer = setInterval(() => { if (!document.hidden) refresh() }, 60_000)
    return () => clearInterval(timer)
  }, [refresh])

  const items = data?.items ?? []
  useEffect(() => {
    if (!wanted || !data) return
    const hit = data.items.find((a) => a.id === wanted)
    if (hit) setOpen(hit)
    setWanted('')
  }, [wanted, data])
  const needs = (a: Artifact) => a.incidents.length > 0 || !!a.sensitive
  const counts = useMemo(() => ({
    all: items.length,
    public: items.filter((a) => a.attrs.visibility === 'public').length,
    attention: items.filter(needs).length,
    sensitive: items.filter((a) => a.sensitive && a.attrs.visibility === 'public').length,
    expiring: items.filter((a) => a.attrs.expires_at && a.attrs.expires_at > now && a.attrs.expires_at - now <= 3 * DAY).length,
  }), [items, now])

  const groups = useMemo(() => {
    const shown = items.filter((a) => filter === 'all' || (filter === 'public' ? a.attrs.visibility === 'public' : needs(a)))
    const map = new Map<string, Artifact[]>()
    for (const a of [...shown].sort((x, y) => updated(y) - updated(x))) {
      const key = group === 'project' ? a.attrs.project || '未归属项目' : timeBucket(updated(a))
      map.set(key, [...(map.get(key) ?? []), a])
    }
    const out = [...map.entries()]
    if (group === 'project') out.sort(([ka, a], [kb, b]) => (ka === '未归属项目' ? 1 : kb === '未归属项目' ? -1 : updated(b[0]) - updated(a[0])))
    return out
  }, [items, group, filter])

  const stats: [ReactNode, string, string?][] = [
    [counts.all, '个工件'],
    [counts.public, '个公开链接'],
    [counts.sensitive, '个公开且敏感', counts.sensitive ? 'var(--k-hot)' : undefined],
    [counts.expiring, '个 3 天内过期', counts.expiring ? 'var(--k-amber-ink)' : undefined],
  ]

  return (
    <div className="mx-auto w-full max-w-[1180px] pb-16">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-2">
        <h1 className="text-[28px] font-[650] tracking-[0.01em] text-k-ink">工件</h1>
        <span className="text-[15px] text-k-ink3">Orca 分享、Claude Artifact、本地页，放在一处</span>
        <button onClick={refresh} className="ml-auto inline-flex items-center gap-1.5 text-[13px] text-k-ink4 hover:text-k-accent">
          <RefreshCw className="h-3.5 w-3.5" />{error ? `读不到：${error}` : data ? '刷新' : '读取中…'}
        </button>
      </div>

      <div className="mt-5 grid grid-cols-2 gap-x-8 gap-y-3 border-y border-k-line-strong py-3 sm:flex sm:flex-wrap sm:items-baseline">
        {stats.map(([n, unit, color]) => (
          <span key={unit} className="inline-flex items-baseline gap-1.5">
            <span className="k-num text-[23px] font-bold leading-none tracking-[-0.02em] text-k-ink" style={color ? { color } : undefined}>{n}</span>
            <span className="text-[12.5px] text-k-ink4">{unit}</span>
          </span>
        ))}
      </div>

      <div className="mt-5 flex flex-wrap items-center gap-3">
        <div className="k-seg" role="group" aria-label="分组">
          {([['project', '按项目'], ['time', '按时间']] as [Group, string][]).map(([k, label]) => (
            <button key={k} aria-pressed={group === k} onClick={() => setGroup(k)}>{label}</button>
          ))}
        </div>
        <div className="k-seg" role="group" aria-label="筛选">
          {([['all', '全部', counts.all], ['attention', '要留意', counts.attention], ['public', '公开', counts.public]] as [Filter, string, number][]).map(([k, label, n]) => (
            <button key={k} aria-pressed={filter === k} onClick={() => setFilter(k)}>{label}<small>{n}</small></button>
          ))}
        </div>
      </div>

      {data && !data.claude_import.exists && (
        <p className="mt-4 text-[13px] text-k-ink4">还没有 Claude Artifact 清单：每天一次的同步任务会写 <span className="k-mono">.kg/plane/claude-artifacts.json</span>。</p>
      )}

      {data && !items.length && <Empty>还没有工件记录。先跑一次 <span className="k-mono">kg plane collect artifacts</span>。</Empty>}
      {data && items.length > 0 && !groups.length && <Empty>这个筛选下没有工件。</Empty>}

      {groups.map(([key, list]) => (
        <Section key={key} title={key} aside={`${list.length} 个`}>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {list.map((a) => <Card key={a.id} a={a} now={now} onOpen={() => setOpen(a)} />)}
          </div>
        </Section>
      ))}

      {open && <Detail a={open} now={now} onClose={() => setOpen(null)} />}
    </div>
  )
}
