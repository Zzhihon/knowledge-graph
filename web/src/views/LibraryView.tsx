import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { Search } from 'lucide-react'
import { BOARDS, STATUS, fetchEntries, tone, toneInk, useReader, type LibraryCard } from '../lib/library'
import { Empty } from './DailyView'

const pad = (n: number) => String(n).padStart(2, '0')
const dayKey = (t: number) => { const d = new Date(t * 1000); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` }
const dayLabel = (key: string) => {
  const d = new Date(`${key}T00:00:00`)
  return { md: `${d.getMonth() + 1}月${d.getDate()}日`, week: `星期${'日一二三四五六'[d.getDay()]}` }
}
const hm = (t: number) => { const d = new Date(t * 1000); return `${pad(d.getHours())}:${pad(d.getMinutes())}` }

interface Props {
  title: string
  /** A fixed board (项目, 实验 pages) or empty for the whole library with board tabs. */
  board?: string
  counts: Record<string, number>
}

export default function LibraryView({ title, board: fixed = '', counts }: Props) {
  const { open } = useReader()
  const [board, setBoard] = useState(fixed)
  const [q, setQ] = useState('')
  const [query, setQuery] = useState('')
  const [limit, setLimit] = useState(40)
  const [list, setList] = useState<LibraryCard[] | null>(null)
  const [error, setError] = useState('')
  const input = useRef<HTMLInputElement>(null)

  useEffect(() => { setBoard(fixed); setLimit(40) }, [fixed])
  useEffect(() => { const t = setTimeout(() => { setQuery(q.trim()); setLimit(40) }, 250); return () => clearTimeout(t) }, [q])
  useEffect(() => {
    let live = true
    setError('')
    fetchEntries(board, query, limit).then((r) => live && setList(r)).catch((e) => live && setError(String(e.message ?? e)))
    return () => { live = false }
  }, [board, query, limit])
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName
      if (e.key === '/' && tag !== 'INPUT' && tag !== 'TEXTAREA') { e.preventDefault(); input.current?.focus() }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const days = useMemo(() => {
    const groups: [string, LibraryCard[]][] = []
    for (const e of list ?? []) {
      const key = dayKey(e.mtime)
      if (groups[groups.length - 1]?.[0] !== key) groups.push([key, []])
      groups[groups.length - 1][1].push(e)
    }
    return groups
  }, [list])
  const total = Object.values(counts).reduce((a, b) => a + b, 0)
  const boards = Object.keys(BOARDS).filter((b) => counts[b])

  return (
    <div className="mx-auto w-full max-w-[1180px] pb-16">
      <div className="flex flex-wrap items-baseline gap-4">
        <h1 className="text-[28px] font-[650] tracking-[0.01em] text-k-ink">{title}</h1>
        <span className="text-[14.5px] text-k-ink4">{fixed ? `${counts[fixed] ?? 0} 条` : `${total} 条，${boards.length} 个板块`}</span>
      </div>

      <div className="mt-5 flex flex-wrap items-center gap-3">
        {!fixed && (
          <div className="k-seg" role="group" aria-label="板块">
            <button aria-pressed={board === ''} onClick={() => { setBoard(''); setLimit(40) }}>全部</button>
            {boards.map((b) => (
              <button key={b} aria-pressed={board === b} onClick={() => { setBoard(b); setLimit(40) }}>
                {BOARDS[b]}<small>{counts[b]}</small>
              </button>
            ))}
          </div>
        )}
        <label className="k-search ml-auto w-full sm:w-[320px]">
          <Search className="h-4 w-4 shrink-0" />
          <input ref={input} value={q} onChange={(e) => setQ(e.target.value)} placeholder="搜标题、正文、标签…" aria-label="搜索条目" />
          <kbd className="k-kbd">/</kbd>
        </label>
      </div>

      {error && <Empty>读不到条目：{error}</Empty>}
      {!list && !error && <Empty>读取中…</Empty>}
      {list && !list.length && <Empty>{query ? `没有找到含"${query}"的条目` : '这个板块还没有条目'}</Empty>}

      {days.map(([key, items]) => {
        const { md, week } = dayLabel(key)
        return (
          <section key={key}>
            <div className="mt-9 flex items-baseline gap-4">
              <h2 className="text-[23px] font-[650] text-k-ink">{md}</h2>
              <span className="text-[14.5px] text-k-ink4">{week} · {items.length} 条</span>
            </div>
            <div className="k-rail mt-4">
              {items.map((e) => {
                const kind = BOARDS[e.board] ?? e.board
                return (
                  <div key={e.path} className="contents">
                    <div className="k-rail-label">{hm(e.mtime)}</div>
                    <div className="k-rail-dot" style={{ ['--c' as string]: `var(${toneInk(kind)})` } as CSSProperties} />
                    <article className="k-card k-card-hover min-w-0 cursor-pointer px-[22px] pb-[14px] pt-[15px]" onClick={() => open(e.path)}>
                      <header className="flex min-h-[20px] items-center gap-2 text-[13px] text-k-ink4">
                        <span className="k-chip" style={tone(kind)}>{kind}</span>
                        {e.status && <span>{STATUS[e.status] ?? e.status}</span>}
                        {e.diagram && <span className="k-chip plain" style={tone('项目')}>含图解</span>}
                        <span className="k-mono ml-auto truncate pl-2 text-[12px]">{e.id}</span>
                      </header>
                      <h3 className="mt-2 text-[17.5px] font-[650] leading-[1.55] text-k-ink">{e.title}</h3>
                      {e.summary && <p className="mt-1.5 line-clamp-3 text-[15px] leading-[1.75] text-k-ink3">{e.summary}</p>}
                      {e.tags.length > 0 && (
                        <div className="mt-2 flex flex-wrap gap-x-2.5 gap-y-1 text-[12.5px] text-k-ink4">
                          {e.tags.slice(0, 5).map((t) => <span key={t}>#{t}</span>)}
                        </div>
                      )}
                    </article>
                  </div>
                )
              })}
            </div>
          </section>
        )
      })}

      {list && list.length >= limit && (
        <div className="mt-8 flex justify-center">
          <button onClick={() => setLimit((n) => n + 40)} className="rounded-full border border-k-line bg-k-surface px-5 py-2 text-[14px] text-k-ink2 hover:border-k-accent hover:text-k-accent">
            再看 40 条
          </button>
        </div>
      )}
    </div>
  )
}
