import { useState } from 'react'
import { Rss, Play, Pause, CheckCircle, XCircle, Loader2, Clock, FileText, Zap, ChevronDown, ChevronRight, Plus, GitMerge, SkipForward } from 'lucide-react'
import type { RSSPullState, RSSPullActions } from '../hooks/useRSSPull'

interface Props {
  pullState: RSSPullState
  pullActions: RSSPullActions
}

export default function RSSView({ pullState, pullActions }: Props) {
  const { feeds, feedsLoaded, phase, feedResults, articleResults, summary, totalArticles, error } = pullState
  const { loadFeeds, startPull, stopPull } = pullActions

  const [sinceDays, setSinceDays] = useState(7)
  const [workers, setWorkers] = useState(8)
  const [dryRun, setDryRun] = useState(false)
  const [expandedArticles, setExpandedArticles] = useState<Set<number>>(new Set())

  if (!feedsLoaded) loadFeeds()

  const toggleArticle = (index: number) => {
    setExpandedArticles(prev => {
      const next = new Set(prev)
      if (next.has(index)) next.delete(index)
      else next.add(index)
      return next
    })
  }

  const processedCount = articleResults.length
  const progressPct = totalArticles > 0 ? Math.round((processedCount / totalArticles) * 100) : 0

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-zinc-100 flex items-center gap-2">
            <Rss className="w-5 h-5 text-orange-400" />
            RSS 自动摄取
          </h1>
          <p className="text-sm text-zinc-500 mt-1">
            从技术博客自动拉取文章并提取知识条目
          </p>
        </div>
      </div>

      {/* Controls */}
      <div className="bg-zinc-900/50 border border-zinc-800/50 rounded-xl p-5">
        <div className="flex items-end gap-4 flex-wrap">
          <div>
            <label className="block text-xs text-zinc-500 mb-1.5">时间范围</label>
            <select
              value={sinceDays}
              onChange={e => setSinceDays(Number(e.target.value))}
              disabled={phase !== 'idle' && phase !== 'complete'}
              className="bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-1.5 text-sm text-zinc-200 focus:outline-none focus:ring-1 focus:ring-indigo-500"
            >
              <option value={1}>最近 1 天</option>
              <option value={3}>最近 3 天</option>
              <option value={7}>最近 7 天</option>
              <option value={14}>最近 14 天</option>
              <option value={30}>最近 30 天</option>
            </select>
          </div>
          <div>
            <label className="block text-xs text-zinc-500 mb-1.5">并发数</label>
            <select
              value={workers}
              onChange={e => setWorkers(Number(e.target.value))}
              disabled={phase !== 'idle' && phase !== 'complete'}
              className="bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-1.5 text-sm text-zinc-200 focus:outline-none focus:ring-1 focus:ring-indigo-500"
            >
              <option value={2}>2 线程</option>
              <option value={4}>4 线程</option>
              <option value={8}>8 线程</option>
            </select>
          </div>
          <label className="flex items-center gap-2 text-sm text-zinc-400 cursor-pointer pb-1">
            <input
              type="checkbox"
              checked={dryRun}
              onChange={e => setDryRun(e.target.checked)}
              disabled={phase !== 'idle' && phase !== 'complete'}
              className="rounded border-zinc-600 bg-zinc-800 text-indigo-500 focus:ring-indigo-500"
            />
            预览模式
          </label>
          <div className="ml-auto">
            {(phase === 'idle' || phase === 'complete') ? (
              <button
                onClick={() => startPull(sinceDays, workers, dryRun)}
                className="flex items-center gap-2 px-4 py-2 bg-orange-500/90 hover:bg-orange-500 text-white rounded-lg text-sm font-medium transition-colors"
              >
                <Play className="w-4 h-4" />
                {dryRun ? '预览拉取' : '开始拉取'}
              </button>
            ) : (
              <button
                onClick={stopPull}
                className="flex items-center gap-2 px-4 py-2 bg-red-500/80 hover:bg-red-500 text-white rounded-lg text-sm font-medium transition-colors"
              >
                <Pause className="w-4 h-4" />
                停止
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="bg-red-500/10 border border-red-500/30 rounded-xl p-4 text-sm text-red-400">
          {error}
        </div>
      )}

      {/* Progress Bar */}
      {(phase === 'extract' || phase === 'complete') && totalArticles > 0 && (
        <div className="bg-zinc-900/50 border border-zinc-800/50 rounded-xl p-5">
          <div className="flex items-center justify-between mb-3">
            <span className="text-sm text-zinc-300 flex items-center gap-2">
              {phase === 'extract' && <Loader2 className="w-4 h-4 animate-spin text-orange-400" />}
              {phase === 'complete' && <CheckCircle className="w-4 h-4 text-emerald-400" />}
              知识提取进度
            </span>
            <span className="text-sm text-zinc-400">
              {processedCount} / {totalArticles} 篇文章 ({progressPct}%)
            </span>
          </div>
          <div className="w-full bg-zinc-800 rounded-full h-2">
            <div
              className={`h-2 rounded-full transition-all duration-300 ${
                phase === 'complete' ? 'bg-emerald-500' : 'bg-orange-500'
              }`}
              style={{ width: `${progressPct}%` }}
            />
          </div>
        </div>
      )}

      {/* Fetch phase indicator (when no articles yet) */}
      {phase === 'fetch' && (
        <div className="bg-zinc-900/50 border border-zinc-800/50 rounded-xl p-5">
          <div className="flex items-center gap-3 text-sm text-zinc-400">
            <Loader2 className="w-4 h-4 animate-spin text-orange-400" />
            正在拉取订阅源...
          </div>
        </div>
      )}

      {/* Summary */}
      {summary && (
        <div className="grid grid-cols-4 gap-4">
          {[
            { label: '创建', value: summary.created, color: 'text-emerald-400', bg: 'bg-emerald-500/10' },
            { label: '合并', value: summary.merged, color: 'text-yellow-400', bg: 'bg-yellow-500/10' },
            { label: '跳过', value: summary.skipped, color: 'text-zinc-400', bg: 'bg-zinc-500/10' },
            { label: '失败', value: summary.failed, color: 'text-red-400', bg: 'bg-red-500/10' },
          ].map(s => (
            <div key={s.label} className={`${s.bg} border border-zinc-800/50 rounded-xl p-4 text-center`}>
              <div className={`text-2xl font-bold ${s.color}`}>{s.value}</div>
              <div className="text-xs text-zinc-500 mt-1">{s.label}</div>
            </div>
          ))}
        </div>
      )}

      {/* Two columns: Feeds + Articles */}
      <div className="grid grid-cols-2 gap-4">
        {/* Feed Results */}
        <div className="bg-zinc-900/50 border border-zinc-800/50 rounded-xl p-5">
          <h3 className="text-sm font-medium text-zinc-300 mb-3 flex items-center gap-2">
            <Rss className="w-4 h-4 text-orange-400" />
            订阅源 ({feeds.length})
          </h3>
          <div className="space-y-1.5 max-h-[400px] overflow-y-auto">
            {phase === 'idle' && !feedResults.length ? (
              feeds.map(f => (
                <div key={f.url} className="flex items-center justify-between py-1.5 px-2 rounded text-xs">
                  <div className="flex items-center gap-2 min-w-0">
                    <Clock className="w-3 h-3 text-zinc-600 shrink-0" />
                    <span className="text-zinc-400 truncate">{f.name}</span>
                  </div>
                  {f.domain && (
                    <span className="text-xs text-zinc-600 bg-zinc-800 px-1.5 py-0.5 rounded shrink-0 ml-2">
                      {f.domain}
                    </span>
                  )}
                </div>
              ))
            ) : (
              feedResults.map((fr, i) => (
                <div key={i} className="flex items-center justify-between py-1.5 px-2 rounded text-xs">
                  <div className="flex items-center gap-2 min-w-0">
                    {fr.status === 'ok' && <CheckCircle className="w-3 h-3 text-emerald-400 shrink-0" />}
                    {fr.status === 'empty' && <Clock className="w-3 h-3 text-zinc-500 shrink-0" />}
                    {fr.status === 'error' && <XCircle className="w-3 h-3 text-red-400 shrink-0" />}
                    <span className={fr.status === 'ok' ? 'text-zinc-200' : 'text-zinc-500'}>{fr.name}</span>
                  </div>
                  <span className={`shrink-0 ml-2 ${fr.count > 0 ? 'text-emerald-400' : 'text-zinc-600'}`}>
                    {fr.count > 0 ? `${fr.count} 篇` : '无新文章'}
                  </span>
                </div>
              ))
            )}
          </div>
        </div>

        {/* Article Results */}
        <div className="bg-zinc-900/50 border border-zinc-800/50 rounded-xl p-5">
          <h3 className="text-sm font-medium text-zinc-300 mb-3 flex items-center gap-2">
            <FileText className="w-4 h-4 text-indigo-400" />
            文章处理 {totalArticles > 0 && `(${processedCount}/${totalArticles})`}
          </h3>
          <div className="space-y-1.5 max-h-[400px] overflow-y-auto">
            {articleResults.length === 0 && phase !== 'extract' && phase !== 'fetch' && (
              <p className="text-xs text-zinc-600 py-4 text-center">
                点击「开始拉取」启动
              </p>
            )}
            {phase === 'fetch' && articleResults.length === 0 && (
              <div className="flex items-center justify-center py-4 gap-2 text-xs text-zinc-500">
                <Loader2 className="w-3 h-3 animate-spin" />
                正在拉取 feeds...
              </div>
            )}
            {phase === 'extract' && articleResults.length === 0 && (
              <div className="flex items-center justify-center py-4 gap-2 text-xs text-zinc-500">
                <Loader2 className="w-3 h-3 animate-spin" />
                正在提取知识条目...
              </div>
            )}
            {articleResults.map((ar, i) => {
              const hasEntries = ar.entries && ar.entries.length > 0
              const isExpanded = expandedArticles.has(ar.index)
              return (
                <div key={i} className="rounded border border-zinc-800/30">
                  <div
                    className={`flex items-center justify-between py-1.5 px-2 text-xs ${hasEntries ? 'cursor-pointer hover:bg-zinc-800/30' : ''}`}
                    onClick={() => hasEntries && toggleArticle(ar.index)}
                  >
                    <div className="flex items-center gap-2 min-w-0">
                      {hasEntries ? (
                        isExpanded
                          ? <ChevronDown className="w-3 h-3 text-zinc-500 shrink-0" />
                          : <ChevronRight className="w-3 h-3 text-zinc-500 shrink-0" />
                      ) : ar.error ? (
                        <XCircle className="w-3 h-3 text-red-400 shrink-0" />
                      ) : (
                        <Zap className="w-3 h-3 text-indigo-400 shrink-0" />
                      )}
                      <span className={`truncate ${ar.error ? 'text-red-400' : 'text-zinc-300'}`}>
                        {ar.title}
                      </span>
                    </div>
                    <div className="flex items-center gap-2 shrink-0 ml-2">
                      {ar.error ? (
                        <span className="text-red-400">失败</span>
                      ) : (
                        <>
                          {(ar.created ?? 0) > 0 && <span className="text-emerald-400">+{ar.created}</span>}
                          {(ar.merged ?? 0) > 0 && <span className="text-yellow-400">~{ar.merged}</span>}
                          {(ar.skipped ?? 0) > 0 && <span className="text-zinc-500">-{ar.skipped}</span>}
                        </>
                      )}
                    </div>
                  </div>
                  {isExpanded && ar.entries && (
                    <div className="border-t border-zinc-800/30 bg-zinc-950/40 px-3 py-2 space-y-1.5">
                      {ar.entries.map((entry, j) => (
                        <div key={j} className="flex items-start gap-2 text-xs">
                          {entry.action === 'create' && <Plus className="w-3 h-3 text-emerald-400 shrink-0 mt-0.5" />}
                          {entry.action === 'merge' && <GitMerge className="w-3 h-3 text-yellow-400 shrink-0 mt-0.5" />}
                          {entry.action === 'skip' && <SkipForward className="w-3 h-3 text-zinc-500 shrink-0 mt-0.5" />}
                          <div className="min-w-0">
                            <span className={`${
                              entry.action === 'create' ? 'text-zinc-200' :
                              entry.action === 'merge' ? 'text-yellow-300' : 'text-zinc-500'
                            }`}>
                              {entry.title}
                            </span>
                            <div className="flex items-center gap-2 mt-0.5">
                              {entry.domain && (
                                <span className="text-[10px] text-zinc-600 bg-zinc-800/60 px-1 py-0.5 rounded">{entry.domain}</span>
                              )}
                              {entry.type && (
                                <span className="text-[10px] text-zinc-600 bg-zinc-800/60 px-1 py-0.5 rounded">{entry.type}</span>
                              )}
                              {entry.merge_target && (
                                <span className="text-[10px] text-yellow-600">→ {entry.merge_target}</span>
                              )}
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      </div>
    </div>
  )
}
