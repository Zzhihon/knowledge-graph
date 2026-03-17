import type { DomainOverview } from '../types'

interface DomainCardProps {
  domain: DomainOverview
  onClick: () => void
}

// Domain-specific color themes
const domainThemes: Record<string, {
  icon: string
  ring: string
  glow: string
  text: string
}> = {
  'cloud-native': {
    icon: 'bg-purple-500/10 border-purple-500/20',
    ring: 'stroke-purple-500',
    glow: 'bg-purple-500',
    text: 'text-purple-400'
  },
  'networking': {
    icon: 'bg-zinc-500/10 border-zinc-500/20',
    ring: 'stroke-zinc-400',
    glow: 'bg-zinc-400',
    text: 'text-zinc-300'
  },
  'ai-agent': {
    icon: 'bg-emerald-500/10 border-emerald-500/20',
    ring: 'stroke-emerald-500',
    glow: 'bg-emerald-500',
    text: 'text-emerald-400'
  },
  'algorithm': {
    icon: 'bg-orange-500/10 border-orange-500/20',
    ring: 'stroke-orange-500',
    glow: 'bg-orange-500',
    text: 'text-orange-400'
  },
  'databases': {
    icon: 'bg-blue-500/10 border-blue-500/20',
    ring: 'stroke-blue-500',
    glow: 'bg-blue-500',
    text: 'text-blue-400'
  },
  'golang': {
    icon: 'bg-cyan-500/10 border-cyan-500/20',
    ring: 'stroke-cyan-500',
    glow: 'bg-cyan-500',
    text: 'text-cyan-400'
  },
  'distributed-systems': {
    icon: 'bg-violet-500/10 border-violet-500/20',
    ring: 'stroke-violet-500',
    glow: 'bg-violet-500',
    text: 'text-violet-400'
  },
  'frontend': {
    icon: 'bg-pink-500/10 border-pink-500/20',
    ring: 'stroke-pink-500',
    glow: 'bg-pink-500',
    text: 'text-pink-400'
  },
  'ai-infra': {
    icon: 'bg-teal-500/10 border-teal-500/20',
    ring: 'stroke-teal-500',
    glow: 'bg-teal-500',
    text: 'text-teal-400'
  }
}

const getMetricColor = (value: number): string => {
  if (value >= 0.7) return 'bg-zinc-400'
  if (value >= 0.4) return 'bg-zinc-500'
  return 'bg-rose-500'
}

export default function DomainCard({ domain, onClick }: DomainCardProps) {
  const metrics = [
    { key: 'coverage', label: '覆盖率', value: domain.metrics.coverage },
    { key: 'depth_score', label: '深度', value: domain.metrics.depth_score },
    { key: 'freshness', label: '新鲜度', value: domain.metrics.freshness },
    { key: 'avg_confidence', label: '置信度', value: domain.metrics.avg_confidence }
  ]

  const overallScore = metrics.reduce((sum, m) => sum + m.value, 0) / metrics.length
  const circumference = 2 * Math.PI * 22
  const strokeDashoffset = circumference - (overallScore * circumference)

  // Get theme for this domain
  const theme = domainThemes[domain.key] || domainThemes['golang']

  // Get visible sub-domains (max 3, then show +N)
  const visibleSubDomains = domain.sub_domains.slice(0, 3)
  const remainingCount = domain.sub_domains.length - 3

  return (
    <button
      onClick={onClick}
      className="group relative overflow-hidden rounded-2xl border border-zinc-800/60 bg-zinc-950/50 p-6 transition-all duration-300 hover:border-zinc-700/50 hover:bg-zinc-900/50 text-left w-full"
    >
      {/* Ambient glow on hover */}
      <div className={`absolute -right-20 -top-20 w-40 h-40 rounded-full blur-[60px] opacity-0 group-hover:opacity-20 transition-opacity duration-500 ${theme.glow}`}></div>

      <div className="relative z-10 flex justify-between items-start">
        {/* Left: Icon and info */}
        <div className="flex gap-4">
          <div className={`mt-0.5 flex h-12 w-12 shrink-0 items-center justify-center rounded-xl border ${theme.icon}`}>
            <span className="text-2xl">{domain.icon}</span>
          </div>
          <div>
            <h3 className="text-base font-semibold text-zinc-100 tracking-tight group-hover:text-white transition-colors">
              {domain.label}
            </h3>
            <p className="mt-1 flex items-center gap-2 text-xs text-zinc-500">
              <span>{domain.metrics.total_entries} Nodes</span>
              <span className="h-1 w-1 rounded-full bg-zinc-700"></span>
              <span>{domain.sub_domains.length} Subs</span>
            </p>

            {/* Tags */}
            <div className="mt-4 flex flex-wrap items-center gap-1.5">
              {visibleSubDomains.map(subDomain => (
                <span
                  key={subDomain}
                  className="px-2 py-0.5 text-[10px] font-medium text-zinc-400 bg-zinc-800/50 rounded-md border border-zinc-800/80"
                >
                  {subDomain}
                </span>
              ))}
              {remainingCount > 0 && (
                <span className="px-2 py-0.5 text-[10px] font-medium text-zinc-500 bg-transparent rounded-md border border-zinc-800/80 border-dashed">
                  +{remainingCount}
                </span>
              )}
            </div>
          </div>
        </div>

        {/* Right: Score ring */}
        <div className="shrink-0 pl-4 flex flex-col items-center">
          <div className="relative flex items-center justify-center">
            <svg className="w-[60px] h-[60px] transform -rotate-90">
              <circle
                cx="30"
                cy="30"
                r="22"
                stroke="currentColor"
                strokeWidth="4"
                fill="transparent"
                className="text-zinc-800/80"
              />
              <circle
                cx="30"
                cy="30"
                r="22"
                stroke="currentColor"
                strokeWidth="4"
                fill="transparent"
                strokeDasharray={circumference}
                strokeDashoffset={strokeDashoffset}
                strokeLinecap="round"
                className={`${theme.ring} transition-all duration-1000 ease-out`}
              />
            </svg>
            <div className="absolute flex flex-col items-center">
              <span className={`text-sm font-bold leading-none ${theme.text}`}>
                {(overallScore * 100).toFixed(0)}
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* Bottom: Mini metrics grid */}
      <div className="mt-6 pt-5 border-t border-zinc-800/50 grid grid-cols-4 gap-2">
        {metrics.map(({ key, label, value }) => {
          const isWarning = value < 0.6
          return (
            <div key={key} className="flex flex-col gap-1">
              <span className="text-[10px] font-medium text-zinc-500 uppercase tracking-wider">
                {label}
              </span>
              <div className="flex items-center gap-1.5">
                <div className="flex-1 h-1 bg-zinc-800 rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full ${isWarning ? 'bg-rose-500/80' : getMetricColor(value)}`}
                    style={{ width: `${value * 100}%` }}
                  ></div>
                </div>
                <span className={`text-xs font-mono ${isWarning ? 'text-rose-400' : 'text-zinc-400'}`}>
                  {(value * 100).toFixed(0)}
                </span>
              </div>
            </div>
          )
        })}
      </div>
    </button>
  )
}



