import { useState, useEffect, useMemo } from 'react'
import { ArrowLeft, Search, ArrowUpDown, ArrowDown, ArrowUp } from 'lucide-react'
import EntryPreview from '../components/EntryPreview'
import type { DomainOverview, DomainEntry } from '../types'

type SortKey = 'date' | 'confidence' | 'title'
type SortDir = 'asc' | 'desc'

interface DomainDetailViewProps {
  domainKey: string
  onBack: () => void
}

const getMetricColor = (value: number): string => {
  if (value >= 0.7) return 'bg-emerald-500'
  if (value >= 0.4) return 'bg-amber-500'
  return 'bg-rose-500'
}

const getMetricTextColor = (value: number): string => {
  if (value >= 0.7) return 'text-emerald-400'
  if (value >= 0.4) return 'text-amber-400'
  return 'text-rose-400'
}

export default function DomainDetailView({ domainKey, onBack }: DomainDetailViewProps) {
  const [domain, setDomain] = useState<DomainOverview | null>(null)
  const [loading, setLoading] = useState(true)
  const [searchQuery, setSearchQuery] = useState('')
  const [selectedEntry, setSelectedEntry] = useState<DomainEntry | null>(null)
  const [sortKey, setSortKey] = useState<SortKey>('date')
  const [sortDir, setSortDir] = useState<SortDir>('desc')

  useEffect(() => {
    fetchDomainDetail()
  }, [domainKey])

  const fetchDomainDetail = async () => {
    try {
      setLoading(true)
      const response = await fetch('/api/domains/overview')
      if (!response.ok) throw new Error('Failed to fetch domain')
      const result = await response.json()
      const foundDomain = result.domains.find((d: DomainOverview) => d.key === domainKey)
      setDomain(foundDomain || null)
    } catch (err) {
      console.error(err)
    } finally {
      setLoading(false)
    }
  }

  const sortedEntries = useMemo(() => {
    const filtered = domain?.entries.filter(entry =>
      entry.title.toLowerCase().includes(searchQuery.toLowerCase())
    ) || []

    return [...filtered].sort((a, b) => {
      let cmp = 0
      switch (sortKey) {
        case 'date':
          cmp = (a.created || '').localeCompare(b.created || '')
          break
        case 'confidence':
          cmp = (a.confidence ?? 0) - (b.confidence ?? 0)
          break
        case 'title':
          cmp = a.title.localeCompare(b.title)
          break
      }
      return sortDir === 'desc' ? -cmp : cmp
    })
  }, [domain?.entries, searchQuery, sortKey, sortDir])

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir(d => d === 'desc' ? 'asc' : 'desc')
    } else {
      setSortKey(key)
      setSortDir(key === 'title' ? 'asc' : 'desc')
    }
  }

  const SortIcon = ({ k }: { k: SortKey }) => {
    if (sortKey !== k) return <ArrowUpDown className="w-3 h-3 text-zinc-600" />
    return sortDir === 'desc'
      ? <ArrowDown className="w-3 h-3 text-indigo-400" />
      : <ArrowUp className="w-3 h-3 text-indigo-400" />
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-zinc-400">加载中...</div>
      </div>
    )
  }

  if (!domain) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-rose-400">域不存在</div>
      </div>
    )
  }

  const metrics = [
    { key: 'coverage', label: '覆盖率', value: domain.metrics.coverage },
    { key: 'depth_score', label: '深度', value: domain.metrics.depth_score },
    { key: 'freshness', label: '新鲜度', value: domain.metrics.freshness },
    { key: 'avg_confidence', label: '置信度', value: domain.metrics.avg_confidence }
  ]

  const coveredSubDomains = new Set<string>()
  domain.entries.forEach(entry => {
    entry.domain.forEach(d => {
      if (domain.sub_domains.includes(d)) {
        coveredSubDomains.add(d)
      }
    })
  })

  return (
    <div className="min-h-screen">
      {/* Header */}
      <div className="mb-6">
        <button
          onClick={onBack}
          className="flex items-center gap-2 text-zinc-400 hover:text-zinc-300 transition-colors mb-4"
        >
          <ArrowLeft className="w-4 h-4" />
          <span className="text-sm">返回主题总览</span>
        </button>

        <div className="flex items-center gap-4">
          <span className="text-5xl">{domain.icon}</span>
          <div>
            <h1 className="text-3xl font-bold text-zinc-100">{domain.label}</h1>
            <p className="text-zinc-500 mt-1">
              {domain.metrics.total_entries} 条目 · {domain.sub_domains.length} 子域
            </p>
          </div>
        </div>
      </div>

      {/* Metrics overview */}
      <div className="mb-6 grid grid-cols-4 gap-4">
        {metrics.map(({ key, label, value }) => (
          <div key={key} className="bg-zinc-900/40 backdrop-blur-sm rounded-xl border border-zinc-800/50 p-4">
            <div className="text-sm text-zinc-500 mb-2">{label}</div>
            <div className={`text-3xl font-bold ${getMetricTextColor(value)} mb-3`}>
              {(value * 100).toFixed(0)}
            </div>
            <div className="h-2 bg-zinc-800 rounded-full overflow-hidden">
              <div
                className={`h-full ${getMetricColor(value)} transition-all duration-300`}
                style={{ width: `${value * 100}%` }}
              />
            </div>
          </div>
        ))}
      </div>

      {/* Sub-domains */}
      {domain.sub_domains.length > 0 && (
        <div className="mb-6 bg-zinc-900/40 backdrop-blur-sm rounded-xl border border-zinc-800/50 p-5">
          <h2 className="text-sm font-medium text-zinc-400 mb-3">子域覆盖</h2>
          <div className="flex flex-wrap gap-2">
            {domain.sub_domains.map(subDomain => {
              const isCovered = coveredSubDomains.has(subDomain)
              return (
                <span
                  key={subDomain}
                  className={`px-3 py-1.5 text-sm rounded-lg transition-colors ${
                    isCovered
                      ? 'bg-blue-500/20 text-blue-300 border border-blue-500/30'
                      : 'bg-zinc-800/50 text-zinc-500 border border-zinc-700/50'
                  }`}
                >
                  {subDomain}
                </span>
              )
            })}
          </div>
        </div>
      )}

      {/* Search */}
      <div className="mb-4 relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-zinc-500" />
        <input
          type="text"
          placeholder="搜索条目..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          className="w-full pl-10 pr-4 py-2.5 border border-zinc-800/50 rounded-lg bg-zinc-900/40 backdrop-blur-sm text-zinc-100 placeholder-zinc-500 focus:ring-2 focus:ring-blue-500/50 focus:border-blue-500/50 transition-all"
        />
      </div>

      {/* Entries list */}
      <div className="bg-zinc-900/40 backdrop-blur-sm rounded-xl border border-zinc-800/50 overflow-hidden">
        <div className="p-4 border-b border-zinc-800/50 flex items-center justify-between">
          <h2 className="text-sm font-medium text-zinc-400">
            条目列表 ({sortedEntries.length})
          </h2>
          <div className="flex items-center gap-1">
            <button
              onClick={() => toggleSort('date')}
              className={`flex items-center gap-1 px-2.5 py-1 text-xs rounded-md transition-colors ${
                sortKey === 'date' ? 'bg-indigo-500/15 text-indigo-400' : 'text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800/50'
              }`}
            >
              日期 <SortIcon k="date" />
            </button>
            <button
              onClick={() => toggleSort('confidence')}
              className={`flex items-center gap-1 px-2.5 py-1 text-xs rounded-md transition-colors ${
                sortKey === 'confidence' ? 'bg-indigo-500/15 text-indigo-400' : 'text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800/50'
              }`}
            >
              置信度 <SortIcon k="confidence" />
            </button>
            <button
              onClick={() => toggleSort('title')}
              className={`flex items-center gap-1 px-2.5 py-1 text-xs rounded-md transition-colors ${
                sortKey === 'title' ? 'bg-indigo-500/15 text-indigo-400' : 'text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800/50'
              }`}
            >
              名称 <SortIcon k="title" />
            </button>
          </div>
        </div>
        <div className="divide-y divide-zinc-800/50">
          {sortedEntries.length === 0 ? (
            <div className="p-8 text-center text-zinc-500">
              {searchQuery ? '未找到匹配的条目' : '暂无条目'}
            </div>
          ) : (
            sortedEntries.map(entry => (
              <button
                key={entry.id}
                onClick={() => setSelectedEntry(entry)}
                className="w-full text-left px-5 py-3.5 hover:bg-zinc-800/30 transition-colors group"
              >
                <div className="flex items-center justify-between gap-4">
                  <div className="flex-1 min-w-0">
                    <span className="text-sm text-zinc-200 group-hover:text-zinc-100">
                      {entry.title}
                    </span>
                  </div>
                  <div className="flex items-center gap-3 flex-shrink-0">
                    {entry.created && (
                      <span className="text-xs text-zinc-600 font-mono tabular-nums">
                        {entry.created}
                      </span>
                    )}
                    <span className="text-xs px-2 py-0.5 rounded-md bg-zinc-800/50 text-zinc-400 border border-zinc-700/50">
                      {entry.type}
                    </span>
                    <span className="text-xs px-2 py-0.5 rounded-md bg-zinc-800/50 text-zinc-500 border border-zinc-700/50">
                      {entry.depth}
                    </span>
                    {entry.confidence !== null && (
                      <span className={`text-sm font-medium tabular-nums w-10 text-right ${getMetricTextColor(entry.confidence)}`}>
                        {(entry.confidence * 100).toFixed(0)}%
                      </span>
                    )}
                  </div>
                </div>
              </button>
            ))
          )}
        </div>
      </div>

      {/* Entry preview modal */}
      {selectedEntry && (
        <EntryPreview
          entryId={selectedEntry.id}
          onClose={() => setSelectedEntry(null)}
        />
      )}
    </div>
  )
}
