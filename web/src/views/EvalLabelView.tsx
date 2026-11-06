import { useEffect, useMemo, useRef, useState } from 'react'
import { Search, X, Plus } from 'lucide-react'
import { evalSet, HubApiError, type EvalItem } from '../api/hub'
import { BOARDS, fetchEntries, tone, useReader, type LibraryCard } from '../lib/library'
import { Problem, btn, btnPrimary } from '../components/k/HubBits'
import { Empty } from './DailyView'

type Entries = Record<string, { title: string; board: string; path: string }>
const statusOf = (it: EvalItem) => (it.state === 'skip' ? 'skip' : it.state === 'ok' ? 'ok' : 'draft')
const FILTERS: [string, string, (it: EvalItem) => boolean][] = [
  ['all', '全部', () => true],
  ['draft', '待审', (it) => statusOf(it) === 'draft'],
  ['unsure', '拿不准', (it) => !!it.unsure && statusOf(it) === 'draft'],
  ['none', '知识库里没有', (it) => !!it.none],
  ['ok', '已确认', (it) => statusOf(it) === 'ok'],
  ['skip', '不要', (it) => statusOf(it) === 'skip'],
]

function Chip({ id, on, entries, onToggle }: { id: string; on: boolean; entries: Entries; onToggle: () => void }) {
  const { open } = useReader()
  const e = entries[id] ?? { title: id, board: '', path: '' }
  const board = BOARDS[e.board] ?? ''
  return (
    <span className={`inline-flex max-w-full items-center gap-1.5 rounded-full border py-0.5 pl-3 pr-1 text-[13.5px] ${on ? 'border-k-accent/40 bg-k-accent-soft text-k-ink' : 'border-k-line bg-k-surface text-k-ink3'}`}>
      <button className="min-w-0 truncate text-left hover:underline" title={id} onClick={() => e.path && open(e.path)} disabled={!e.path}>{e.title}</button>
      {board && <span className="shrink-0 text-[11.5px] text-k-ink4">{board}</span>}
      <button onClick={onToggle} aria-label={on ? '移除' : '加入'} className="shrink-0 rounded-full p-0.5 text-k-ink4 hover:bg-k-sunk hover:text-k-ink">
        {on ? <X className="h-3.5 w-3.5" /> : <Plus className="h-3.5 w-3.5" />}
      </button>
    </span>
  )
}

function Finder({ onPick }: { onPick: (c: LibraryCard) => void }) {
  const [q, setQ] = useState('')
  const [hits, setHits] = useState<LibraryCard[] | null>(null)
  const [busy, setBusy] = useState(false)
  const find = async () => {
    if (!q.trim()) return
    setBusy(true)
    try { setHits(await fetchEntries('', q.trim(), 20)) } catch { setHits([]) } finally { setBusy(false) }
  }
  return (
    <div className="relative min-w-0 flex-1">
      <label className="k-search h-9">
        <Search className="h-4 w-4 shrink-0" />
        <input value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && find()}
          placeholder="搜条目标题或正文，回车" aria-label="搜索条目" />
      </label>
      {(busy || hits) && (
        <div className="absolute left-0 right-0 top-10 z-20 max-h-72 overflow-y-auto rounded-panel border border-k-line bg-k-surface p-1.5" style={{ boxShadow: 'var(--k-shadow-pop)' }}>
          {busy && <p className="px-3 py-2 text-[13px] text-k-ink4">搜索中…</p>}
          {!busy && hits?.length === 0 && <p className="px-3 py-2 text-[13px] text-k-ink4">没找到</p>}
          {!busy && hits?.map((h) => (
            <button key={h.path} onClick={() => { onPick(h); setHits(null) }} className="block w-full rounded-tile px-3 py-2 text-left hover:bg-k-sunk">
              <span className="block truncate text-[14px] text-k-ink">{h.title}</span>
              <span className="block truncate text-[12px] text-k-ink4">{BOARDS[h.board] ?? h.board} · {h.id}</span>
            </button>
          ))}
          {!busy && <button onClick={() => setHits(null)} className="block w-full px-3 py-1.5 text-right text-[12px] text-k-ink4">收起</button>}
        </div>
      )}
    </div>
  )
}

export default function EvalLabelView() {
  const [items, setItems] = useState<EvalItem[] | null>(null)
  const [entries, setEntries] = useState<Entries>({})
  const [error, setError] = useState('')
  const [filter, setFilter] = useState('all')
  const [saved, setSaved] = useState('')
  const [confirm, setConfirm] = useState(false)
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined)

  useEffect(() => {
    evalSet.draft().then((d) => { setItems(d.items); setEntries(d.entries); setSaved('草稿已载入') })
      .catch((e) => setError(e instanceof HubApiError ? e.message : String(e)))
  }, [])

  const save = (next: EvalItem[]) => {
    clearTimeout(timer.current)
    setSaved('保存中…')
    timer.current = setTimeout(async () => {
      try {
        await evalSet.save(next)
        setSaved(`已自动保存 ${new Date().toLocaleTimeString('zh-CN', { hour12: false })}`)
      } catch (e) { setSaved(`没保存上：${e instanceof Error ? e.message : String(e)}`) }
    }, 600)
  }
  const change = (n: number, fn: (it: EvalItem) => EvalItem) => {
    if (!items) return
    const next = items.map((it) => (it.n === n ? fn({ ...it }) : it))
    setItems(next)
    save(next)
    setConfirm(false)
  }
  const counts = useMemo(() => Object.fromEntries(FILTERS.map(([k, , f]) => [k, items?.filter(f).length ?? 0])), [items])

  const publish = async () => {
    if (!items) return
    const left = counts.draft
    if (left && !confirm) { setConfirm(true); return }
    clearTimeout(timer.current)
    try {
      const r = await evalSet.publish(items)
      setResult({ ok: true, text: `评测集已生成：${r.published} 题，其中 ${r.labelled} 题有标注答案（.kg/eval/retrieval.jsonl）。` })
      setSaved(`已保存 ${new Date().toLocaleTimeString('zh-CN', { hour12: false })}`)
    } catch (e) {
      setResult({ ok: false, text: `没生成成功：${e instanceof Error ? e.message : String(e)}` })
    } finally { setConfirm(false) }
  }

  if (error) return <div className="mx-auto w-full max-w-[980px]"><h1 className="text-[28px] font-[650] text-k-ink">评测集审核</h1><div className="mt-6"><Problem message={error} /></div></div>
  if (!items) return <Empty>读取中…</Empty>
  const done = items.length - counts.draft
  const shown = items.filter(FILTERS.find(([k]) => k === filter)![2])

  return (
    <div className="mx-auto w-full max-w-[980px] pb-16">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-2">
        <h1 className="text-[28px] font-[650] tracking-[0.01em] text-k-ink">评测集审核</h1>
        <span className="text-[14.5px] text-k-ink4">知识库 · 评测</span>
      </div>
      <p className="mt-2 max-w-[760px] text-[14.5px] leading-[1.75] text-k-ink3">
        问题是从最近的会话里挑出来的。每题下面是<b className="text-k-ink">应该被检索出来的条目</b>：对就点"确认"，不对就删掉或从候选、搜索里换。知识库里确实没有答案的点"知识库里没有"，不像知识类问题的点"这题不要"。改动自动保存，审完点"生成评测集"。
      </p>

      <div className="sticky top-0 z-10 -mx-2 mt-5 flex flex-wrap items-center gap-3 bg-k-bg/95 px-2 py-3 backdrop-blur">
        <span className="text-[14px] text-k-ink3">已审 <b className="k-num text-k-ink">{done}</b> / {items.length}</span>
        <div className="k-seg" role="group" aria-label="筛选">
          {FILTERS.map(([k, label]) => (
            <button key={k} aria-pressed={filter === k} onClick={() => setFilter(k)}>{label}<small>{counts[k]}</small></button>
          ))}
        </div>
        <span className="text-[12.5px] text-k-ink4">{saved}</span>
        <button className={`${btnPrimary} ml-auto`} onClick={publish}>{confirm ? `还有 ${counts.draft} 题没审，再点一次照样生成` : '生成评测集'}</button>
        <div className="h-1 w-full overflow-hidden rounded-full bg-k-sunk"><i className="block h-full bg-k-accent transition-[width]" style={{ width: `${(done / Math.max(items.length, 1)) * 100}%` }} /></div>
      </div>
      {result && <div className="mt-3">{result.ok
        ? <div className="rounded-tile px-4 py-2.5 text-[14px] text-k-ok" style={{ background: 'var(--k-ok-soft)' }}>{result.text}</div>
        : <Problem message={result.text} onClose={() => setResult(null)} />}</div>}

      <div className="mt-4 space-y-4">
        {shown.map((it) => {
          const st = statusOf(it)
          const others = it.candidates.filter((id) => !it.expected.includes(id))
          return (
            <article key={it.n} className={`k-card px-5 pb-4 pt-4 sm:px-6 ${st === 'skip' ? 'opacity-60' : ''}`}
              style={st === 'ok' ? { borderColor: 'color-mix(in srgb, var(--k-ok) 45%, transparent)' } : undefined}>
              <header className="flex flex-wrap items-center gap-2 text-[13px] text-k-ink4">
                {it.project && <span className="k-chip" style={tone('项目')}>{it.project}</span>}
                <span className="truncate">{it.source}</span>
                {it.unsure && st === 'draft' && <span className="k-chip">拿不准</span>}
                {it.none && <span className="k-chip plain">知识库里没有</span>}
                <span className="k-mono ml-auto">#{it.n + 1}</span>
              </header>
              <h3 className="mt-2 text-[16.5px] font-[650] leading-[1.6] text-k-ink">{it.q}</h3>
              <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-[64px_minmax(0,1fr)] sm:items-start">
                <span className="pt-1 text-[12.5px] text-k-ink4">应命中</span>
                <div className="flex min-w-0 flex-wrap gap-1.5">
                  {it.expected.length ? it.expected.map((id) => (
                    <Chip key={id} id={id} on entries={entries} onToggle={() => change(it.n, (x) => ({
                      ...x, expected: x.expected.filter((y) => y !== id), candidates: x.candidates.includes(id) ? x.candidates : [id, ...x.candidates],
                    }))} />
                  )) : <span className="pt-1 text-[13px] text-k-ink4">还没有</span>}
                </div>
                {others.length > 0 && <>
                  <span className="pt-1 text-[12.5px] text-k-ink4">候选</span>
                  <div className="flex min-w-0 flex-wrap gap-1.5">
                    {others.map((id) => (
                      <Chip key={id} id={id} on={false} entries={entries} onToggle={() => change(it.n, (x) => ({ ...x, expected: [...x.expected, id], none: false }))} />
                    ))}
                  </div>
                </>}
                <span className="pt-2 text-[12.5px] text-k-ink4">搜索</span>
                <Finder onPick={(c) => {
                  setEntries((e) => ({ ...e, [c.id]: { title: c.title, board: c.board, path: c.path } }))
                  change(it.n, (x) => ({ ...x, expected: x.expected.includes(c.id) ? x.expected : [...x.expected, c.id], none: false }))
                }} />
              </div>
              <div className="mt-4 flex flex-wrap gap-2">
                <button className={st === 'ok' ? btnPrimary : btn} onClick={() => change(it.n, (x) => ({ ...x, state: 'ok' }))}>确认</button>
                <button className={it.none ? btnPrimary : btn} onClick={() => change(it.n, (x) => (x.none
                  ? { ...x, none: false }
                  : { ...x, none: true, candidates: [...x.expected, ...x.candidates], expected: [], state: 'ok' }))}>知识库里没有</button>
                <button className={st === 'skip' ? btnPrimary : btn} onClick={() => change(it.n, (x) => ({ ...x, state: 'skip' }))}>这题不要</button>
                {st !== 'draft' && <button className={btn} onClick={() => change(it.n, (x) => ({ ...x, state: 'draft' }))}>撤回</button>}
              </div>
            </article>
          )
        })}
        {!shown.length && <Empty>这一栏没有题目</Empty>}
      </div>
    </div>
  )
}
