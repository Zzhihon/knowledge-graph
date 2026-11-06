// The 异常 badge every page shows in its top-right corner, and the list of open incidents it opens.
import { Siren } from 'lucide-react'
import { Drawer } from './HubBits'
import { SEVERITY, agoUnix, severityTone, type Incident } from '../../lib/overview'
import { issueUrl, isLive } from '../../lib/modules'

export function IncidentBadge({ count, error, onOpen, compact }: {
  count: number | null; error: string; onOpen: () => void; compact?: boolean
}) {
  const bad = (count ?? 0) > 0
  const label = error && count === null ? '异常数读不到' : bad ? `${count} 个未关闭事故` : '没有未关闭的事故'
  return (
    <button onClick={onOpen} title={error ? `读不到事故：${error}` : label} aria-label={label}
      className={`inline-flex h-8 shrink-0 items-center gap-1.5 rounded-full border px-3 text-[13px] transition-colors ${
        bad ? 'border-transparent bg-k-hot-soft font-semibold text-k-hot hover:opacity-90'
          : 'border-k-line bg-k-surface text-k-ink4 hover:text-k-ink'}`}>
      <Siren className="h-3.5 w-3.5" />
      {bad ? <>{!compact && '异常 '}<span className="k-num">{count}</span></> : error && count === null ? '?' : !compact && '无异常'}
    </button>
  )
}

export function IncidentDrawer({ items, error, onClose, onNavigate }: {
  items: Incident[]; error: string; onClose: () => void; onNavigate: (id: string) => void
}) {
  return (
    <Drawer title="未关闭的事故" sub={`${items.length} 个 · 每 30 秒刷新`} onClose={onClose}>
      {error && <p className="mb-4 text-[13.5px] text-k-hot">读不到事故：{error}</p>}
      {items.length === 0 && !error && <p className="py-10 text-center text-[14.5px] text-k-ink4">没有未关闭的事故。</p>}
      <ul className="space-y-3">
        {items.map((x) => {
          const [c, cs] = severityTone(x.severity)
          return (
            <li key={x.id} className="k-card relative overflow-hidden px-5 py-3.5">
              <span aria-hidden className="absolute inset-y-0 left-0 w-[3px]" style={{ background: `var(${c})` }} />
              <div className="flex flex-wrap items-center gap-2 text-[12.5px] text-k-ink4">
                <span className="k-chip" style={{ ['--c' as string]: `var(${c})`, ['--cs' as string]: `var(${cs})` }}>{SEVERITY[x.severity] ?? x.severity}</span>
                <span className="k-mono">#{x.id}</span>
                <span className="ml-auto">{agoUnix(x.opened_at)}开</span>
              </div>
              <div className="mt-1.5 text-[15px] font-[600] text-k-ink">{x.summary || x.rule}</div>
              <div className="mt-0.5 break-all text-[12.5px] text-k-ink4"><span className="k-mono">{x.resource_id}</span> · {x.rule}</div>
            </li>
          )
        })}
      </ul>
      <p className="mt-6 text-[13px] text-k-ink4">
        {isLive('incidents')
          ? <button className="text-k-accent hover:underline" onClick={() => { onClose(); onNavigate('incidents') }}>打开事故页</button>
          : <>事故页（证据、自愈、结案）规划中 <a className="text-k-accent hover:underline" href={issueUrl(9)} target="_blank" rel="noopener noreferrer">#9</a></>}
      </p>
    </Drawer>
  )
}
