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
    icon: 'bg-k-violet/10 border-k-violet/20',
    ring: 'stroke-k-violet',
    glow: 'bg-k-violet',
    text: 'text-k-violet'
  },
  'networking': {
    icon: 'bg-k-ink4/10 border-k-ink4/20',
    ring: 'stroke-k-ink3',
    glow: 'bg-k-ink',
    text: 'text-k-ink2'
  },
  'ai-agent': {
    icon: 'bg-k-ok/10 border-k-ok/20',
    ring: 'stroke-k-ok',
    glow: 'bg-k-ok',
    text: 'text-k-ok'
  },
  'algorithm': {
    icon: 'bg-k-amber/10 border-k-amber/20',
    ring: 'stroke-k-amber',
    glow: 'bg-k-amber',
    text: 'text-k-amber-ink'
  },
  'databases': {
    icon: 'bg-k-accent/10 border-k-accent/20',
    ring: 'stroke-k-accent',
    glow: 'bg-k-accent',
    text: 'text-k-accent'
  },
  'golang': {
    icon: 'bg-k-accent/10 border-k-accent/20',
    ring: 'stroke-k-accent',
    glow: 'bg-k-accent',
    text: 'text-k-accent'
  },
  'distributed-systems': {
    icon: 'bg-k-violet/10 border-k-violet/20',
    ring: 'stroke-k-violet',
    glow: 'bg-k-violet',
    text: 'text-k-violet'
  },
  'frontend': {
    icon: 'bg-k-hot/10 border-k-hot/20',
    ring: 'stroke-k-hot',
    glow: 'bg-k-hot',
    text: 'text-k-hot'
  },
  'ai-infra': {
    icon: 'bg-k-accent/10 border-k-accent/20',
    ring: 'stroke-k-accent',
    glow: 'bg-k-accent',
    text: 'text-k-accent'
  }
}

const getMetricColor = (value: number): string => {
  if (value >= 0.7) return 'bg-k-ink'
  if (value >= 0.4) return 'bg-k-ink4'
  return 'bg-k-hot'
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
      className="group relative overflow-hidden rounded-2xl border border-k-line/60 bg-k-bg/50 p-6 transition-all duration-300 hover:border-k-line-strong/50 hover:bg-k-surface/50 text-left w-full"
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
            <h3 className="text-base font-semibold text-k-ink tracking-tight group-hover:text-white transition-colors">
              {domain.label}
            </h3>
            <p className="mt-1 flex items-center gap-2 text-xs text-k-ink4">
              <span>{domain.metrics.total_entries} Nodes</span>
              <span className="h-1 w-1 rounded-full bg-k-muted"></span>
              <span>{domain.sub_domains.length} Subs</span>
            </p>

            {/* Tags */}
            <div className="mt-4 flex flex-wrap items-center gap-1.5">
              {visibleSubDomains.map(subDomain => (
                <span
                  key={subDomain}
                  className="px-2 py-0.5 text-[10px] font-medium text-k-ink3 bg-k-sunk/50 rounded-md border border-k-line/80"
                >
                  {subDomain}
                </span>
              ))}
              {remainingCount > 0 && (
                <span className="px-2 py-0.5 text-[10px] font-medium text-k-ink4 bg-transparent rounded-md border border-k-line/80 border-dashed">
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
                className="text-k-line-strong/80"
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
      <div className="mt-6 pt-5 border-t border-k-line/50 grid grid-cols-4 gap-2">
        {metrics.map(({ key, label, value }) => {
          const isWarning = value < 0.6
          return (
            <div key={key} className="flex flex-col gap-1">
              <span className="text-[10px] font-medium text-k-ink4 uppercase tracking-wider">
                {label}
              </span>
              <div className="flex items-center gap-1.5">
                <div className="flex-1 h-1 bg-k-sunk rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full ${isWarning ? 'bg-k-hot/80' : getMetricColor(value)}`}
                    style={{ width: `${value * 100}%` }}
                  ></div>
                </div>
                <span className={`text-xs font-mono ${isWarning ? 'text-k-hot' : 'text-k-ink3'}`}>
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



