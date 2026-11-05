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
      golang: 'bg-k-accent/15 text-k-accent',
      algorithm: 'bg-k-amber/15 text-k-amber-ink',
      infrastructure: 'bg-k-violet/15 text-k-violet',
      architecture: 'bg-k-accent/15 text-k-accent',
      database: 'bg-k-ok/15 text-k-ok',
      frontend: 'bg-k-hot/15 text-k-hot',
      security: 'bg-k-hot/15 text-k-hot',
      devops: 'bg-k-amber/15 text-k-amber-ink',
    }
    return colors[key?.toLowerCase() ?? ''] ?? 'bg-k-ink4/15 text-k-ink3'
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center pt-[15vh] bg-k-bg/60 backdrop-blur-sm"
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      <div
        className="w-full max-w-2xl bg-k-surface border border-k-line-strong/50 rounded-xl shadow-2xl shadow-k-ink/50 overflow-hidden"
        onKeyDown={handleKeyDown}
      >
        {/* Search input */}
        <div className="flex items-center gap-3 px-4 py-3 border-b border-k-line">
          <Search className="w-5 h-5 text-k-ink4 shrink-0" />
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="搜索知识库中的条目..."
            className="flex-1 bg-transparent text-sm text-k-ink placeholder:text-k-ink4 outline-none"
          />
          {loading && <Loader2 className="w-4 h-4 text-k-ink4 animate-spin shrink-0" />}
          <kbd className="hidden sm:inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded bg-k-sunk border border-k-line-strong text-[10px] text-k-ink4 font-mono">
            ⌘K
          </kbd>
          <button onClick={onClose} className="text-k-ink4 hover:text-k-ink2 transition-colors">
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Results list */}
        <div ref={listRef} className="max-h-[50vh] overflow-y-auto">
          {!query.trim() && (
            <div className="px-4 py-10 text-center text-sm text-k-ink4">
              输入关键词搜索知识库中的条目
            </div>
          )}

          {query.trim() && !loading && results.length === 0 && (
            <div className="px-4 py-10 text-center text-sm text-k-ink4">
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
                    ? 'bg-k-accent/10 border-l-2 border-k-accent'
                    : 'border-l-2 border-transparent hover:bg-k-sunk/50'
                }`}
              >
                <div className="flex-1 min-w-0">
                  <div className="text-sm text-k-ink truncate">{r.title}</div>
                  <div className="flex items-center gap-2 mt-1 flex-wrap">
                    {domainLabel && (
                      <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${domainColor(r.domain)}`}>
                        {domainLabel}
                      </span>
                    )}
                    <span className="px-1.5 py-0.5 rounded text-[10px] bg-k-sunk text-k-ink4">
                      {r.type}
                    </span>
                    {r.tags.slice(0, 3).map(tag => (
                      <span key={tag} className="text-[10px] text-k-ink4">#{tag}</span>
                    ))}
                  </div>
                </div>
                <div className="shrink-0 flex flex-col items-end gap-1 pt-0.5">
                  {r.confidence != null && (
                    <span className={`text-[10px] font-mono ${
                      r.confidence >= 0.8 ? 'text-k-ok' : r.confidence >= 0.5 ? 'text-k-amber-ink' : 'text-k-hot'
                    }`}>
                      {Math.round(r.confidence * 100)}%
                    </span>
                  )}
                  <span className="text-[10px] text-k-ink4 font-mono">{r.score.toFixed(2)}</span>
                </div>
              </button>
            )
          })}
        </div>

        {/* Footer */}
        <div className="px-4 py-2 border-t border-k-line flex items-center gap-4 text-[11px] text-k-ink4">
          <span><kbd className="px-1 py-0.5 rounded bg-k-sunk text-k-ink4 font-mono text-[10px]">↑↓</kbd> 导航</span>
          <span><kbd className="px-1 py-0.5 rounded bg-k-sunk text-k-ink4 font-mono text-[10px]">Enter</kbd> 打开</span>
          <span><kbd className="px-1 py-0.5 rounded bg-k-sunk text-k-ink4 font-mono text-[10px]">Esc</kbd> 关闭</span>
        </div>
      </div>
    </div>
  )
}
