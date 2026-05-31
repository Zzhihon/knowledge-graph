import { useState, useEffect, useRef, useCallback } from 'react'
import { Search, X, Loader2 } from 'lucide-react'
import { get } from '../api/client'
import type { TypeaheadResponse, TypeaheadResult } from '../types'

interface Props {
  open: boolean
  onClose: () => void
  onSelectEntry: (id: string) => void
}

export default function SearchOverlay({ open, onClose, onSelectEntry }: Props) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<TypeaheadResult[]>([])
  const [loading, setLoading] = useState(false)
  const [selectedIdx, setSelectedIdx] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)

  // Focus input when overlay opens
  useEffect(() => {
    if (open) {
      setQuery('')
      setResults([])
      setSelectedIdx(0)
      // Small delay to ensure the DOM is ready
      requestAnimationFrame(() => inputRef.current?.focus())
    }
  }, [open])

  // Debounced search
  useEffect(() => {
    if (!query.trim()) {
      setResults([])
      setSelectedIdx(0)
      return
    }

    const timer = setTimeout(async () => {
      setLoading(true)
      try {
        const resp = await get<TypeaheadResponse>('/typeahead', { q: query.trim(), limit: 15 })
        // Stale response guard: only apply if query still matches
        if (resp.query === query.trim()) {
          setResults(resp.results)
          setSelectedIdx(0)
        }
      } catch {
        // silently ignore
      } finally {
        setLoading(false)
      }
    }, 300)

    return () => clearTimeout(timer)
  }, [query])

  // Scroll selected item into view
  useEffect(() => {
    if (!listRef.current) return
    const items = listRef.current.querySelectorAll('[data-result-item]')
    items[selectedIdx]?.scrollIntoView({ block: 'nearest' })
  }, [selectedIdx])

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setSelectedIdx(i => Math.min(i + 1, results.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setSelectedIdx(i => Math.max(i - 1, 0))
    } else if (e.key === 'Enter' && results.length > 0) {
      e.preventDefault()
      onSelectEntry(results[selectedIdx].id)
      onClose()
    } else if (e.key === 'Escape') {
      e.preventDefault()
      onClose()
    }
  }, [results, selectedIdx, onSelectEntry, onClose])

  if (!open) return null

  const domainColor = (d: string | string[]) => {
    const key = Array.isArray(d) ? d[0] : d
    const colors: Record<string, string> = {
      golang: 'bg-cyan-500/15 text-cyan-400',
      algorithm: 'bg-amber-500/15 text-amber-400',
      infrastructure: 'bg-violet-500/15 text-violet-400',
      architecture: 'bg-indigo-500/15 text-indigo-400',
      database: 'bg-emerald-500/15 text-emerald-400',
      frontend: 'bg-rose-500/15 text-rose-400',
      security: 'bg-red-500/15 text-red-400',
      devops: 'bg-orange-500/15 text-orange-400',
    }
    return colors[key?.toLowerCase() ?? ''] ?? 'bg-zinc-500/15 text-zinc-400'
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center pt-[15vh] bg-black/60 backdrop-blur-sm"
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      <div
        className="w-full max-w-2xl bg-zinc-900 border border-zinc-700/50 rounded-xl shadow-2xl shadow-black/50 overflow-hidden"
        onKeyDown={handleKeyDown}
      >
        {/* Search input */}
        <div className="flex items-center gap-3 px-4 py-3 border-b border-zinc-800">
          <Search className="w-5 h-5 text-zinc-500 shrink-0" />
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="搜索知识库中的条目..."
            className="flex-1 bg-transparent text-sm text-zinc-100 placeholder:text-zinc-500 outline-none"
          />
          {loading && <Loader2 className="w-4 h-4 text-zinc-500 animate-spin shrink-0" />}
          <kbd className="hidden sm:inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded bg-zinc-800 border border-zinc-700 text-[10px] text-zinc-500 font-mono">
            ⌘K
          </kbd>
          <button onClick={onClose} className="text-zinc-500 hover:text-zinc-300 transition-colors">
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Results list */}
        <div ref={listRef} className="max-h-[50vh] overflow-y-auto">
          {!query.trim() && (
            <div className="px-4 py-10 text-center text-sm text-zinc-500">
              输入关键词搜索知识库中的条目
            </div>
          )}

          {query.trim() && !loading && results.length === 0 && (
            <div className="px-4 py-10 text-center text-sm text-zinc-500">
              未找到匹配的条目
            </div>
          )}

          {results.map((r, idx) => {
            const domainLabel = Array.isArray(r.domain) ? r.domain.join(', ') : r.domain
            return (
              <button
                key={r.id}
                data-result-item
                onClick={() => { onSelectEntry(r.id); onClose() }}
                onMouseEnter={() => setSelectedIdx(idx)}
                className={`w-full text-left px-4 py-2.5 flex items-start gap-3 transition-colors cursor-pointer ${
                  idx === selectedIdx
                    ? 'bg-indigo-500/10 border-l-2 border-indigo-500'
                    : 'border-l-2 border-transparent hover:bg-zinc-800/50'
                }`}
              >
                <div className="flex-1 min-w-0">
                  <div className="text-sm text-zinc-100 truncate">{r.title}</div>
                  <div className="flex items-center gap-2 mt-1 flex-wrap">
                    {domainLabel && (
                      <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${domainColor(r.domain)}`}>
                        {domainLabel}
                      </span>
                    )}
                    <span className="px-1.5 py-0.5 rounded text-[10px] bg-zinc-800 text-zinc-500">
                      {r.type}
                    </span>
                    {r.tags.slice(0, 3).map(tag => (
                      <span key={tag} className="text-[10px] text-zinc-600">#{tag}</span>
                    ))}
                  </div>
                </div>
                <div className="shrink-0 flex flex-col items-end gap-1 pt-0.5">
                  {r.confidence != null && (
                    <span className={`text-[10px] font-mono ${
                      r.confidence >= 0.8 ? 'text-emerald-500' : r.confidence >= 0.5 ? 'text-amber-500' : 'text-rose-500'
                    }`}>
                      {Math.round(r.confidence * 100)}%
                    </span>
                  )}
                  <span className="text-[10px] text-zinc-600 font-mono">{r.score.toFixed(2)}</span>
                </div>
              </button>
            )
          })}
        </div>

        {/* Footer */}
        <div className="px-4 py-2 border-t border-zinc-800 flex items-center gap-4 text-[11px] text-zinc-600">
          <span><kbd className="px-1 py-0.5 rounded bg-zinc-800 text-zinc-500 font-mono text-[10px]">↑↓</kbd> 导航</span>
          <span><kbd className="px-1 py-0.5 rounded bg-zinc-800 text-zinc-500 font-mono text-[10px]">Enter</kbd> 打开</span>
          <span><kbd className="px-1 py-0.5 rounded bg-zinc-800 text-zinc-500 font-mono text-[10px]">Esc</kbd> 关闭</span>
        </div>
      </div>
    </div>
  )
}
