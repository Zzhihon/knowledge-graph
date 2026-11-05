import { useState, useEffect } from 'react'
import { X, ExternalLink, Tag, Calendar, Shield, Layers, BookOpen } from 'lucide-react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { get } from '../api/client'
import type { EntryDetail } from '../types'

interface Props {
  entryId: string
  onClose: () => void
  onNavigate?: (entryId: string) => void
}

const depthLabel: Record<string, string> = {
  surface: '浅层',
  intermediate: '中层',
  deep: '深层',
}

const depthColor: Record<string, string> = {
  surface: 'bg-k-accent/15 text-k-accent ring-k-accent/20',
  intermediate: 'bg-k-amber/15 text-k-amber-ink ring-k-amber/20',
  deep: 'bg-k-violet/15 text-k-violet ring-k-violet/20',
}

const statusColor: Record<string, string> = {
  validated: 'bg-k-ok/15 text-k-ok',
  draft: 'bg-k-ink4/15 text-k-ink3',
}

export default function EntryPreview({ entryId, onClose, onNavigate }: Props) {
  const [entry, setEntry] = useState<EntryDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    setLoading(true)
    setError('')
    get<EntryDetail>(`/entries/${entryId}`)
      .then(setEntry)
      .catch(() => setError('加载条目失败'))
      .finally(() => setLoading(false))
  }, [entryId])

  const obsidianUri = entry
    ? `obsidian://open?vault=${encodeURIComponent(entry.vault_name)}&file=${encodeURIComponent(entry.relative_path.replace(/\.md$/, ''))}`
    : ''

  const domains = entry
    ? Array.isArray(entry.domain) ? entry.domain : [entry.domain]
    : []

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-k-bg/40 backdrop-blur-sm z-40 animate-fade-in"
        onClick={onClose}
      />

      {/* Panel */}
      <div className="fixed top-0 right-0 h-full w-full max-w-2xl bg-k-bg border-l border-k-line/60 z-50 flex flex-col animate-slide-in-right">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3.5 border-b border-k-line/60 bg-k-surface/40">
          <div className="flex items-center gap-2 min-w-0">
            <BookOpen className="w-4 h-4 text-k-ink4 shrink-0" />
            <span className="text-sm font-medium text-k-ink truncate">
              {loading ? '加载中...' : entry?.title ?? '条目预览'}
            </span>
          </div>
          <div className="flex items-center gap-1.5 shrink-0">
            {entry && (
              <a
                href={obsidianUri}
                className="flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium text-k-accent hover:text-k-accent bg-k-accent/10 hover:bg-k-accent/15 rounded-lg ring-1 ring-k-accent/20 transition-colors"
              >
                <ExternalLink className="w-3 h-3" />
                在 Obsidian 中编辑
              </a>
            )}
            <button
              onClick={onClose}
              className="p-1.5 rounded-lg text-k-ink4 hover:text-k-ink2 hover:bg-k-sunk transition-colors"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto">
          {loading && (
            <div className="flex items-center justify-center h-32">
              <div className="text-sm text-k-ink4">加载中...</div>
            </div>
          )}

          {error && (
            <div className="p-5">
              <div className="text-sm text-k-hot bg-k-hot/10 rounded-lg px-4 py-3 ring-1 ring-k-hot/20">{error}</div>
            </div>
          )}

          {entry && !loading && (
            <>
              {/* Metadata bar */}
              <div className="px-5 py-4 border-b border-k-line/40 space-y-3">
                {/* Badges row */}
                <div className="flex flex-wrap items-center gap-1.5">
                  {domains.map((d) => (
                    <span key={d} className="inline-flex items-center gap-1 px-2 py-0.5 text-[11px] font-medium bg-k-accent/10 text-k-accent rounded-md ring-1 ring-k-accent/20">
                      {d}
                    </span>
                  ))}
                  {entry.depth && (
                    <span className={`inline-flex items-center gap-1 px-2 py-0.5 text-[11px] font-medium rounded-md ring-1 ${depthColor[entry.depth] ?? 'bg-k-sunk text-k-ink3 ring-k-line-strong'}`}>
                      <Layers className="w-2.5 h-2.5" />
                      {depthLabel[entry.depth] ?? entry.depth}
                    </span>
                  )}
                  {entry.status && (
                    <span className={`px-2 py-0.5 text-[11px] font-medium rounded-md ${statusColor[entry.status] ?? 'bg-k-sunk text-k-ink3'}`}>
                      {entry.status}
                    </span>
                  )}
                  {entry.difficulty && (
                    <span className={`px-2 py-0.5 text-[11px] font-medium rounded-md ${
                      entry.difficulty === 'easy' ? 'bg-k-ok/10 text-k-ok' :
                      entry.difficulty === 'medium' ? 'bg-k-amber/10 text-k-amber-ink' :
                      'bg-k-hot/10 text-k-hot'
                    }`}>
                      {entry.difficulty}
                    </span>
                  )}
                </div>

                {/* Stats row */}
                <div className="flex flex-wrap items-center gap-4 text-xs text-k-ink4">
                  {entry.confidence != null && (
                    <span className="flex items-center gap-1">
                      <Shield className="w-3 h-3" />
                      置信度 {Math.round(entry.confidence * 100)}%
                    </span>
                  )}
                  {entry.created && (
                    <span className="flex items-center gap-1">
                      <Calendar className="w-3 h-3" />
                      创建 {entry.created}
                    </span>
                  )}
                  {entry.updated && entry.updated !== entry.created && (
                    <span className="flex items-center gap-1">
                      <Calendar className="w-3 h-3" />
                      更新 {entry.updated}
                    </span>
                  )}
                  {entry.review_date && (
                    <span>复习 {entry.review_date}</span>
                  )}
                </div>

                {/* Tags */}
                {entry.tags.length > 0 && (
                  <div className="flex flex-wrap items-center gap-1.5">
                    <Tag className="w-3 h-3 text-k-ink4" />
                    {entry.tags.map((t) => (
                      <span key={t} className="px-1.5 py-0.5 text-[11px] text-k-ink4 bg-k-sunk/60 rounded">
                        {t}
                      </span>
                    ))}
                  </div>
                )}
              </div>

              {/* Markdown content */}
              <div className="px-5 py-5">
                <div className="prose-chat text-sm leading-relaxed text-k-ink2">
                  <Markdown remarkPlugins={[remarkGfm]}>
                    {entry.content}
                  </Markdown>
                </div>
              </div>

              {/* Related entries */}
              {entry.related.length > 0 && (
                <div className="px-5 py-4 border-t border-k-line/40">
                  <div className="text-xs font-medium text-k-ink4 mb-2">关联条目</div>
                  <div className="space-y-1">
                    {entry.related.map((r) => {
                      const rid = r.replace(/^\[\[/, '').replace(/\]\]$/, '')
                      return (
                        <button
                          key={rid}
                          onClick={() => onNavigate?.(rid)}
                          className="block w-full text-left text-xs text-k-accent hover:text-k-accent hover:bg-k-sunk/40 rounded px-2 py-1 transition-colors truncate"
                        >
                          {rid}
                        </button>
                      )
                    })}
                  </div>
                </div>
              )}

              {/* File path */}
              <div className="px-5 py-3 border-t border-k-line/40">
                <div className="text-[11px] text-k-ink4 font-mono truncate" title={entry.file_path}>
                  {entry.relative_path}
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </>
  )
}
