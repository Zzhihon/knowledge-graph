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
  surface: 'bg-sky-500/15 text-sky-400 ring-sky-500/20',
  intermediate: 'bg-amber-500/15 text-amber-400 ring-amber-500/20',
  deep: 'bg-violet-500/15 text-violet-400 ring-violet-500/20',
}

const statusColor: Record<string, string> = {
  validated: 'bg-emerald-500/15 text-emerald-400',
  draft: 'bg-zinc-500/15 text-zinc-400',
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
        className="fixed inset-0 bg-black/40 backdrop-blur-sm z-40 animate-fade-in"
        onClick={onClose}
      />

      {/* Panel */}
      <div className="fixed top-0 right-0 h-full w-full max-w-2xl bg-[#0c0c0e] border-l border-zinc-800/60 z-50 flex flex-col animate-slide-in-right">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3.5 border-b border-zinc-800/60 bg-zinc-900/40">
          <div className="flex items-center gap-2 min-w-0">
            <BookOpen className="w-4 h-4 text-zinc-500 shrink-0" />
            <span className="text-sm font-medium text-zinc-200 truncate">
              {loading ? '加载中...' : entry?.title ?? '条目预览'}
            </span>
          </div>
          <div className="flex items-center gap-1.5 shrink-0">
            {entry && (
              <a
                href={obsidianUri}
                className="flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium text-indigo-400 hover:text-indigo-300 bg-indigo-500/10 hover:bg-indigo-500/15 rounded-lg ring-1 ring-indigo-500/20 transition-colors"
              >
                <ExternalLink className="w-3 h-3" />
                在 Obsidian 中编辑
              </a>
            )}
            <button
              onClick={onClose}
              className="p-1.5 rounded-lg text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800 transition-colors"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto">
          {loading && (
            <div className="flex items-center justify-center h-32">
              <div className="text-sm text-zinc-500">加载中...</div>
            </div>
          )}

          {error && (
            <div className="p-5">
              <div className="text-sm text-red-400 bg-red-500/10 rounded-lg px-4 py-3 ring-1 ring-red-500/20">{error}</div>
            </div>
          )}

          {entry && !loading && (
            <>
              {/* Metadata bar */}
              <div className="px-5 py-4 border-b border-zinc-800/40 space-y-3">
                {/* Badges row */}
                <div className="flex flex-wrap items-center gap-1.5">
                  {domains.map((d) => (
                    <span key={d} className="inline-flex items-center gap-1 px-2 py-0.5 text-[11px] font-medium bg-indigo-500/10 text-indigo-400 rounded-md ring-1 ring-indigo-500/20">
                      {d}
                    </span>
                  ))}
                  {entry.depth && (
                    <span className={`inline-flex items-center gap-1 px-2 py-0.5 text-[11px] font-medium rounded-md ring-1 ${depthColor[entry.depth] ?? 'bg-zinc-800 text-zinc-400 ring-zinc-700'}`}>
                      <Layers className="w-2.5 h-2.5" />
                      {depthLabel[entry.depth] ?? entry.depth}
                    </span>
                  )}
                  {entry.status && (
                    <span className={`px-2 py-0.5 text-[11px] font-medium rounded-md ${statusColor[entry.status] ?? 'bg-zinc-800 text-zinc-400'}`}>
                      {entry.status}
                    </span>
                  )}
                  {entry.difficulty && (
                    <span className={`px-2 py-0.5 text-[11px] font-medium rounded-md ${
                      entry.difficulty === 'easy' ? 'bg-emerald-500/10 text-emerald-400' :
                      entry.difficulty === 'medium' ? 'bg-amber-500/10 text-amber-400' :
                      'bg-red-500/10 text-red-400'
                    }`}>
                      {entry.difficulty}
                    </span>
                  )}
                </div>

                {/* Stats row */}
                <div className="flex flex-wrap items-center gap-4 text-xs text-zinc-500">
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
                    <Tag className="w-3 h-3 text-zinc-600" />
                    {entry.tags.map((t) => (
                      <span key={t} className="px-1.5 py-0.5 text-[11px] text-zinc-500 bg-zinc-800/60 rounded">
                        {t}
                      </span>
                    ))}
                  </div>
                )}
              </div>

              {/* Markdown content */}
              <div className="px-5 py-5">
                <div className="prose-chat text-sm leading-relaxed text-zinc-300">
                  <Markdown remarkPlugins={[remarkGfm]}>
                    {entry.content}
                  </Markdown>
                </div>
              </div>

              {/* Related entries */}
              {entry.related.length > 0 && (
                <div className="px-5 py-4 border-t border-zinc-800/40">
                  <div className="text-xs font-medium text-zinc-500 mb-2">关联条目</div>
                  <div className="space-y-1">
                    {entry.related.map((r) => {
                      const rid = r.replace(/^\[\[/, '').replace(/\]\]$/, '')
                      return (
                        <button
                          key={rid}
                          onClick={() => onNavigate?.(rid)}
                          className="block w-full text-left text-xs text-indigo-400 hover:text-indigo-300 hover:bg-zinc-800/40 rounded px-2 py-1 transition-colors truncate"
                        >
                          {rid}
                        </button>
                      )
                    })}
                  </div>
                </div>
              )}

              {/* File path */}
              <div className="px-5 py-3 border-t border-zinc-800/40">
                <div className="text-[11px] text-zinc-600 font-mono truncate" title={entry.file_path}>
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
