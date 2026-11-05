import { useEffect, useRef, useState, useCallback } from 'react'
import { Rss, ChevronRight } from 'lucide-react'
import { get } from '../api/client'
import type { RecentFeedItem } from '../types'

interface Props {
  onPreviewEntry: (id: string) => void
  setActiveTab: (tab: string) => void
}

const DOMAIN_COLORS: Record<string, string> = {
  'cloud-native': 'bg-k-accent/20 text-k-accent',
  'golang': 'bg-k-accent/20 text-k-accent',
  'kubernetes': 'bg-k-accent/20 text-k-accent',
  'architecture': 'bg-k-violet/20 text-k-violet',
  'security': 'bg-k-hot/20 text-k-hot',
  'ai-agent': 'bg-k-violet/20 text-k-violet',
  'devops': 'bg-k-amber/20 text-k-amber-ink',
  'distributed-systems': 'bg-k-amber/20 text-k-amber-ink',
  'frontend': 'bg-k-ok/20 text-k-ok',
  'database': 'bg-k-amber/20 text-k-amber-ink',
}

const DEFAULT_COLOR = 'bg-k-ink4/20 text-k-ink3'

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
            <Rss className="w-4 h-4 text-k-amber-ink" />
            <h2 className="text-sm font-medium text-k-ink3">最近知识摄入</h2>
          </div>
        </div>
        <div className="flex gap-4">
          {[1, 2, 3].map((i) => (
            <div key={i} className="w-[280px] h-[140px] shrink-0 rounded-xl bg-k-sunk/30 animate-pulse" />
          ))}
        </div>
      </div>
    )
  }

  if (items.length === 0) {
    return (
      <div className="space-y-3">
        <div className="flex items-center gap-2">
          <Rss className="w-4 h-4 text-k-amber-ink" />
          <h2 className="text-sm font-medium text-k-ink3">最近知识摄入</h2>
        </div>
        <div className="border border-dashed border-k-line-strong rounded-xl p-8 text-center">
          <p className="text-k-ink4 text-sm">暂无最近摄入，运行 <code className="text-xs bg-k-sunk px-1.5 py-0.5 rounded font-mono text-k-ink3">kg pull rss</code> 开始拉取</p>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Rss className="w-4 h-4 text-k-amber-ink" />
          <h2 className="text-sm font-medium text-k-ink3">最近知识摄入</h2>
        </div>
        <button
          onClick={() => setActiveTab('rss')}
          className="flex items-center gap-1 text-xs text-k-ink4 hover:text-k-ink2 transition-colors"
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
            className="w-[280px] shrink-0 snap-start p-4 border border-k-line/80 bg-k-surface/40 rounded-xl hover:border-k-amber/40 hover:bg-k-amber/5 cursor-pointer transition-all group"
          >
            <div className="flex items-center justify-between mb-3">
              <span className={`text-[11px] font-medium px-2 py-0.5 rounded-full ${getDomainColor(item.domain)}`}>
                {getDomainLabel(item.domain)}
              </span>
              <span className="text-[11px] text-k-ink4">{formatRelativeDate(item.created)}</span>
            </div>
            <h3 className="text-sm text-k-ink font-medium leading-snug line-clamp-2 mb-3 group-hover:text-k-amber-ink transition-colors">
              {item.title}
            </h3>
            <div className="text-[11px] text-k-ink4 truncate">
              {item.feed_name || item.type}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
