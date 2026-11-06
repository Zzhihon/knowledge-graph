// What a planned module shows until it ships: what it will do and the issue that builds it.
import { ArrowRight, ArrowUpRight } from 'lucide-react'
import { issueUrl, type Module } from '../lib/modules'

export default function PlannedView({ module: m, onNavigate }: { module: Module; onNavigate: (id: string) => void }) {
  const Icon = m.icon
  return (
    <div className="mx-auto w-full max-w-[760px] pb-16">
      <div className="flex items-center gap-3">
        <Icon className="h-6 w-6 text-k-ink4" />
        <h1 className="text-[28px] font-[650] text-k-ink">{m.label}</h1>
        <span className="k-chip" style={{ ['--c' as string]: 'var(--k-ink-4)', ['--cs' as string]: 'var(--k-line-soft)' }}>规划中{m.issue ? ` #${m.issue}` : ''}</span>
      </div>
      <div className="k-card mt-6 px-6 py-5">
        <p className="text-[15.5px] leading-[1.8] text-k-ink2">{m.blurb ?? '这个模块还没做。'}</p>
        <p className="mt-3 text-[14px] leading-[1.75] text-k-ink4">
          还没上线，所以这里先放说明。数据接上之后，总览首页对应的那一块也会从"规划中"换成真实内容。
        </p>
        <div className="mt-4 flex flex-wrap gap-3 text-[14px]">
          {m.issue && (
            <a href={issueUrl(m.issue)} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-k-accent hover:underline">
              看 issue #{m.issue} 的方案和进度<ArrowUpRight className="h-4 w-4" />
            </a>
          )}
          <button onClick={() => onNavigate('overview')} className="inline-flex items-center gap-1 text-k-ink3 hover:text-k-accent">
            回总览<ArrowRight className="h-4 w-4" />
          </button>
        </div>
      </div>
    </div>
  )
}
