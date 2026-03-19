import { useState, useRef, useCallback } from 'react'

export interface FeedInfo {
  name: string
  url: string
  domain: string | null
  tags: string[]
  quality_weight: number
  last_published: string | null
  last_checked: string | null
}

export interface FeedResult {
  name: string
  count: number
  status: 'ok' | 'empty' | 'error'
  error?: string
}

export interface EntryPreviewInfo {
  id: string
  title: string
  action: 'create' | 'merge' | 'skip'
  type: string
  domain: string
  merge_target?: string
}

export interface ArticleResult {
  index: number
  total: number
  title: string
  created?: number
  merged?: number
  skipped?: number
  error?: string
  entries?: EntryPreviewInfo[]
}

export interface PullSummary {
  total_articles: number
  created: number
  merged: number
  skipped: number
  failed: number
  dry_run?: boolean
}

export type Phase = 'idle' | 'fetch' | 'extract' | 'complete'

export interface RSSPullState {
  feeds: FeedInfo[]
  feedsLoaded: boolean
  phase: Phase
  feedResults: FeedResult[]
  articleResults: ArticleResult[]
  summary: PullSummary | null
  totalArticles: number
  error: string | null
}

export interface RSSPullActions {
  loadFeeds: () => void
  startPull: (sinceDays: number, workers: number, dryRun: boolean) => void
  stopPull: () => void
}

export function useRSSPull(): [RSSPullState, RSSPullActions] {
  const [feeds, setFeeds] = useState<FeedInfo[]>([])
  const [feedsLoaded, setFeedsLoaded] = useState(false)
  const [phase, setPhase] = useState<Phase>('idle')
  const [feedResults, setFeedResults] = useState<FeedResult[]>([])
  const [articleResults, setArticleResults] = useState<ArticleResult[]>([])
  const [summary, setSummary] = useState<PullSummary | null>(null)
  const [totalArticles, setTotalArticles] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  const loadFeeds = useCallback(async () => {
    if (feedsLoaded) return
    try {
      const res = await fetch('/api/rss/feeds')
      const data = await res.json()
      setFeeds(data.feeds || [])
      setFeedsLoaded(true)
    } catch {
      setError('无法加载 feeds 配置')
    }
  }, [feedsLoaded])

  const handleEvent = useCallback((event: string, data: Record<string, unknown>) => {
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
  }, [])

  const startPull = useCallback(async (sinceDays: number, workers: number, dryRun: boolean) => {
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
  }, [handleEvent])

  const stopPull = useCallback(() => {
    abortRef.current?.abort()
    setPhase('idle')
  }, [])

  const state: RSSPullState = {
    feeds, feedsLoaded, phase, feedResults,
    articleResults, summary, totalArticles, error,
  }

  const actions: RSSPullActions = { loadFeeds, startPull, stopPull }

  return [state, actions]
}
