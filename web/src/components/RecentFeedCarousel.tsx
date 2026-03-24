import { useEffect, useRef, useState, useCallback } from 'react'
import { Rss, ChevronRight } from 'lucide-react'
import { get } from '../api/client'
import type { RecentFeedItem } from '../types'

interface Props {
  onPreviewEntry: (id: string) => void
  setActiveTab: (tab: string) => void
}

const DOMAIN_COLORS: Record<string, string> = {
  'cloud-native': 'bg-sky-500/20 text-sky-400',
  'golang': 'bg-cyan-500/20 text-cyan-400',
  'kubernetes': 'bg-blue-500/20 text-blue-400',
  'architecture': 'bg-violet-500/20 text-violet-400',
  'security': 'bg-red-500/20 text-red-400',
  'ai-agent': 'bg-fuchsia-500/20 text-fuchsia-400',
  'devops': 'bg-orange-500/20 text-orange-400',
  'distributed-systems': 'bg-amber-500/20 text-amber-400',
  'frontend': 'bg-emerald-500/20 text-emerald-400',
  'database': 'bg-yellow-500/20 text-yellow-400',
}

const DEFAULT_COLOR = 'bg-zinc-500/20 text-zinc-400'

function formatRelativeDate(dateStr: string): string {
  if (!dateStr) return ''
  const date = new Date(dateStr)
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24))

  if (diffDays === 0) return '今天'
  if (diffDays === 1) return '昨天'
  if (diffDays < 14) return `${diffDays}天前`
  const m = date.getMonth() + 1
  const d = date.getDate()
  return `${m}月${d}日`
}

function getDomainLabel(domain: string | string[]): string {
  if (Array.isArray(domain)) return domain[0] || 'unknown'
  return domain
}

function getDomainColor(domain: string | string[]): string {
  const key = Array.isArray(domain) ? domain[0] : domain
  return DOMAIN_COLORS[key] || DEFAULT_COLOR
}

export default function RecentFeedCarousel({ onPreviewEntry, setActiveTab }: Props) {
  const [items, setItems] = useState<RecentFeedItem[]>([])
  const [loading, setLoading] = useState(true)
  const scrollRef = useRef<HTMLDivElement>(null)
  const isPaused = useRef(false)

  useEffect(() => {
    get<{ items: RecentFeedItem[]; total: number }>('/recent-feed')
      .then((data) => setItems(data.items))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  // Auto-scroll every 8 seconds
  const scrollNext = useCallback(() => {
    const el = scrollRef.current
    if (!el || isPaused.current) return
    const cardWidth = 296 // 280 + 16 gap
    const maxScroll = el.scrollWidth - el.clientWidth
    if (el.scrollLeft >= maxScroll - 10) {
      el.scrollTo({ left: 0, behavior: 'smooth' })
    } else {
      el.scrollBy({ left: cardWidth, behavior: 'smooth' })
    }
  }, [])

  useEffect(() => {
    if (items.length <= 3) return
    const timer = setInterval(scrollNext, 8000)
    return () => clearInterval(timer)
  }, [items.length, scrollNext])

  if (loading) {
    return (
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Rss className="w-4 h-4 text-orange-400" />
            <h2 className="text-sm font-medium text-zinc-400">最近知识摄入</h2>
          </div>
        </div>
        <div className="flex gap-4">
          {[1, 2, 3].map((i) => (
            <div key={i} className="w-[280px] h-[140px] shrink-0 rounded-xl bg-zinc-800/30 animate-pulse" />
          ))}
        </div>
      </div>
    )
  }

  if (items.length === 0) {
    return (
      <div className="space-y-3">
        <div className="flex items-center gap-2">
          <Rss className="w-4 h-4 text-orange-400" />
          <h2 className="text-sm font-medium text-zinc-400">最近知识摄入</h2>
        </div>
        <div className="border border-dashed border-zinc-700 rounded-xl p-8 text-center">
          <p className="text-zinc-500 text-sm">暂无最近摄入，运行 <code className="text-xs bg-zinc-800 px-1.5 py-0.5 rounded font-mono text-zinc-400">kg pull rss</code> 开始拉取</p>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Rss className="w-4 h-4 text-orange-400" />
          <h2 className="text-sm font-medium text-zinc-400">最近知识摄入</h2>
        </div>
        <button
          onClick={() => setActiveTab('rss')}
          className="flex items-center gap-1 text-xs text-zinc-500 hover:text-zinc-300 transition-colors"
        >
          查看全部 <ChevronRight className="w-3 h-3" />
        </button>
      </div>

      <div
        ref={scrollRef}
        onMouseEnter={() => { isPaused.current = true }}
        onMouseLeave={() => { isPaused.current = false }}
        className="flex gap-4 overflow-x-auto snap-x snap-mandatory scrollbar-thin pb-2"
      >
        {items.map((item) => (
          <div
            key={item.id}
            onClick={() => onPreviewEntry(item.id)}
            className="w-[280px] shrink-0 snap-start p-4 border border-zinc-800/80 bg-zinc-900/40 rounded-xl hover:border-orange-500/40 hover:bg-orange-500/5 cursor-pointer transition-all group"
          >
            <div className="flex items-center justify-between mb-3">
              <span className={`text-[11px] font-medium px-2 py-0.5 rounded-full ${getDomainColor(item.domain)}`}>
                {getDomainLabel(item.domain)}
              </span>
              <span className="text-[11px] text-zinc-600">{formatRelativeDate(item.created)}</span>
            </div>
            <h3 className="text-sm text-zinc-200 font-medium leading-snug line-clamp-2 mb-3 group-hover:text-orange-200 transition-colors">
              {item.title}
            </h3>
            <div className="text-[11px] text-zinc-600 truncate">
              {item.feed_name || item.type}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
