import { useEffect, useState } from 'react'
import { Network, GitCommit, Activity, Sparkles, ExternalLink, Search, ArrowLeft, Merge } from 'lucide-react'
import { get, post } from '../api/client'
import type { LinkSuggestion, DiffRecord, CrossDomainInsight, Backlink, DistillGroup, DistillResult } from '../types'

interface Props {
  onPreviewEntry?: (entryId: string) => void
}

export default function GraphView({ onPreviewEntry }: Props) {
  const [links, setLinks] = useState<LinkSuggestion[]>([])
  const [diffs, setDiffs] = useState<DiffRecord[]>([])
  const [loadingLinks, setLoadingLinks] = useState(true)

  // Cross-domain state
  const [crossInsights, setCrossInsights] = useState<CrossDomainInsight[]>([])
  const [loadingCross, setLoadingCross] = useState(false)
  const [crossLoaded, setCrossLoaded] = useState(false)

  // Distill state
  const [distillGroups, setDistillGroups] = useState<DistillGroup[]>([])
  const [loadingDistill, setLoadingDistill] = useState(false)
  const [distillLoaded, setDistillLoaded] = useState(false)
  const [distillResults, setDistillResults] = useState<Record<number, DistillResult>>({})
  const [executingDistill, setExecutingDistill] = useState<number | null>(null)

  // Backlink state
  const [backlinkQuery, setBacklinkQuery] = useState('')
  const [backlinks, setBacklinks] = useState<Backlink[]>([])
  const [loadingBacklinks, setLoadingBacklinks] = useState(false)
  const [backlinkSearched, setBacklinkSearched] = useState(false)

  const searchBacklinks = () => {
    const q = backlinkQuery.trim()
    if (!q) return
    setLoadingBacklinks(true)
    setBacklinkSearched(false)
    get<Backlink[]>(`/graph/backlinks/${encodeURIComponent(q)}`)
      .then((data) => {
        setBacklinks(data)
        setBacklinkSearched(true)
      })
      .catch(() => {
        setBacklinks([])
        setBacklinkSearched(true)
      })
      .finally(() => setLoadingBacklinks(false))
  }

  useEffect(() => {
    get<LinkSuggestion[]>('/graph/links', { top_n: '10', threshold: '0.75' })
      .then(setLinks)
      .catch(() => {})
      .finally(() => setLoadingLinks(false))
  }, [])

  const loadDiff = (entryId: string) => {
    get<DiffRecord[]>(`/graph/diff/${entryId}`, { limit: '5' })
      .then(setDiffs)
      .catch(() => {})
  }

  const loadDistillCandidates = () => {
    setLoadingDistill(true)
    get<DistillGroup[]>('/distill/candidates', { threshold: '0.80' })
      .then((data) => {
        setDistillGroups(data)
        setDistillLoaded(true)
      })
      .catch(() => setDistillLoaded(true))
      .finally(() => setLoadingDistill(false))
  }

  const executeDistill = (group: DistillGroup, dryRun = false) => {
    setExecutingDistill(group.group_id)
    post<DistillResult>('/distill/execute', {
      entry_ids: group.entry_ids,
      dry_run: dryRun,
    })
      .then((result) => {
        setDistillResults((prev) => ({ ...prev, [group.group_id]: result }))
        if (!dryRun) {
          // Remove the group from the list since it's been merged
          setDistillGroups((prev) => prev.filter((g) => g.group_id !== group.group_id))
        }
      })
      .catch(() => {})
      .finally(() => setExecutingDistill(null))
  }

  const loadCrossDomain = () => {
    setLoadingCross(true)
    get<CrossDomainInsight[]>('/graph/cross-domain', {
      min_similarity: '0.6',
      max_insights: '20',
    })
      .then((data) => {
        setCrossInsights(data)
        setCrossLoaded(true)
      })
      .catch(() => setCrossLoaded(true))
      .finally(() => setLoadingCross(false))
  }

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-2xl font-semibold text-zinc-100 mb-1">图谱网络与演进</h1>
        <p className="text-zinc-500 text-sm">洞察知识点间的潜在关联，追踪思维演进链路。</p>
      </div>

      {/* Backlink search panel */}
      <div className="mb-6 bg-zinc-900/40 border border-zinc-800/80 rounded-xl p-5">
        <div className="flex justify-between items-center mb-4">
          <h3 className="text-sm font-medium text-zinc-300 flex items-center gap-2">
            <ArrowLeft className="w-4 h-4 text-teal-400" /> 反向链接查询
          </h3>
          <span className="text-[10px] font-mono text-zinc-600 bg-zinc-950 px-1.5 py-0.5 rounded">kg backlinks</span>
        </div>
        <div className="flex gap-2 mb-4">
          <input
            type="text"
            value={backlinkQuery}
            onChange={(e) => setBacklinkQuery(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && searchBacklinks()}
            placeholder="输入条目 ID（如 ke-20260315-xxx）"
            className="flex-1 bg-zinc-950/60 border border-zinc-800/60 rounded-lg px-3 py-2 text-sm text-zinc-200 placeholder-zinc-600 focus:outline-none focus:border-teal-700/50"
          />
          <button
            onClick={searchBacklinks}
            disabled={loadingBacklinks || !backlinkQuery.trim()}
            className="px-4 py-2 bg-teal-600/20 text-teal-400 hover:bg-teal-600/30 disabled:opacity-40 rounded-lg text-sm font-medium transition-colors flex items-center gap-1.5"
          >
            <Search className="w-3.5 h-3.5" />
            {loadingBacklinks ? '查询中...' : '查询'}
          </button>
        </div>

        {loadingBacklinks && (
          <div className="text-zinc-500 text-sm p-3 text-center">正在查询反向链接...</div>
        )}

        {backlinkSearched && !loadingBacklinks && backlinks.length === 0 && (
          <p className="text-zinc-500 text-sm p-3">该条目没有被其他条目引用。</p>
        )}

        {backlinks.length > 0 && (
          <div>
            <p className="text-xs text-zinc-500 mb-3">共 {backlinks.length} 个条目引用了该条目</p>
            <div className="space-y-2">
              {backlinks.map((bl, i) => (
                <div key={i} className="flex items-center gap-3 p-3 bg-zinc-950/50 rounded-lg border border-zinc-800/50">
                  <button
                    onClick={() => onPreviewEntry?.(bl.source_id)}
                    className="text-sm text-zinc-300 hover:text-zinc-100 truncate text-left flex items-center gap-1"
                  >
                    {bl.source_title}
                    <ExternalLink className="w-3 h-3 text-zinc-600 shrink-0" />
                  </button>
                  <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-zinc-800/50 text-zinc-500 shrink-0">
                    {bl.source_domain}
                  </span>
                  <span className={`ml-auto text-[10px] font-mono px-1.5 py-0.5 rounded shrink-0 ${
                    bl.link_type === 'graph_relation'
                      ? 'bg-blue-950/50 text-blue-400 border border-blue-800/30'
                      : 'bg-amber-950/50 text-amber-400 border border-amber-800/30'
                  }`}>
                    {bl.link_type === 'graph_relation' ? `图:${bl.rel_type}` : 'wiki link'}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Link suggestions */}
        <div className="bg-zinc-900/40 border border-zinc-800/80 rounded-xl p-5">
          <div className="flex justify-between items-center mb-4">
            <h3 className="text-sm font-medium text-zinc-300 flex items-center gap-2">
              <Network className="w-4 h-4 text-indigo-400" /> 潜在关联发现
            </h3>
            <span className="text-[10px] font-mono text-zinc-600 bg-zinc-950 px-1.5 py-0.5 rounded">kg link</span>
          </div>
          <div className="space-y-3">
            {loadingLinks ? (
              <div className="text-zinc-500 text-sm p-3">加载中...</div>
            ) : links.length === 0 ? (
              <div className="text-zinc-500 text-sm p-3">暂无关联建议。请先运行同步索引。</div>
            ) : (
              links.map((link, i) => (
                <div key={i} className="flex items-center gap-3 p-3 bg-zinc-950/50 rounded-lg border border-zinc-800/50 hover:border-zinc-700/50 transition-colors">
                  <button
                    onClick={() => onPreviewEntry?.(link.source_id)}
                    className="text-sm text-zinc-300 hover:text-indigo-400 truncate text-left transition-colors"
                  >
                    {link.source_title}
                  </button>
                  <Activity className="w-4 h-4 text-zinc-600 shrink-0" />
                  <button
                    onClick={() => onPreviewEntry?.(link.target_id)}
                    className="text-sm text-zinc-300 hover:text-indigo-400 truncate text-left transition-colors"
                  >
                    {link.target_title}
                  </button>
                  <span className="ml-auto text-xs text-emerald-400 font-mono shrink-0">
                    {link.similarity.toFixed(2)}
                  </span>
                </div>
              ))
            )}
          </div>
        </div>

        {/* Diff history */}
        <div className="bg-zinc-900/40 border border-zinc-800/80 rounded-xl p-5">
          <div className="flex justify-between items-center mb-4">
            <h3 className="text-sm font-medium text-zinc-300 flex items-center gap-2">
              <GitCommit className="w-4 h-4 text-amber-400" /> 知识节点演进 (Diff)
            </h3>
            <div className="flex gap-2">
              <span className="text-[10px] font-mono text-zinc-600 bg-zinc-950 px-1.5 py-0.5 rounded">kg diff</span>
            </div>
          </div>

          {diffs.length === 0 ? (
            <div className="text-zinc-500 text-sm p-3">
              {links.length > 0 ? (
                <p>点击下方条目查看演进历史：</p>
              ) : (
                <p>暂无演进记录。</p>
              )}
              <div className="mt-3 space-y-2">
                {links.slice(0, 3).map((link, i) => (
                  <button
                    key={i}
                    onClick={() => {
                      loadDiff(link.source_id)
                      onPreviewEntry?.(link.source_id)
                    }}
                    className="block text-xs text-indigo-400 hover:text-indigo-300"
                  >
                    查看 {link.source_title} 的演进 →
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="relative border-l border-zinc-800 ml-3 pl-5 space-y-6 mt-2">
              {diffs.map((record, i) => {
                const isCreate = record.change_type === 'created'
                return (
                  <div key={i} className="relative group">
                    <div className={`absolute -left-[25px] top-1 w-2.5 h-2.5 rounded-full ring-4 ring-zinc-950 ${
                      isCreate ? 'bg-emerald-500' :
                      record.change_type === 'modified' ? 'bg-amber-500' : 'bg-zinc-700'
                    }`}></div>
                    <p className="text-xs text-zinc-500 mb-1">{record.timestamp}</p>
                    <div className="flex items-center gap-2">
                      <span className={`text-sm font-medium ${
                        isCreate ? 'text-emerald-400' :
                        record.change_type === 'modified' ? 'text-amber-400' : 'text-rose-400'
                      }`}>{record.change_type}</span>
                      {record.stats && (
                        <span className="text-zinc-500 text-xs">
                          +{record.stats.additions} -{record.stats.deletions}
                        </span>
                      )}
                    </div>
                    {record.entry_id && (
                      <button
                        onClick={() => {
                          if (record.entry_id) {
                            onPreviewEntry?.(record.entry_id)
                          }
                        }}
                        className="mt-1 text-xs text-zinc-400 hover:text-indigo-400 transition-colors flex items-center gap-1"
                      >
                        查看此版本
                        <ExternalLink className="w-3 h-3" />
                      </button>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>

      {/* Distillation panel */}
      <div className="mt-6 bg-zinc-900/40 border border-zinc-800/80 rounded-xl p-5">
        <div className="flex justify-between items-center mb-4">
          <h3 className="text-sm font-medium text-zinc-300 flex items-center gap-2">
            <Merge className="w-4 h-4 text-rose-400" /> 知识蒸馏
          </h3>
          <div className="flex items-center gap-3">
            <span className="text-[10px] font-mono text-zinc-600 bg-zinc-950 px-1.5 py-0.5 rounded">kg distill</span>
            {!distillLoaded && (
              <button
                onClick={loadDistillCandidates}
                disabled={loadingDistill}
                className="px-3 py-1.5 bg-rose-600/20 text-rose-400 hover:bg-rose-600/30 disabled:opacity-50 rounded-lg text-xs font-medium transition-colors"
              >
                {loadingDistill ? '分析中...' : '发现候选'}
              </button>
            )}
            {distillLoaded && (
              <button
                onClick={() => { setDistillLoaded(false); setDistillGroups([]); setDistillResults({}) }}
                className="px-3 py-1.5 bg-zinc-700/30 text-zinc-400 hover:bg-zinc-700/50 rounded-lg text-xs font-medium transition-colors"
              >
                重置
              </button>
            )}
          </div>
        </div>

        {!distillLoaded && !loadingDistill && (
          <p className="text-zinc-500 text-sm p-3">
            点击 "发现候选" 自动扫描高度相似的条目组，再由 Claude 蒸馏为一个权威规范条目。
          </p>
        )}

        {loadingDistill && (
          <div className="text-zinc-500 text-sm p-3 text-center">正在扫描相似条目... 请稍候。</div>
        )}

        {distillLoaded && distillGroups.length === 0 && (
          <p className="text-zinc-500 text-sm p-3">未发现可合并的候选组。可尝试降低相似度阈值（默认 0.80）。</p>
        )}

        {distillGroups.length > 0 && (
          <div className="space-y-4">
            <p className="text-xs text-zinc-500">共发现 {distillGroups.length} 个候选组，按相似度排序</p>
            {distillGroups.map((group) => {
              const result = distillResults[group.group_id]
              const isExecuting = executingDistill === group.group_id
              return (
                <div key={group.group_id} className="p-4 bg-zinc-950/50 rounded-lg border border-zinc-800/50 space-y-3">
                  <div className="flex items-center gap-3">
                    <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-rose-950/50 text-rose-400 border border-rose-800/30">
                      相似度 {group.avg_similarity.toFixed(3)}
                    </span>
                    {group.domains.map((d) => (
                      <span key={d} className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-zinc-800/50 text-zinc-400">{d}</span>
                    ))}
                    <span className="text-xs text-zinc-600 ml-auto">{group.entry_ids.length} 个条目</span>
                  </div>

                  <div className="space-y-1">
                    {group.titles.map((title, i) => (
                      <div key={i} className="flex items-center gap-2">
                        <button
                          onClick={() => onPreviewEntry?.(group.entry_ids[i])}
                          className="text-sm text-zinc-300 hover:text-zinc-100 truncate text-left flex items-center gap-1"
                        >
                          {title}
                          <ExternalLink className="w-3 h-3 text-zinc-600 shrink-0" />
                        </button>
                        <span className="text-[10px] font-mono text-zinc-600 shrink-0">{group.entry_ids[i]}</span>
                      </div>
                    ))}
                  </div>

                  {result && (
                    <div className="p-2.5 bg-emerald-950/30 border border-emerald-800/30 rounded-lg">
                      <p className="text-xs text-emerald-400 font-medium mb-1">蒸馏完成</p>
                      <div className="flex items-center gap-2">
                        <button
                          onClick={() => onPreviewEntry?.(result.new_entry_id)}
                          className="text-sm text-emerald-300 hover:text-emerald-100 truncate text-left flex items-center gap-1"
                        >
                          {result.new_entry_title}
                          <ExternalLink className="w-3 h-3 text-emerald-600 shrink-0" />
                        </button>
                        <span className="text-[10px] text-zinc-500 ml-auto shrink-0">已删除 {result.deleted_count} 个旧条目</span>
                      </div>
                    </div>
                  )}

                  {!result && (
                    <div className="flex gap-2">
                      <button
                        onClick={() => executeDistill(group, true)}
                        disabled={isExecuting}
                        className="px-3 py-1.5 bg-zinc-700/30 text-zinc-400 hover:bg-zinc-700/50 disabled:opacity-40 rounded-lg text-xs font-medium transition-colors"
                      >
                        {isExecuting ? '处理中...' : '预览'}
                      </button>
                      <button
                        onClick={() => executeDistill(group, false)}
                        disabled={isExecuting}
                        className="px-3 py-1.5 bg-rose-600/20 text-rose-400 hover:bg-rose-600/30 disabled:opacity-40 rounded-lg text-xs font-medium transition-colors"
                      >
                        {isExecuting ? '蒸馏中...' : '执行蒸馏'}
                      </button>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* Cross-domain discovery */}
      <div className="mt-6 bg-zinc-900/40 border border-zinc-800/80 rounded-xl p-5">
        <div className="flex justify-between items-center mb-4">
          <h3 className="text-sm font-medium text-zinc-300 flex items-center gap-2">
            <Sparkles className="w-4 h-4 text-violet-400" /> 跨域知识发现
          </h3>
          <div className="flex items-center gap-3">
            <span className="text-[10px] font-mono text-zinc-600 bg-zinc-950 px-1.5 py-0.5 rounded">kg cross-domain</span>
            {!crossLoaded && (
              <button
                onClick={loadCrossDomain}
                disabled={loadingCross}
                className="px-3 py-1.5 bg-violet-600/20 text-violet-400 hover:bg-violet-600/30 disabled:opacity-50 rounded-lg text-xs font-medium transition-colors"
              >
                {loadingCross ? '分析中...' : '生成洞察'}
              </button>
            )}
          </div>
        </div>

        {!crossLoaded && !loadingCross && (
          <p className="text-zinc-500 text-sm p-3">
            点击 "生成洞察" 发现不同知识域之间的共通模式和可迁移思想。
          </p>
        )}

        {loadingCross && (
          <div className="text-zinc-500 text-sm p-3 text-center">
            正在分析跨域连接... 这可能需要一些时间。
          </div>
        )}

        {crossLoaded && crossInsights.length === 0 && (
          <p className="text-zinc-500 text-sm p-3">未发现跨域知识连接。可尝试降低相似度阈值或增加知识条目。</p>
        )}

        {crossInsights.length > 0 && (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            {crossInsights.map((insight, i) => (
              <div key={i} className="p-4 bg-zinc-950/50 rounded-lg border border-zinc-800/50 space-y-2">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-cyan-950/50 text-cyan-400 border border-cyan-800/30">
                    {insight.domain_a}
                  </span>
                  <span className="text-zinc-600 text-xs">↔</span>
                  <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-violet-950/50 text-violet-400 border border-violet-800/30">
                    {insight.domain_b}
                  </span>
                  <span className="ml-auto text-xs text-emerald-400 font-mono">
                    {insight.similarity.toFixed(2)}
                  </span>
                </div>
                <div className="flex items-start gap-2">
                  <div className="flex-1 min-w-0">
                    <button
                      onClick={() => onPreviewEntry?.(insight.entry_a_id)}
                      className="text-sm text-zinc-300 hover:text-zinc-100 truncate block text-left"
                    >
                      {insight.entry_a_title}
                      <ExternalLink className="w-3 h-3 inline ml-1 text-zinc-600" />
                    </button>
                  </div>
                  <span className="text-zinc-700 shrink-0 mt-0.5">↔</span>
                  <div className="flex-1 min-w-0">
                    <button
                      onClick={() => onPreviewEntry?.(insight.entry_b_id)}
                      className="text-sm text-zinc-300 hover:text-zinc-100 truncate block text-left"
                    >
                      {insight.entry_b_title}
                      <ExternalLink className="w-3 h-3 inline ml-1 text-zinc-600" />
                    </button>
                  </div>
                </div>
                {insight.description && (
                  <p className="text-xs text-zinc-500 leading-relaxed">{insight.description}</p>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
