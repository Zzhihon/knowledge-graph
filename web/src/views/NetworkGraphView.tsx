import { useState, useEffect, useMemo } from 'react'
import { Loader2, Filter, Eye, EyeOff, Maximize2, Minimize2 } from 'lucide-react'
import NetworkGraph, { DOMAIN_COLORS, EDGE_COLORS, getDomainColor } from '../components/NetworkGraph'
import { get } from '../api/client'
import type { NetworkData } from '../types'

interface Props {
  onPreviewEntry: (id: string) => void
}

export default function NetworkGraphView({ onPreviewEntry }: Props) {
  const [data, setData] = useState<NetworkData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Filter state
  const [enabledDomains, setEnabledDomains] = useState<Set<string>>(new Set())
  const [enabledEdgeTypes, setEnabledEdgeTypes] = useState<Set<string>>(new Set(['references', 'prerequisites', 'supersedes']))
  const [showFilters, setShowFilters] = useState(false)
  const [isFullscreen, setIsFullscreen] = useState(false)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    get<NetworkData>('/graph/network')
      .then((d) => {
        if (cancelled) return
        setData(d)
        setEnabledDomains(new Set(d.meta.domains))
        setEnabledEdgeTypes(new Set(d.meta.edge_types))
      })
      .catch((err) => {
        if (!cancelled) setError(err.message || '加载失败')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => { cancelled = true }
  }, [])

  const toggleDomain = (domain: string) => {
    setEnabledDomains((prev) => {
      const next = new Set(prev)
      if (next.has(domain)) next.delete(domain)
      else next.add(domain)
      return next
    })
  }

  const toggleEdgeType = (et: string) => {
    setEnabledEdgeTypes((prev) => {
      const next = new Set(prev)
      if (next.has(et)) next.delete(et)
      else next.add(et)
      return next
    })
  }

  const toggleAllDomains = () => {
    if (!data) return
    if (enabledDomains.size === data.meta.domains.length) {
      setEnabledDomains(new Set())
    } else {
      setEnabledDomains(new Set(data.meta.domains))
    }
  }

  const edgeLabels: Record<string, string> = {
    references: '引用',
    prerequisites: '前置依赖',
    supersedes: '替代演进',
  }

  // Domain color chip helper
  const domainChips = useMemo(() => {
    if (!data) return []
    return data.meta.domains.map((d) => ({
      name: d,
      color: getDomainColor([d]),
      known: !!DOMAIN_COLORS[d.toLowerCase()],
    }))
  }, [data])

  const toggleFullscreen = () => {
    if (!document.fullscreenElement) {
      document.documentElement.requestFullscreen().then(() => setIsFullscreen(true)).catch(() => {})
    } else {
      document.exitFullscreen().then(() => setIsFullscreen(false)).catch(() => {})
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="flex flex-col items-center gap-3 text-zinc-500">
          <Loader2 className="w-6 h-6 animate-spin" />
          <span className="text-sm">正在加载知识网络…</span>
        </div>
      </div>
    )
  }

  if (error || !data) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-sm text-red-400">{error || '数据为空'}</div>
      </div>
    )
  }

  return (
    <div className="relative w-full h-full">
      {/* Graph */}
      <NetworkGraph
        nodes={data.nodes}
        edges={data.edges}
        filteredDomains={enabledDomains}
        filteredEdgeTypes={enabledEdgeTypes}
        onNodeClick={onPreviewEntry}
      />

      {/* Stats badge (top-left) */}
      <div className="absolute top-4 left-4 flex items-center gap-2 px-3 py-1.5 bg-zinc-900/90 border border-zinc-800/50 rounded-lg text-xs text-zinc-400 backdrop-blur-sm">
        <span className="text-zinc-100 font-medium">{data.meta.node_count}</span> 节点
        <span className="text-zinc-600">·</span>
        <span className="text-zinc-100 font-medium">{data.meta.edge_count}</span> 边
      </div>

      {/* Controls (top-right) */}
      <div className="absolute top-4 right-4 flex items-center gap-2">
        <button
          onClick={() => setShowFilters(!showFilters)}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors backdrop-blur-sm border ${
            showFilters
              ? 'bg-indigo-500/20 text-indigo-300 border-indigo-500/30'
              : 'bg-zinc-900/90 text-zinc-400 border-zinc-800/50 hover:text-zinc-200'
          }`}
        >
          <Filter className="w-3.5 h-3.5" />
          过滤器
        </button>
        <button
          onClick={toggleFullscreen}
          className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs bg-zinc-900/90 text-zinc-400 border border-zinc-800/50 hover:text-zinc-200 transition-colors backdrop-blur-sm"
        >
          {isFullscreen ? <Minimize2 className="w-3.5 h-3.5" /> : <Maximize2 className="w-3.5 h-3.5" />}
        </button>
      </div>

      {/* Filter panel (slide-in) */}
      {showFilters && (
        <div className="absolute top-14 right-4 w-64 bg-zinc-900/95 border border-zinc-800/50 rounded-xl shadow-2xl backdrop-blur-md overflow-hidden">
          {/* Domain filters */}
          <div className="p-3 border-b border-zinc-800/50">
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-medium text-zinc-400">领域过滤</span>
              <button onClick={toggleAllDomains} className="text-xs text-indigo-400 hover:text-indigo-300">
                {enabledDomains.size === data.meta.domains.length ? '取消全选' : '全选'}
              </button>
            </div>
            <div className="space-y-1 max-h-48 overflow-y-auto">
              {domainChips.map((d) => (
                <button
                  key={d.name}
                  onClick={() => toggleDomain(d.name)}
                  className={`w-full flex items-center gap-2 px-2 py-1 rounded text-xs transition-colors ${
                    enabledDomains.has(d.name)
                      ? 'text-zinc-200'
                      : 'text-zinc-600'
                  }`}
                >
                  {enabledDomains.has(d.name)
                    ? <Eye className="w-3 h-3 text-zinc-500" />
                    : <EyeOff className="w-3 h-3 text-zinc-700" />
                  }
                  <span
                    className="w-2.5 h-2.5 rounded-full shrink-0"
                    style={{ backgroundColor: d.color, opacity: enabledDomains.has(d.name) ? 1 : 0.3 }}
                  />
                  <span className="truncate">{d.name}</span>
                </button>
              ))}
            </div>
          </div>

          {/* Edge type filters */}
          <div className="p-3">
            <span className="text-xs font-medium text-zinc-400 block mb-2">边类型</span>
            <div className="space-y-1">
              {data.meta.edge_types.map((et) => (
                <button
                  key={et}
                  onClick={() => toggleEdgeType(et)}
                  className={`w-full flex items-center gap-2 px-2 py-1 rounded text-xs transition-colors ${
                    enabledEdgeTypes.has(et)
                      ? 'text-zinc-200'
                      : 'text-zinc-600'
                  }`}
                >
                  {enabledEdgeTypes.has(et)
                    ? <Eye className="w-3 h-3 text-zinc-500" />
                    : <EyeOff className="w-3 h-3 text-zinc-700" />
                  }
                  <span
                    className="w-5 h-0.5 rounded shrink-0"
                    style={{
                      backgroundColor: EDGE_COLORS[et] || '#334155',
                      opacity: enabledEdgeTypes.has(et) ? 1 : 0.3,
                    }}
                  />
                  <span>{edgeLabels[et] || et}</span>
                </button>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Legend (bottom-left) */}
      <div className="absolute bottom-4 left-4 flex flex-wrap gap-x-3 gap-y-1 px-3 py-2 bg-zinc-900/90 border border-zinc-800/50 rounded-lg backdrop-blur-sm max-w-sm">
        {domainChips.slice(0, 8).map((d) => (
          <div key={d.name} className="flex items-center gap-1.5 text-[10px] text-zinc-400">
            <span className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: d.color }} />
            {d.name}
          </div>
        ))}
        {domainChips.length > 8 && (
          <span className="text-[10px] text-zinc-600">+{domainChips.length - 8}</span>
        )}
      </div>

      {/* Usage hint (bottom-right) */}
      <div className="absolute bottom-4 right-4 text-[10px] text-zinc-600 bg-zinc-900/80 px-2 py-1 rounded border border-zinc-800/30 backdrop-blur-sm">
        滚轮缩放 · 拖拽平移 · 点击查看详情 · Hover 高亮邻居
      </div>
    </div>
  )
}
