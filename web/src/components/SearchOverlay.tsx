// ⌘K: one search over knowledge entries, artifact pages, automation rules and kg-web's own pages.
// Entries come from /api/typeahead as before; artifact pages (#6) from the same endpoint with
// kind=artifact, so they never take an entry's place; rules from /api/hub/rules, matched here.
// One source failing leaves the others in the list, with a line saying which one is missing.
import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { Search, X, Loader2, Workflow, FileText, LayoutGrid, Package } from 'lucide-react'
import { get } from '../api/client'
import { hub, type Rule } from '../api/hub'
import { cronInfo } from '../lib/hub'
import type { Module } from '../lib/modules'
import type { ArtifactSearchHit, ArtifactSearchResponse, TypeaheadResponse, TypeaheadResult } from '../types'

interface Props {
  open: boolean
  onClose: () => void
  onSelectEntry: (id: string) => void
  /** Pages to search by name; opening one calls onNavigate. */
  modules?: Module[]
  onNavigate?: (id: string) => void
}

type Hit =
  | { kind: 'entry'; key: string; entry: TypeaheadResult }
  | { kind: 'artifact'; key: string; artifact: ArtifactSearchHit }
  | { kind: 'rule'; key: string; rule: Rule }
  | { kind: 'page'; key: string; page: Module }

const ruleText = (r: Rule) =>
  [r.name, r.to, r.skill, r.prompt, r.group, 'cron' in r.when ? r.when.cron : [r.when.source, ...r.when.types].join(' ')]
    .filter(Boolean).join(' ').toLowerCase()
const ruleWhen = (r: Rule) => ('cron' in r.when ? cronInfo(r.when.cron).text : `收到 ${r.when.types.join('、')}`)

/** Rules whose name, agent, skill, prompt, group or trigger contain every word of the query. */
export function matchRules(rules: Rule[], q: string): Rule[] {
  const words = q.toLowerCase().split(/\s+/).filter(Boolean)
  if (!words.length) return []
  return rules
    .filter((r) => { const t = ruleText(r); return words.every((w) => t.includes(w)) })
    .sort((a, b) => Number(!a.name.toLowerCase().includes(words[0])) - Number(!b.name.toLowerCase().includes(words[0])))
}

export default function SearchOverlay({ open, onClose, onSelectEntry, modules = [], onNavigate }: Props) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<TypeaheadResult[]>([])
  const [entryError, setEntryError] = useState('')
  const [arts, setArts] = useState<ArtifactSearchHit[]>([])
  const [artError, setArtError] = useState('')
  const [rules, setRules] = useState<Rule[] | null>(null)
  const [ruleError, setRuleError] = useState('')
  const [loading, setLoading] = useState(false)
  const [selectedIdx, setSelectedIdx] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)

  // Focus input when overlay opens; read the rules once per opening (they change rarely).
  useEffect(() => {
    if (!open) return
    setQuery('')
    setResults([])
    setArts([])
    setSelectedIdx(0)
    requestAnimationFrame(() => inputRef.current?.focus())
    hub.rules()
      .then((doc) => { setRules(doc.data?.rules ?? []); setRuleError(doc.data ? '' : '规则文件不是合法 JSON') })
      .catch((e) => { setRuleError(e instanceof Error ? e.message : String(e)) })
  }, [open])

  // Debounced entry search
  useEffect(() => {
    if (!query.trim()) {
      setResults([])
      setArts([])
      setSelectedIdx(0)
      return
    }
    const timer = setTimeout(async () => {
      setLoading(true)
      const q = query.trim()
      const [entries, pages] = await Promise.allSettled([
        get<TypeaheadResponse>('/typeahead', { q, limit: 15 }),
        get<ArtifactSearchResponse>('/typeahead', { q, kind: 'artifact', limit: 5 }),
      ])
      // Stale response guard: only apply if query still matches
      if (entries.status === 'fulfilled') {
        if (entries.value.query === q) { setResults(entries.value.results); setEntryError('') }
      } else {
        setEntryError(entries.reason instanceof Error ? entries.reason.message : String(entries.reason))
      }
      if (pages.status === 'fulfilled') {
        if (pages.value.query === q) {
          setArts(pages.value.results ?? [])
          // No artifact in the index yet is not an error worth a red line.
          setArtError(pages.value.error && pages.value.error.code !== 'index_missing' ? pages.value.error.message : '')
        }
      } else {
        setArtError(pages.reason instanceof Error ? pages.reason.message : String(pages.reason))
      }
      setLoading(false)
    }, 300)
    return () => clearTimeout(timer)
  }, [query])

  const hits = useMemo<Hit[]>(() => {
    const q = query.trim().toLowerCase()
    if (!q) return []
    const pages = modules
      .filter((m) => !m.hidden && (m.label.toLowerCase().includes(q) || m.id.includes(q) || (m.blurb ?? '').toLowerCase().includes(q)))
      .slice(0, 4)
      .map((m): Hit => ({ kind: 'page', key: `page:${m.id}`, page: m }))
    const ruleHits = matchRules(rules ?? [], q).slice(0, 6).map((r): Hit => ({ kind: 'rule', key: `rule:${r.name}`, rule: r }))
    const entryHits = results.map((r): Hit => ({ kind: 'entry', key: `entry:${r.id}`, entry: r }))
    const artHits = arts.map((a): Hit => ({ kind: 'artifact', key: `artifact:${a.id}`, artifact: a }))
    return [...pages, ...ruleHits, ...entryHits, ...artHits]
  }, [query, modules, rules, results, arts])

  useEffect(() => { setSelectedIdx(0) }, [hits.length])

  // Scroll selected item into view
  useEffect(() => {
    if (!listRef.current) return
    const items = listRef.current.querySelectorAll('[data-result-item]')
    items[selectedIdx]?.scrollIntoView({ block: 'nearest' })
  }, [selectedIdx])

  const choose = useCallback((h: Hit) => {
    if (h.kind === 'entry') onSelectEntry(h.entry.id)
    else if (h.kind === 'artifact') {
      // Same deep link as the overview: the artifact page opens this one.
      history.replaceState(null, '', `#artifacts&open=${encodeURIComponent(h.artifact.id)}`)
      onNavigate?.('artifacts')
    }
    else if (h.kind === 'rule') onNavigate?.('rules')
    else onNavigate?.(h.page.id)
    onClose()
  }, [onSelectEntry, onNavigate, onClose])

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setSelectedIdx(i => Math.min(i + 1, hits.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setSelectedIdx(i => Math.max(i - 1, 0))
    } else if (e.key === 'Enter' && hits.length > 0) {
      e.preventDefault()
      choose(hits[selectedIdx])
    } else if (e.key === 'Escape') {
      e.preventDefault()
      onClose()
    }
  }, [hits, selectedIdx, choose, onClose])

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
  const HEAD: Record<Hit['kind'], string> = { page: '页面', rule: '规则', entry: '知识条目', artifact: '工件' }
  const q = query.trim()

  return (
    <div
      className="k-app fixed inset-0 z-50 flex items-start justify-center bg-k-bg/60 px-3 pt-[10vh] backdrop-blur-sm sm:pt-[15vh]"
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      <div
        className="w-full max-w-2xl overflow-hidden rounded-xl border border-k-line-strong/50 bg-k-surface shadow-2xl shadow-k-ink/50"
        onKeyDown={handleKeyDown}
      >
        {/* Search input */}
        <div className="flex items-center gap-3 border-b border-k-line px-4 py-3">
          <Search className="h-5 w-5 shrink-0 text-k-ink4" />
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="搜索知识条目、工件、自动化规则、页面…"
            className="flex-1 bg-transparent text-sm text-k-ink outline-none placeholder:text-k-ink4"
          />
          {loading && <Loader2 className="h-4 w-4 shrink-0 animate-spin text-k-ink4" />}
          <kbd className="hidden items-center gap-0.5 rounded border border-k-line-strong bg-k-sunk px-1.5 py-0.5 font-mono text-[10px] text-k-ink4 sm:inline-flex">
            ⌘K
          </kbd>
          <button onClick={onClose} className="text-k-ink4 transition-colors hover:text-k-ink2" aria-label="关闭搜索">
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Results list */}
        <div ref={listRef} className="max-h-[60vh] overflow-y-auto">
          {!q && (
            <div className="px-4 py-10 text-center text-sm text-k-ink4">
              输入关键词，同时搜知识条目、工件正文、自动化规则和页面
            </div>
          )}

          {q && !loading && hits.length === 0 && (
            <div className="px-4 py-10 text-center text-sm text-k-ink4">没有匹配的条目、工件、规则或页面</div>
          )}

          {hits.map((h, idx) => {
            const head = idx === 0 || hits[idx - 1].kind !== h.kind ? HEAD[h.kind] : null
            const sel = idx === selectedIdx
            return (
              <div key={h.key}>
                {head && <div className="px-4 pb-1 pt-3 text-[11px] tracking-[0.08em] text-k-ink4">{head}</div>}
                <button
                  data-result-item
                  onClick={() => choose(h)}
                  onMouseEnter={() => setSelectedIdx(idx)}
                  className={`flex w-full cursor-pointer items-start gap-3 px-4 py-2.5 text-left transition-colors ${
                    sel ? 'border-l-2 border-k-accent bg-k-accent/10' : 'border-l-2 border-transparent hover:bg-k-sunk/50'}`}
                >
                  {h.kind === 'entry' && <FileText className="mt-0.5 h-4 w-4 shrink-0 text-k-ink4" />}
                  {h.kind === 'artifact' && <Package className="mt-0.5 h-4 w-4 shrink-0 text-k-ink4" />}
                  {h.kind === 'rule' && <Workflow className="mt-0.5 h-4 w-4 shrink-0 text-k-ink4" />}
                  {h.kind === 'page' && <LayoutGrid className="mt-0.5 h-4 w-4 shrink-0 text-k-ink4" />}
                  <div className="min-w-0 flex-1">
                    {h.kind === 'entry' && (
                      <>
                        <div className="truncate text-sm text-k-ink">{h.entry.title}</div>
                        <div className="mt-1 flex flex-wrap items-center gap-2">
                          {(Array.isArray(h.entry.domain) ? h.entry.domain.join(', ') : h.entry.domain) && (
                            <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${domainColor(h.entry.domain)}`}>
                              {Array.isArray(h.entry.domain) ? h.entry.domain.join(', ') : h.entry.domain}
                            </span>
                          )}
                          <span className="rounded bg-k-sunk px-1.5 py-0.5 text-[10px] text-k-ink4">{h.entry.type}</span>
                          {(h.entry.tags ?? []).slice(0, 3).map(tag => <span key={tag} className="text-[10px] text-k-ink4">#{tag}</span>)}
                        </div>
                      </>
                    )}
                    {h.kind === 'artifact' && (
                      <>
                        <div className="flex items-center gap-2">
                          <span className="truncate text-sm text-k-ink">{h.artifact.title}</span>
                          {h.artifact.sensitive && (
                            <span className="shrink-0 rounded bg-k-hot/15 px-1.5 py-0.5 text-[10px] text-k-hot" title="原文含内网地址或凭据，这里显示的是脱敏后的正文">敏感</span>
                          )}
                          {h.artifact.url && <span className="shrink-0 text-[10px] text-k-ink4">有分享链接</span>}
                        </div>
                        {h.artifact.snippet && (
                          <div className="mt-0.5 line-clamp-2 text-[11.5px] text-k-ink3">{h.artifact.snippet}</div>
                        )}
                      </>
                    )}
                    {h.kind === 'rule' && (
                      <>
                        <div className="truncate text-sm text-k-ink">{h.rule.name}</div>
                        <div className="mt-0.5 truncate text-[11.5px] text-k-ink4">
                          {ruleWhen(h.rule)} → {h.rule.to}{h.rule.skill ? ` · ${h.rule.skill}` : ''}
                        </div>
                      </>
                    )}
                    {h.kind === 'page' && (
                      <>
                        <div className="truncate text-sm text-k-ink">
                          {h.page.label}
                          {h.page.status === 'planned' && <span className="ml-2 text-[11px] text-k-ink4">规划中 #{h.page.issue}</span>}
                        </div>
                        {h.page.blurb && <div className="mt-0.5 truncate text-[11.5px] text-k-ink4">{h.page.blurb}</div>}
                      </>
                    )}
                  </div>
                  {h.kind === 'entry' && (
                    <div className="flex shrink-0 flex-col items-end gap-1 pt-0.5">
                      {h.entry.confidence != null && (
                        <span className={`font-mono text-[10px] ${
                          h.entry.confidence >= 0.8 ? 'text-k-ok' : h.entry.confidence >= 0.5 ? 'text-k-amber-ink' : 'text-k-hot'}`}>
                          {Math.round(h.entry.confidence * 100)}%
                        </span>
                      )}
                      <span className="font-mono text-[10px] text-k-ink4">{h.entry.score.toFixed(2)}</span>
                    </div>
                  )}
                </button>
              </div>
            )
          })}
          {q && (ruleError || entryError || artError) && (
            <div className="border-t border-k-line-soft px-4 py-2 text-[11.5px] text-k-hot">
              {ruleError && <div>规则没搜：{ruleError}</div>}
              {entryError && <div>条目没搜：{entryError}</div>}
              {artError && <div>工件没搜：{artError}</div>}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center gap-4 border-t border-k-line px-4 py-2 text-[11px] text-k-ink4">
          <span><kbd className="rounded bg-k-sunk px-1 py-0.5 font-mono text-[10px] text-k-ink4">↑↓</kbd> 导航</span>
          <span><kbd className="rounded bg-k-sunk px-1 py-0.5 font-mono text-[10px] text-k-ink4">Enter</kbd> 打开</span>
          <span><kbd className="rounded bg-k-sunk px-1 py-0.5 font-mono text-[10px] text-k-ink4">Esc</kbd> 关闭</span>
        </div>
      </div>
    </div>
  )
}
