import { useState, useRef, useCallback } from 'react'
import { Rss, Play, Pause, CheckCircle, XCircle, Loader2, Clock, FileText, Zap } from 'lucide-react'

interface FeedInfo {
  name: string
  url: string
  domain: string | null
  tags: string[]
  quality_weight: number
  last_published: string | null
  last_checked: string | null
}

interface FeedResult {
  name: string
  count: number
  status: 'ok' | 'empty' | 'error'
  error?: string
}

interface ArticleResult {
  index: number
  total: number
  title: string
  created?: number
  merged?: number
  skipped?: number
  error?: string
}

interface PullSummary {
  total_articles: number
  created: number
  merged: number
  skipped: number
  failed: number
  dry_run?: boolean
}

type Phase = 'idle' | 'fetch' | 'extract' | 'complete'

export default function RSSView() {
  const [feeds, setFeeds] = useState<FeedInfo[]>([])
  const [feedsLoaded, setFeedsLoaded] = useState(false)
  const [phase, setPhase] = useState<Phase>('idle')
  const [feedResults, setFeedResults] = useState<FeedResult[]>([])
  const [articleResults, setArticleResults] = useState<ArticleResult[]>([])
  const [summary, setSummary] = useState<PullSummary | null>(null)
  const [totalArticles, setTotalArticles] = useState(0)
  const [sinceDays, setSinceDays] = useState(7)
  const [workers, setWorkers] = useState(8)
  const [dryRun, setDryRun] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  const loadFeeds = useCallback(async () => {
    try {
      const res = await fetch('/api/rss/feeds')
      const data = await res.json()
      setFeeds(data.feeds || [])
      setFeedsLoaded(true)
    } catch {
      setError('无法加载 feeds 配置')
    }
  }, [])

  if (!feedsLoaded) loadFeeds()

  const startPull = async () => {
    setPhase('fetch')
    setFeedResults([])
    setArticleResults([])
    setSummary(null)
    setError(null)
    setTotalArticles(0)

    const controller = new AbortController()
    abortRef.current = controller

    try {
      const res = await fetch('/api/rss/pull', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          since_days: sinceDays,
          workers,
          dry_run: dryRun,
          quality_check: true,
        }),
        signal: controller.signal,
      })

      if (!res.ok || !res.body) {
        setError(`请求失败: ${res.status}`)
        setPhase('idle')
        return
      }

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        let currentEvent = ''
        for (const line of lines) {
          if (line.startsWith('event: ')) {
            currentEvent = line.slice(7).trim()
          } else if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.slice(6))
              handleEvent(currentEvent, data)
            } catch { /* ignore parse errors */ }
          }
        }
      }
    } catch (err: unknown) {
      if (err instanceof Error && err.name !== 'AbortError') {
        setError(`连接错误: ${err.message}`)
      }
    }
  }

  const handleEvent = (event: string, data: Record<string, unknown>) => {
    switch (event) {
      case 'phase':
        if (data.phase === 'extract') {
          setPhase('extract')
          setTotalArticles(data.total_articles as number)
        }
        break
      case 'feed_done':
        setFeedResults(prev => [...prev, data as unknown as FeedResult])
        break
      case 'article_done':
        setArticleResults(prev => [...prev, data as unknown as ArticleResult])
        break
      case 'article_failed':
        setArticleResults(prev => [...prev, { ...data, error: data.error as string } as unknown as ArticleResult])
        break
      case 'complete':
        setSummary(data as unknown as PullSummary)
        setPhase('complete')
        break
      case 'error':
        setError(data.message as string)
        setPhase('idle')
        break
    }
  }

  const stopPull = () => {
    abortRef.current?.abort()
    setPhase('idle')
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
                onClick={startPull}
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
            {articleResults.length === 0 && phase !== 'extract' && (
              <p className="text-xs text-zinc-600 py-4 text-center">
                {phase === 'fetch' ? '正在拉取 feeds...' : '点击「开始拉取」启动'}
              </p>
            )}
            {phase === 'extract' && articleResults.length === 0 && (
              <div className="flex items-center justify-center py-4 gap-2 text-xs text-zinc-500">
                <Loader2 className="w-3 h-3 animate-spin" />
                正在提取知识条目...
              </div>
            )}
            {articleResults.map((ar, i) => (
              <div key={i} className="flex items-center justify-between py-1.5 px-2 rounded text-xs group">
                <div className="flex items-center gap-2 min-w-0">
                  {ar.error ? (
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
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
