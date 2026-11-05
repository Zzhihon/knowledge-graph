import { useState, type ReactNode } from 'react'
import { ArrowRight, ArrowUpRight } from 'lucide-react'
import { hub, HubApiError, type HubCard } from '../api/hub'
import { useReader } from '../lib/library'
import { BY, ago, money, namesOf, tr, useHubState, when } from '../lib/hub'
import { Drawer, KV, Live, Problem, Section, btn, btnPrimary, field } from '../components/k/HubBits'
import { Empty } from './DailyView'

const VAULT_FILE = /(?:[^\s"'`]*\/)?((?:0\d|1[01])-[^\s/]+\/[^\s"'`]+\.md)/g

/** Card text with vault file paths turned into links that open the reader. */
function Linked({ text }: { text: string }) {
  const { open } = useReader()
  const parts: ReactNode[] = []
  let last = 0
  for (const m of text.matchAll(VAULT_FILE)) {
    parts.push(text.slice(last, m.index))
    const rel = m[1]
    parts.push(
      <button key={m.index} onClick={() => open(rel)} className="k-wiki break-all text-left" title={m[0]}>{rel}</button>,
    )
    last = (m.index ?? 0) + m[0].length
  }
  parts.push(text.slice(last))
  return <>{parts}</>
}

function Ticket({ card, onDone }: { card: HubCard; onDone: () => void }) {
  const [busy, setBusy] = useState<number | 'text' | null>(null)
  const [problem, setProblem] = useState<HubApiError | null>(null)
  const [text, setText] = useState('')
  const [full, setFull] = useState(false)
  const ask = card.kind === 'ask'
  const long = card.text.split('\n').length > 5 || card.text.length > 260

  const send = async (body: { choice: number } | { text: string }, which: number | 'text') => {
    setBusy(which)
    setProblem(null)
    try {
      await hub.answer(card.id, body)
      onDone()
    } catch (e) {
      setProblem(e instanceof HubApiError ? e : new HubApiError(0, String(e)))
      setBusy(null)
    }
  }

  return (
    <article className="k-card relative min-w-0 overflow-hidden px-5 pb-4 pt-4 sm:px-6">
      <span aria-hidden className="absolute inset-y-0 left-0 w-[3px]" style={{ background: ask ? 'var(--k-amber)' : 'var(--k-accent)' }} />
      <header className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px] text-k-ink4">
        <span className="k-mono">#{card.id}</span>
        <span className="k-chip" style={ask ? undefined : { ['--c' as string]: 'var(--k-accent)', ['--cs' as string]: 'var(--k-accent-soft)' }}>
          {ask ? '要你选' : '要你留意'}
        </span>
        <span className="truncate">{card.rule}</span>
        <span className="ml-auto whitespace-nowrap">{ago(card.createdAt)}</span>
      </header>
      <h3 className="mt-2 text-[17px] font-[650] leading-[1.5] text-k-ink">{card.title}</h3>
      <p className={`mt-1.5 whitespace-pre-line break-words text-[14.5px] leading-[1.75] text-k-ink3 ${long && !full ? 'line-clamp-5' : ''}`}>
        <Linked text={card.text} />
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        {card.options.map((o, i) => (
          <button key={i} disabled={busy !== null} onClick={() => send({ choice: i }, i)}
            className={!ask || card.recommend === i ? btnPrimary : btn}>
            {busy === i ? '提交中…' : <>{o}{card.recommend === i && '（推荐）'}</>}
          </button>
        ))}
        {long && <button onClick={() => setFull((v) => !v)} className="px-2 text-[13px] text-k-ink4 hover:text-k-ink">{full ? '收起' : '展开全文'}</button>}
        {card.link && (
          <a href={card.link} className="ml-auto inline-flex items-center gap-0.5 text-[13px] text-k-ink4 hover:text-k-accent" target="_blank" rel="noopener noreferrer">
            打开<ArrowUpRight className="h-3.5 w-3.5" />
          </a>
        )}
      </div>
      {ask && (
        <form className="mt-3 flex gap-2" onSubmit={(e) => { e.preventDefault(); if (text.trim()) send({ text: text.trim() }, 'text') }}>
          <input value={text} onChange={(e) => setText(e.target.value)} placeholder="或者写你的想法" className={field} aria-label={`回答 #${card.id}`} />
          <button disabled={busy !== null || !text.trim()} className={btn}>{busy === 'text' ? '提交中…' : '发送'}</button>
        </form>
      )}
      {problem && <div className="mt-3"><Problem message={`没提交成功：${problem.message}`} fromHub={problem.fromHub} /></div>}
    </article>
  )
}

export default function HubView({ onNavigate }: { onNavigate?: (tab: string) => void }) {
  const { state, error, at, refresh } = useHubState()
  const [recent, setRecent] = useState<HubCard | null>(null)
  const names = namesOf(state)
  const d = new Date()

  if (!state) {
    return (
      <div className="mx-auto w-full max-w-[1180px] pb-16">
        <h1 className="text-[28px] font-[650] text-k-ink">中台</h1>
        {error ? <div className="mt-6"><Problem message={`读不到中台：${error}`} /></div> : <Empty>读取中…</Empty>}
      </div>
    )
  }
  const t = state.totals, sp = state.spending
  const online = state.agents.filter((a) => a.online).length
  const bits = [`做完 ${t.doneToday} 轮`, t.running ? `${t.running} 轮在跑` : '', t.queued + t.retrying ? `${t.queued + t.retrying} 轮排队` : ''].filter(Boolean)

  return (
    <div className="mx-auto w-full max-w-[1180px] pb-16">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-2">
        <h1 className="text-[28px] font-[650] tracking-[0.01em] text-k-ink">中台</h1>
        <span className="text-[15px] text-k-ink3">{d.getMonth() + 1}月{d.getDate()}日 · {bits.join(' · ')}</span>
        <span className="ml-auto"><Live at={at} error={error} /></span>
      </div>

      <div className="mt-5 grid grid-cols-2 gap-x-8 gap-y-3 border-y border-k-line-strong py-3 sm:flex sm:flex-wrap sm:items-baseline">
        {([
          [t.attention, '件等你处理', t.attention ? 'var(--k-hot)' : undefined],
          [money(sp.today.cost), '今天花费'],
          [money(sp.week.cost), '近 7 天'],
          [`${online}/${state.agents.length}`, '个 Agent 在线', online < state.agents.length ? 'var(--k-amber-ink)' : undefined],
        ] as [string | number, string, string?][]).map(([n, unit, color]) => (
          <span key={unit} className="inline-flex items-baseline gap-1.5">
            <span className="k-num text-[23px] font-bold leading-none tracking-[-0.02em] text-k-ink" style={color ? { color } : undefined}>{n}</span>
            <span className="text-[12.5px] text-k-ink4">{unit}</span>
          </span>
        ))}
        {sp.week.missingCost > 0 && <span className="col-span-2 text-[12px] text-k-ink4">近 7 天另有 {sp.week.missingCost} 轮没报花费</span>}
      </div>

      <Section title="等你处理" aside={state.cards.length ? `${state.cards.length} 张卡片` : undefined}>
        {state.cards.length ? (
          <div className="grid gap-4 lg:grid-cols-2">
            {state.cards.map((c) => <Ticket key={`${c.id}:${c.version}`} card={c} onDone={refresh} />)}
          </div>
        ) : <p className="k-card px-6 py-8 text-center text-[14.5px] text-k-ink4">没有等你处理的事。</p>}
      </Section>

      <Section title="处理过的卡片" aside={onNavigate && (
        <button onClick={() => onNavigate('rules')} className="inline-flex items-center gap-1 text-k-ink3 hover:text-k-accent">
          规则与运行<ArrowRight className="h-3.5 w-3.5" />
        </button>
      )}>
        <div className="k-card divide-y divide-k-line-soft">
          {state.recentCards.slice(0, 10).map((c) => (
            <button key={c.id} onClick={() => setRecent(c)} className="grid w-full grid-cols-[44px_minmax(0,1fr)_auto] items-baseline gap-3 px-5 py-3 text-left hover:bg-k-sunk">
              <span className="k-mono text-[12.5px] text-k-ink4">#{c.id}</span>
              <span className="min-w-0">
                <span className="block truncate text-[14.5px] font-medium text-k-ink">{c.title}</span>
                <span className="block truncate text-[13px] text-k-ink4">{c.answer || c.result || c.rule}</span>
              </span>
              <span className="whitespace-nowrap text-right text-[12.5px] text-k-ink4">
                {when(c.closedAt || c.createdAt)}{c.by && <span className="block">{tr(BY, c.by)}</span>}
              </span>
            </button>
          ))}
          {!state.recentCards.length && <p className="px-5 py-6 text-center text-[14px] text-k-ink4">还没有</p>}
        </div>
      </Section>

      {recent && (
        <Drawer title={`#${recent.id} ${recent.title}`} sub={`${recent.rule} · ${when(recent.createdAt)}`} onClose={() => setRecent(null)}>
          <KV rows={[
            ['结果', recent.result],
            ['回答', recent.choice !== null ? recent.options[recent.choice] : recent.answer],
            ['谁', recent.by ? tr(BY, recent.by) : null],
            ['结案', when(recent.closedAt)],
            ['来自', names[recent.rule] ?? recent.rule],
          ]} />
          <p className="mt-5 whitespace-pre-line break-words text-[14.5px] leading-[1.8] text-k-ink2"><Linked text={recent.text} /></p>
        </Drawer>
      )}
    </div>
  )
}
