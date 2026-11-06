// Small pieces the automation pages share: a side drawer, section headings, the live status line
// and the box that shows what kg-web or the hub said when something was refused.
import { useEffect, type ReactNode } from 'react'
import { X } from 'lucide-react'

export function Drawer({ title, sub, onClose, children, wide }: {
  title: ReactNode; sub?: ReactNode; onClose: () => void; children: ReactNode; wide?: boolean
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true">
      <div className="absolute inset-0 bg-[color-mix(in_srgb,var(--k-ink)_22%,transparent)]" onClick={onClose} />
      <div className={`k-app relative flex h-full w-full ${wide ? 'max-w-[820px]' : 'max-w-[640px]'} flex-col border-l border-k-line bg-k-bg`}
        style={{ boxShadow: 'var(--k-shadow-pop)' }}>
        <header className="flex items-center gap-3 border-b border-k-line-soft px-5 py-3 sm:px-7">
          <div className="min-w-0">
            <h2 className="truncate text-[17px] font-[650] text-k-ink">{title}</h2>
            {sub && <div className="truncate text-[12.5px] text-k-ink4">{sub}</div>}
          </div>
          <button onClick={onClose} className="ml-auto rounded-full p-1.5 text-k-ink3 hover:bg-k-sunk" aria-label="关闭"><X className="h-5 w-5" /></button>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-16 pt-5 sm:px-7">{children}</div>
      </div>
    </div>
  )
}

export function Section({ title, aside, children, className = '' }: { title: ReactNode; aside?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`mt-9 min-w-0 ${className}`}>
      <div className="mb-3 flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 className="text-[19px] font-[650] text-k-ink">{title}</h2>
        {aside && <div className="text-[13px] text-k-ink4">{aside}</div>}
      </div>
      {children}
    </section>
  )
}

export function Live({ at, error }: { at: Date | null; error: string }) {
  const on = !!at && !error
  return (
    <span className="inline-flex items-center gap-2 text-[13px] text-k-ink4" title={error || undefined}>
      <span className="h-2 w-2 rounded-full" style={{ background: on ? 'var(--k-ok)' : 'var(--k-hot)' }} />
      {error ? `读不到中台：${error}` : at ? `更新于 ${at.toLocaleTimeString('zh-CN', { hour12: false })}` : '连接中…'}
    </span>
  )
}

export function Problem({ message, issues = [], fromHub, onClose }: { message: string; issues?: string[]; fromHub?: boolean; onClose?: () => void }) {
  return (
    <div role="alert" className="rounded-tile border px-4 py-3 text-[14px]" style={{ background: 'var(--k-hot-soft)', borderColor: 'color-mix(in srgb, var(--k-hot) 30%, transparent)' }}>
      <div className="flex items-start gap-2">
        <span className="font-semibold text-k-hot">{fromHub ? '中台说：' : ''}{message}</span>
        {onClose && <button onClick={onClose} className="ml-auto shrink-0 text-[12.5px] text-k-ink4 hover:text-k-ink">关闭</button>}
      </div>
      {issues.length > 0 && (
        <ul className="mt-2 list-disc space-y-1 pl-5 text-[13.5px] text-k-ink2">
          {issues.map((x, i) => <li key={i} className="break-words">{x}</li>)}
        </ul>
      )}
    </div>
  )
}

/** A key → value grid that stacks on narrow screens. */
export function KV({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[76px_minmax(0,1fr)] gap-x-4 gap-y-2 text-[14px]">
      {rows.filter(([, v]) => v !== null && v !== undefined && v !== '').map(([k, v]) => (
        <div key={k} className="contents"><dt className="text-k-ink4">{k}</dt><dd className="min-w-0 break-words text-k-ink2">{v}</dd></div>
      ))}
    </dl>
  )
}

export const btn = 'inline-flex shrink-0 items-center whitespace-nowrap justify-center gap-1.5 rounded-full border border-k-line bg-k-surface px-3.5 py-1.5 text-[13.5px] text-k-ink2 transition-colors hover:border-k-accent hover:text-k-accent disabled:cursor-not-allowed disabled:opacity-50'
export const btnPrimary = 'inline-flex items-center justify-center gap-1.5 rounded-full bg-k-accent px-4 py-1.5 text-[13.5px] font-semibold text-k-accent-contrast transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50'
export const field = 'w-full min-w-0 rounded-control border border-k-line bg-k-surface px-3 py-2 text-[14px] text-k-ink outline-none placeholder:text-k-ink4 focus:border-k-accent'
