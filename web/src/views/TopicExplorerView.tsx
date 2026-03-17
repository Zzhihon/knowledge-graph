import { useState, useEffect } from 'react'
import { Search, Filter, BarChart2 } from 'lucide-react'
import DomainCard from '../components/DomainCard'
import DomainDetailView from './DomainDetailView'
import type { DomainsOverviewResponse, DomainOverview } from '../types'

type SortBy = 'entries' | 'score'

export default function TopicExplorerView() {
  const [data, setData] = useState<DomainsOverviewResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [sortBy, setSortBy] = useState<SortBy>('score')
  const [selectedDomain, setSelectedDomain] = useState<string | null>(null)

  useEffect(() => {
    fetchDomainsOverview()
  }, [])

  const fetchDomainsOverview = async () => {
    try {
      setLoading(true)
      const response = await fetch('/api/domains/overview')
      if (!response.ok) throw new Error('Failed to fetch domains overview')
      const result = await response.json()
      setData(result)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error')
    } finally {
      setLoading(false)
    }
  }

  const calculateOverallScore = (domain: DomainOverview): number => {
    const { coverage, depth_score, freshness, avg_confidence } = domain.metrics
    return (coverage + depth_score + freshness + avg_confidence) / 4
  }

  const filteredAndSortedDomains = data?.domains
    .filter(domain =>
      domain.label.toLowerCase().includes(searchQuery.toLowerCase()) ||
      domain.key.toLowerCase().includes(searchQuery.toLowerCase())
    )
    .sort((a, b) => {
      if (sortBy === 'entries') {
        return b.metrics.total_entries - a.metrics.total_entries
      } else {
        return calculateOverallScore(b) - calculateOverallScore(a)
      }
    }) || []

  const totalDomains = data?.domains.length || 0
  const totalEntries = data?.domains.reduce((sum, d) => sum + d.metrics.total_entries, 0) || 0
  const avgScore = totalDomains > 0
    ? (data?.domains.reduce((sum, d) => sum + calculateOverallScore(d), 0) || 0) / totalDomains
    : 0

  // Show detail view if a domain is selected
  if (selectedDomain) {
    return (
      <DomainDetailView
        domainKey={selectedDomain}
        onBack={() => setSelectedDomain(null)}
      />
    )
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-zinc-400">加载中...</div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-rose-400">错误: {error}</div>
      </div>
    )
  }

  return (
    <div className="animate-in fade-in duration-500 pb-10">
      {/* Hero Banner */}
      <div className="relative overflow-hidden rounded-2xl border border-zinc-800/60 bg-zinc-900/20 mb-8">
        {/* Decorative background */}
        <div className="absolute inset-0 bg-[linear-gradient(to_right,#18181b_1px,transparent_1px),linear-gradient(to_bottom,#18181b_1px,transparent_1px)] bg-[size:4rem_4rem] opacity-[0.03]"></div>
        <div className="absolute -top-24 -right-24 w-96 h-96 bg-indigo-500/10 rounded-full blur-3xl pointer-events-none"></div>

        <div className="relative p-8 flex flex-col md:flex-row md:items-center justify-between gap-6">
          <div>
            <h1 className="text-2xl font-semibold text-zinc-100 tracking-tight mb-2">
              全域知识大盘
            </h1>
            <p className="text-sm text-zinc-400 max-w-md">
              监控你的知识图谱健康状态。系统已基于 Qdrant 和 SurrealDB 完成最新一次索引同步。
            </p>
          </div>

          <div className="flex gap-8 md:gap-12">
            <div className="flex flex-col">
              <span className="text-xs font-medium text-zinc-500 uppercase tracking-wider mb-1">
                Knowledge Domains
              </span>
              <span className="text-4xl font-light text-zinc-100">{totalDomains}</span>
            </div>
            <div className="flex flex-col">
              <span className="text-xs font-medium text-zinc-500 uppercase tracking-wider mb-1">
                Total Nodes
              </span>
              <span className="text-4xl font-light text-zinc-100">{totalEntries}</span>
            </div>
            <div className="flex flex-col">
              <span className="text-xs font-medium text-zinc-500 uppercase tracking-wider mb-1">
                Avg Score
              </span>
              <span className="text-4xl font-light text-emerald-400 flex items-baseline gap-1">
                {(avgScore * 100).toFixed(0)}
                <span className="text-lg text-emerald-500/50">%</span>
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* Control bar */}
      <div className="flex flex-col sm:flex-row gap-4 mb-6 items-center justify-between">
        <div className="relative w-full sm:max-w-xs">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-zinc-500" />
          <input
            type="text"
            placeholder="检索知识域..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full bg-zinc-900/50 border border-zinc-800 rounded-xl pl-9 pr-4 py-2 text-sm text-zinc-200 focus:outline-none focus:border-indigo-500/50 focus:bg-zinc-900 transition-all placeholder:text-zinc-600"
          />
        </div>

        <div className="flex items-center gap-2">
          <button className="px-3 py-2 text-xs font-medium text-zinc-400 hover:text-zinc-200 bg-zinc-900/50 border border-zinc-800 rounded-lg flex items-center gap-2 transition-colors">
            <Filter className="w-3.5 h-3.5" /> 筛选条件
          </button>
          <button
            onClick={() => setSortBy(sortBy === 'score' ? 'entries' : 'score')}
            className="px-3 py-2 text-xs font-medium text-zinc-400 hover:text-zinc-200 bg-zinc-900/50 border border-zinc-800 rounded-lg flex items-center gap-2 transition-colors"
          >
            <BarChart2 className="w-3.5 h-3.5" />
            排序: {sortBy === 'score' ? '综合得分' : '条目数量'}
          </button>
        </div>
      </div>

      {/* Domain cards grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        {filteredAndSortedDomains.map(domain => (
          <DomainCard
            key={domain.key}
            domain={domain}
            onClick={() => setSelectedDomain(domain.key)}
          />
        ))}
      </div>

      {filteredAndSortedDomains.length === 0 && (
        <div className="text-center py-16 text-zinc-500">
          未找到匹配的领域
        </div>
      )}
    </div>
  )
}
