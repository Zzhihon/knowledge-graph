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
          <h1 className="text-xl font-semibold text-k-ink flex items-center gap-2">
            <Rss className="w-5 h-5 text-k-amber-ink" />
            RSS 自动摄取
          </h1>
          <p className="text-sm text-k-ink4 mt-1">
            从技术博客自动拉取文章并提取知识条目
          </p>
        </div>
      </div>

      {/* Controls */}
      <div className="bg-k-surface/50 border border-k-line/50 rounded-xl p-5">
        <div className="flex items-end gap-4 flex-wrap">
          <div>
            <label className="block text-xs text-k-ink4 mb-1.5">时间范围</label>
            <select
              value={sinceDays}
              onChange={e => setSinceDays(Number(e.target.value))}
              disabled={phase !== 'idle' && phase !== 'complete'}
              className="bg-k-sunk border border-k-line-strong rounded-lg px-3 py-1.5 text-sm text-k-ink focus:outline-none focus:ring-1 focus:ring-k-accent"
            >
              <option value={1}>最近 1 天</option>
              <option value={3}>最近 3 天</option>
              <option value={7}>最近 7 天</option>
              <option value={14}>最近 14 天</option>
              <option value={30}>最近 30 天</option>
            </select>
          </div>
          <div>
            <label className="block text-xs text-k-ink4 mb-1.5">并发数</label>
            <select
              value={workers}
              onChange={e => setWorkers(Number(e.target.value))}
              disabled={phase !== 'idle' && phase !== 'complete'}
              className="bg-k-sunk border border-k-line-strong rounded-lg px-3 py-1.5 text-sm text-k-ink focus:outline-none focus:ring-1 focus:ring-k-accent"
            >
              <option value={2}>2 线程</option>
              <option value={4}>4 线程</option>
              <option value={8}>8 线程</option>
            </select>
          </div>
          <label className="flex items-center gap-2 text-sm text-k-ink3 cursor-pointer pb-1">
            <input
              type="checkbox"
              checked={dryRun}
              onChange={e => setDryRun(e.target.checked)}
              disabled={phase !== 'idle' && phase !== 'complete'}
              className="rounded border-k-line-strong bg-k-sunk text-k-accent focus:ring-k-accent"
            />
            预览模式
          </label>
          <div className="ml-auto">
            {(phase === 'idle' || phase === 'complete') ? (
              <button
                onClick={() => startPull(sinceDays, workers, dryRun)}
                className="flex items-center gap-2 px-4 py-2 bg-k-amber/90 hover:bg-k-amber text-white rounded-lg text-sm font-medium transition-colors"
              >
                <Play className="w-4 h-4" />
                {dryRun ? '预览拉取' : '开始拉取'}
              </button>
            ) : (
              <button
                onClick={stopPull}
                className="flex items-center gap-2 px-4 py-2 bg-k-hot/80 hover:bg-k-hot text-white rounded-lg text-sm font-medium transition-colors"
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
        <div className="bg-k-hot/10 border border-k-hot/30 rounded-xl p-4 text-sm text-k-hot">
          {error}
        </div>
      )}

      {/* Progress Bar */}
      {(phase === 'extract' || phase === 'complete') && totalArticles > 0 && (
        <div className="bg-k-surface/50 border border-k-line/50 rounded-xl p-5">
          <div className="flex items-center justify-between mb-3">
            <span className="text-sm text-k-ink2 flex items-center gap-2">
              {phase === 'extract' && <Loader2 className="w-4 h-4 animate-spin text-k-amber-ink" />}
              {phase === 'complete' && <CheckCircle className="w-4 h-4 text-k-ok" />}
              知识提取进度
            </span>
            <span className="text-sm text-k-ink3">
              {processedCount} / {totalArticles} 篇文章 ({progressPct}%)
            </span>
          </div>
          <div className="w-full bg-k-sunk rounded-full h-2">
            <div
              className={`h-2 rounded-full transition-all duration-300 ${
                phase === 'complete' ? 'bg-k-ok' : 'bg-k-amber'
              }`}
              style={{ width: `${progressPct}%` }}
            />
          </div>
        </div>
      )}

      {/* Fetch phase indicator (when no articles yet) */}
      {phase === 'fetch' && (
        <div className="bg-k-surface/50 border border-k-line/50 rounded-xl p-5">
          <div className="flex items-center gap-3 text-sm text-k-ink3">
            <Loader2 className="w-4 h-4 animate-spin text-k-amber-ink" />
            正在拉取订阅源...
          </div>
        </div>
      )}

      {/* Summary */}
      {summary && (
        <div className="grid grid-cols-4 gap-4">
          {[
            { label: '创建', value: summary.created, color: 'text-k-ok', bg: 'bg-k-ok/10' },
            { label: '合并', value: summary.merged, color: 'text-k-amber-ink', bg: 'bg-k-amber/10' },
            { label: '跳过', value: summary.skipped, color: 'text-k-ink3', bg: 'bg-k-ink4/10' },
            { label: '失败', value: summary.failed, color: 'text-k-hot', bg: 'bg-k-hot/10' },
          ].map(s => (
            <div key={s.label} className={`${s.bg} border border-k-line/50 rounded-xl p-4 text-center`}>
              <div className={`text-2xl font-bold ${s.color}`}>{s.value}</div>
              <div className="text-xs text-k-ink4 mt-1">{s.label}</div>
            </div>
          ))}
        </div>
      )}

      {/* Two columns: Feeds + Articles */}
      <div className="grid grid-cols-2 gap-4">
        {/* Feed Results */}
        <div className="bg-k-surface/50 border border-k-line/50 rounded-xl p-5">
          <h3 className="text-sm font-medium text-k-ink2 mb-3 flex items-center gap-2">
            <Rss className="w-4 h-4 text-k-amber-ink" />
            订阅源 ({feeds.length})
          </h3>
          <div className="space-y-1.5 max-h-[400px] overflow-y-auto">
            {phase === 'idle' && !feedResults.length ? (
              feeds.map(f => (
                <div key={f.url} className="flex items-center justify-between py-1.5 px-2 rounded text-xs">
                  <div className="flex items-center gap-2 min-w-0">
                    <Clock className="w-3 h-3 text-k-ink4 shrink-0" />
                    <span className="text-k-ink3 truncate">{f.name}</span>
                  </div>
                  {f.domain && (
                    <span className="text-xs text-k-ink4 bg-k-sunk px-1.5 py-0.5 rounded shrink-0 ml-2">
                      {f.domain}
                    </span>
                  )}
                </div>
              ))
            ) : (
              feedResults.map((fr, i) => (
                <div key={i} className="flex items-center justify-between py-1.5 px-2 rounded text-xs">
                  <div className="flex items-center gap-2 min-w-0">
                    {fr.status === 'ok' && <CheckCircle className="w-3 h-3 text-k-ok shrink-0" />}
                    {fr.status === 'empty' && <Clock className="w-3 h-3 text-k-ink4 shrink-0" />}
                    {fr.status === 'error' && <XCircle className="w-3 h-3 text-k-hot shrink-0" />}
                    <span className={fr.status === 'ok' ? 'text-k-ink' : 'text-k-ink4'}>{fr.name}</span>
                  </div>
                  <span className={`shrink-0 ml-2 ${fr.count > 0 ? 'text-k-ok' : 'text-k-ink4'}`}>
                    {fr.count > 0 ? `${fr.count} 篇` : '无新文章'}
                  </span>
                </div>
              ))
            )}
          </div>
        </div>

        {/* Article Results */}
        <div className="bg-k-surface/50 border border-k-line/50 rounded-xl p-5">
          <h3 className="text-sm font-medium text-k-ink2 mb-3 flex items-center gap-2">
            <FileText className="w-4 h-4 text-k-accent" />
            文章处理 {totalArticles > 0 && `(${processedCount}/${totalArticles})`}
          </h3>
          <div className="space-y-1.5 max-h-[400px] overflow-y-auto">
            {articleResults.length === 0 && phase !== 'extract' && phase !== 'fetch' && (
              <p className="text-xs text-k-ink4 py-4 text-center">
                点击「开始拉取」启动
              </p>
            )}
            {phase === 'fetch' && articleResults.length === 0 && (
              <div className="flex items-center justify-center py-4 gap-2 text-xs text-k-ink4">
                <Loader2 className="w-3 h-3 animate-spin" />
                正在拉取 feeds...
              </div>
            )}
            {phase === 'extract' && articleResults.length === 0 && (
              <div className="flex items-center justify-center py-4 gap-2 text-xs text-k-ink4">
                <Loader2 className="w-3 h-3 animate-spin" />
                正在提取知识条目...
              </div>
            )}
            {articleResults.map((ar, i) => {
              const hasEntries = ar.entries && ar.entries.length > 0
              const isExpanded = expandedArticles.has(ar.index)
              return (
                <div key={i} className="rounded border border-k-line/30">
                  <div
                    className={`flex items-center justify-between py-1.5 px-2 text-xs ${hasEntries ? 'cursor-pointer hover:bg-k-sunk/30' : ''}`}
                    onClick={() => hasEntries && toggleArticle(ar.index)}
                  >
                    <div className="flex items-center gap-2 min-w-0">
                      {hasEntries ? (
                        isExpanded
                          ? <ChevronDown className="w-3 h-3 text-k-ink4 shrink-0" />
                          : <ChevronRight className="w-3 h-3 text-k-ink4 shrink-0" />
                      ) : ar.error ? (
                        <XCircle className="w-3 h-3 text-k-hot shrink-0" />
                      ) : (
                        <Zap className="w-3 h-3 text-k-accent shrink-0" />
                      )}
                      <span className={`truncate ${ar.error ? 'text-k-hot' : 'text-k-ink2'}`}>
                        {ar.title}
                      </span>
                    </div>
                    <div className="flex items-center gap-2 shrink-0 ml-2">
                      {ar.error ? (
                        <span className="text-k-hot">失败</span>
                      ) : (
                        <>
                          {(ar.created ?? 0) > 0 && <span className="text-k-ok">+{ar.created}</span>}
                          {(ar.merged ?? 0) > 0 && <span className="text-k-amber-ink">~{ar.merged}</span>}
                          {(ar.skipped ?? 0) > 0 && <span className="text-k-ink4">-{ar.skipped}</span>}
                        </>
                      )}
                    </div>
                  </div>
                  {isExpanded && ar.entries && (
                    <div className="border-t border-k-line/30 bg-k-bg/40 px-3 py-2 space-y-1.5">
                      {ar.entries.map((entry, j) => (
                        <div key={j} className="flex items-start gap-2 text-xs">
                          {entry.action === 'create' && <Plus className="w-3 h-3 text-k-ok shrink-0 mt-0.5" />}
                          {entry.action === 'merge' && <GitMerge className="w-3 h-3 text-k-amber-ink shrink-0 mt-0.5" />}
                          {entry.action === 'skip' && <SkipForward className="w-3 h-3 text-k-ink4 shrink-0 mt-0.5" />}
                          <div className="min-w-0">
                            <span className={`${
                              entry.action === 'create' ? 'text-k-ink' :
                              entry.action === 'merge' ? 'text-k-amber-ink' : 'text-k-ink4'
                            }`}>
                              {entry.title}
                            </span>
                            <div className="flex items-center gap-2 mt-0.5">
                              {entry.domain && (
                                <span className="text-[10px] text-k-ink4 bg-k-sunk/60 px-1 py-0.5 rounded">{entry.domain}</span>
                              )}
                              {entry.type && (
                                <span className="text-[10px] text-k-ink4 bg-k-sunk/60 px-1 py-0.5 rounded">{entry.type}</span>
                              )}
                              {entry.merge_target && (
                                <span className="text-[10px] text-k-amber-ink">→ {entry.merge_target}</span>
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
