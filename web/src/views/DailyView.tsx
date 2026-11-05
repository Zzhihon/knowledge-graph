import { useEffect, useMemo, useState, type CSSProperties, type ReactNode } from 'react'
import { ChevronDown, ArrowUpRight } from 'lucide-react'
import Markdown from '../components/k/Markdown'
import { fetchDaily, tone, toneInk, useReader } from '../lib/library'
import { ageOf, parseDaily, type Daily } from '../lib/daily'

const FILTERS = [
  ['all', '全部'],
  ['proj', '项目'],
  ['fresh', '新沉淀'],
  ['lab', '实验'],
  ['news', '简报'],
] as const
type Filter = (typeof FILTERS)[number][0]

const dayTitle = (date: string) => {
  const d = new Date(`${date}T00:00:00`)
  return { md: `${d.getMonth() + 1}月${d.getDate()}日`, week: `星期${'日一二三四五六'[d.getDay()]}` }
}
const RANK = ['--k-rank-1', '--k-rank-2', '--k-rank-3']

function Row({ label, dot, children }: { label: string; dot: string; children: ReactNode }) {
  return (
    <>
      <div className="k-rail-label">{label}</div>
      <div className="k-rail-dot" style={{ ['--c' as string]: `var(${dot})` } as CSSProperties} />
      {children}
    </>
  )
}

function Card({ source, chip, extra, title, body, note, onOpen }: {
  source: ReactNode; chip?: string; extra?: ReactNode; title?: ReactNode; body?: ReactNode; note?: ReactNode; onOpen?: () => void
}) {
  return (
    <article className={`k-card k-card-hover relative min-w-0 px-[22px] pb-[14px] pt-[15px] ${onOpen ? 'cursor-pointer' : ''}`} onClick={onOpen}>
      <header className="flex min-h-[20px] items-center gap-2 text-[13px] text-k-ink4">
        <span className="min-w-0 truncate">{source}</span>
        {chip && <span className="k-chip" style={tone(chip)}>{chip}</span>}
        {extra && <span className="ml-auto flex shrink-0 items-center gap-2">{extra}</span>}
      </header>
      {title && <h3 className="mt-2 text-[17.5px] font-[650] leading-[1.55] text-k-ink">{title}</h3>}
      {body && <div className="mt-1.5 text-[15px] leading-[1.75] text-k-ink3">{body}</div>}
      {note && <div className="mt-3 border-t border-k-line-soft pt-2.5 text-[13.5px] leading-[1.75] text-k-note">{note}</div>}
    </article>
  )
}

export default function DailyView({ dailies }: { dailies: string[] }) {
  const { index, open } = useReader()
  const [date, setDate] = useState(dailies[0] ?? '')
  const [daily, setDaily] = useState<Daily | null>(null)
  const [error, setError] = useState('')
  const [filter, setFilter] = useState<Filter>('all')
  const [picking, setPicking] = useState(false)

  useEffect(() => { if (!date && dailies[0]) setDate(dailies[0]) }, [dailies, date])
  useEffect(() => {
    if (!date) return
    setDaily(null)
    setError('')
    fetchDaily(date).then((f) => setDaily(parseDaily(f.content))).catch((e) => setError(String(e.message ?? e)))
  }, [date])

  const counts = useMemo(() => daily && {
    proj: daily.projects.length, fresh: daily.fresh.length, lab: daily.lab.length, news: daily.news.length,
  }, [daily])

  if (!dailies.length) return <Empty>还没有日报。每天 09:15 自动生成，存在 00-Index/Daily/。</Empty>
  const { md, week } = dayTitle(date)
  const at = dailies.indexOf(date)
  const show = (f: Filter) => filter === 'all' || filter === f
  const titleOf = (id: string) => (index[id]?.[0] ?? id)
  const splitTitle = (id: string) => { const [name, ...rest] = titleOf(id).split(/[：:]/); return { name, sub: rest.join('：').trim() } }

  return (
    <div className="mx-auto w-full max-w-[1180px] pb-16">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-2">
        <h1 className="text-[28px] font-[650] tracking-[0.01em] text-k-ink">日报</h1>
        <div className="relative">
          <button className="flex items-center gap-1.5 rounded-full px-2 py-1 text-[15px] text-k-ink3 hover:bg-k-sunk" onClick={() => setPicking((v) => !v)}>
            <span className="k-mono">{date}</span><span>{week}</span><ChevronDown className="h-4 w-4" />
          </button>
          {picking && (
            <div className="absolute left-0 top-9 z-20 max-h-72 w-56 overflow-y-auto rounded-panel border border-k-line bg-k-surface p-1.5" style={{ boxShadow: 'var(--k-shadow-pop)' }}>
              {dailies.map((d) => (
                <button key={d} onClick={() => { setDate(d); setPicking(false) }}
                  className={`flex w-full justify-between rounded-tile px-3 py-2 text-left text-[14px] hover:bg-k-sunk ${d === date ? 'font-semibold text-k-ink' : 'text-k-ink2'}`}>
                  <span className="k-mono">{d}</span><span className="text-k-ink4">{dayTitle(d).week}</span>
                </button>
              ))}
            </div>
          )}
        </div>
        <div className="ml-auto flex gap-1 text-[13.5px]">
          <button disabled={at >= dailies.length - 1} onClick={() => setDate(dailies[at + 1])} className="rounded-full px-3 py-1 text-k-ink3 hover:bg-k-sunk disabled:opacity-40">← 前一天</button>
          <button disabled={at <= 0} onClick={() => setDate(dailies[at - 1])} className="rounded-full px-3 py-1 text-k-ink3 hover:bg-k-sunk disabled:opacity-40">后一天 →</button>
        </div>
      </div>

      {error && <Empty>读不到这天的日报：{error}</Empty>}
      {!daily && !error && <Empty>读取中…</Empty>}
      {daily && counts && (
        <>
          <section className="mt-5 rounded-panel border px-6 py-5" style={{ background: 'var(--k-amber-soft)', borderColor: 'color-mix(in srgb, var(--k-amber) 28%, transparent)' }}>
            <div className="flex items-center gap-2 text-[13px] font-semibold text-k-amber-ink">
              <span className="h-1.5 w-1.5 rounded-full bg-k-amber" />{md} · 一句话
            </div>
            <Markdown inline source={daily.lead || '今天没有一句话总结。'} className="mt-2 block text-[17px] leading-[1.75] text-k-ink" />
          </section>

          <div className="mt-6 flex flex-wrap items-baseline gap-x-8 gap-y-2 border-y border-k-line-strong py-3">
            {([[counts.proj, '个项目推进'], [counts.fresh, '条新沉淀'], [counts.lab, '项实验动态'], [daily.todo.length, '件待办'], [counts.news, '条外部简报']] as const).map(([n, unit]) => (
              <span key={unit} className="inline-flex items-baseline gap-1.5">
                <span className="k-num text-[23px] font-bold leading-none tracking-[-0.02em] text-k-ink">{n}</span>
                <span className="text-[12.5px] text-k-ink4">{unit}</span>
              </span>
            ))}
          </div>

          <div className="mt-6 flex flex-wrap items-center gap-3">
            <div className="k-seg" role="group" aria-label="筛选">
              {FILTERS.map(([key, label]) => (
                <button key={key} aria-pressed={filter === key} onClick={() => setFilter(key)}>
                  {label}{key !== 'all' && <small>{counts[key]}</small>}
                </button>
              ))}
            </div>
          </div>

          {filter === 'all' && daily.todo.length > 0 && (
            <section className="k-card relative mt-6 overflow-hidden px-6 pb-2 pt-4">
              <div aria-hidden className="pointer-events-none absolute -right-20 -top-28 h-64 w-96" style={{ background: 'radial-gradient(closest-side, var(--k-hot-soft), transparent)' }} />
              <header className="relative flex items-center gap-2.5 text-[15.5px] font-[650] text-k-ink">
                <span className="h-2 w-2 rounded-full bg-k-hot" />今天要你动手
                <span className="ml-auto text-[12.5px] font-normal text-k-ink4">来自 GitHub 每日汇总</span>
              </header>
              {daily.todo.map((t, i) => {
                const age = ageOf(t)
                return (
                  <div key={i} className="relative grid grid-cols-[30px_minmax(0,1fr)_auto] items-baseline gap-2 border-t border-k-line-soft py-3 first-of-type:border-0">
                    <span className="k-mono text-[16px] font-bold" style={{ color: `var(${RANK[i] ?? '--k-rank-rest'})` }}>{i + 1}</span>
                    <Markdown inline source={t.replace(/(^|[^\w&[])#(\d+)/g, '$1`#$2`')} className="text-[15px] font-medium leading-[1.65] text-k-ink" />
                    <span className="whitespace-nowrap text-[13px] text-k-ink3">{age && <><b className="k-mono text-k-ink">{age}</b> 天未动</>}</span>
                  </div>
                )
              })}
            </section>
          )}

          {filter === 'all' && daily.diagram && (
            <section className="mt-6">
              <h2 className="mb-2 text-[15px] font-[650] text-k-ink">今日图解</h2>
              <Markdown source={daily.diagram} />
            </section>
          )}

          <div className="mt-9 flex items-baseline gap-4">
            <h2 className="text-[23px] font-[650] text-k-ink">{md}</h2>
            <span className="text-[14.5px] text-k-ink4">{week} · 进展与沉淀</span>
          </div>

          <div className="k-rail mt-4">
            {show('proj') && daily.projects.map((p, i) => {
              const { name, sub } = splitTitle(p.id)
              const path = index[p.id]?.[1]
              return (
                <Row key={`p${i}`} label="项目" dot="--k-accent">
                  <Card source={`项目进展${sub ? `：${sub}` : ''}`} chip={p.isNew ? '新项目页' : undefined}
                    title={p.id ? name : undefined} body={<Markdown inline source={p.md} />} onOpen={path ? () => open(path) : undefined} />
                </Row>
              )
            })}
            {show('fresh') && daily.fresh.map((x, i) => {
              const m = x.md.match(/^\[\[([^\]|]+)(?:\|[^\]]*)?\]\]\s*(.*)$/)
              const path = m ? index[m[1].trim()]?.[1] : undefined
              return (
                <Row key={`f${i}`} label={x.kind || '沉淀'} dot={toneInk(x.kind)}>
                  <Card source="新沉淀" chip={x.kind || undefined}
                    title={m ? titleOf(m[1].trim()) : undefined}
                    body={m ? undefined : <Markdown inline source={x.md} />}
                    note={m && m[2] ? <>一句话：<Markdown inline source={m[2]} /></> : undefined}
                    onOpen={path ? () => open(path) : undefined} />
                </Row>
              )
            })}
            {show('lab') && daily.lab.map((x, i) => (
              <Row key={`l${i}`} label="实验" dot="--k-violet">
                <Card source="实验板块" chip={x.kind || undefined} body={<Markdown inline source={x.md} />} />
              </Row>
            ))}
            {show('news') && daily.news.map((n, i) => (
              <Row key={`n${i}`} label="简报" dot="--k-amber">
                <Card source={`外部简报：${n.source}`} chip="简报"
                  extra={n.url && (
                    <a href={n.url} target="_blank" rel="noopener noreferrer" onClick={(e) => e.stopPropagation()}
                      className="inline-flex items-center gap-0.5 rounded-full border border-k-line px-2.5 py-0.5 text-[12.5px] text-k-ink3 hover:border-k-accent hover:text-k-accent">
                      原文<ArrowUpRight className="h-3.5 w-3.5" />
                    </a>
                  )}
                  title={n.title || undefined}
                  note={n.md ? <>和你的关系：<Markdown inline source={n.md} /></> : undefined}
                  onOpen={n.url ? () => window.open(n.url, '_blank', 'noopener') : undefined} />
              </Row>
            ))}
            {show('news') && daily.newsNote && (
              <Row label="简报" dot="--k-amber"><Card source="外部简报" body={<Markdown inline source={daily.newsNote} />} /></Row>
            )}
          </div>

          {filter === 'all' && daily.other.map((s) => (
            <section key={s.title} className="k-card mt-6 px-6 py-5">
              <h2 className="mb-2 text-[16px] font-[650] text-k-ink">{s.title}</h2>
              <Markdown source={s.md} />
            </section>
          ))}
        </>
      )}
    </div>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="py-16 text-center text-[14.5px] text-k-ink4">{children}</p>
}
