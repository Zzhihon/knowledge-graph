// The overview page's data (/api/overview) and the open incidents behind the 异常 badge.
// Both refresh every 30 s while the tab is visible.
import { useCallback, useEffect, useRef, useState } from 'react'
import { get } from '../api/client'

export type Failed = { ok: false; error: string }
export type Block<T> = ({ ok: true } & T) | Failed
export interface Planned { planned: number }

export interface AttentionItem {
  kind: 'incident' | 'card' | 'session'
  urgency: 'critical' | 'card' | 'warning' | 'info' | 'session'
  id: number | string
  title: string
  sub: string
  ts: number
  ask?: boolean
}

export interface Pill {
  id: string
  label: string
  status: 'up' | 'down' | 'asleep' | 'unknown' | 'planned' | 'stale'
  text: string
  count?: number
  issue?: number
  reason?: string
  down?: string[]
  availability?: number | null
  last?: number | null
  period?: number
  stale?: boolean
  /** Replaces the 24-hour line, e.g. how many agents are online. */
  note?: string
  /** Index pill only: files changed since the last build (null when there is no index). */
  pending?: number | null
}

/** A recently active project from the #5 board. */
export interface ProjectCard {
  id: string
  name: string
  stage: 'waiting' | 'active' | 'stalled' | 'idle' | 'done' | string
  stage_label: string
  reason: string
  last_action: number | null
  conclusion: string
  conclusion_ts: number | null
  dirty: number
  open_prs: number | null
  waiting: number
}

/** One of the newest artifacts from #6, with what to watch out for. */
export interface ArtifactCard {
  id: string
  title: string
  adapter: string | null
  visibility: 'public' | 'private' | 'local' | string | null
  project: string | null
  updated: number | null
  incidents: number
  expires_at: number | null
  expired: boolean
  expiring: boolean
  sensitive: boolean | null
  public_sensitive: boolean
}

export interface Overview {
  now: number
  today: string
  attention: { ok: boolean; items: AttentionItem[]; count: number; errors: Record<string, string> }
  health: Block<{ pills: Pill[] }>
  incidents: Block<{ open: number }>
  daily: Block<{ date: string | null; today: boolean; lead: string }>
  automation: Block<{ rounds: number; running: number; cost: number; missingCost: number }>
  fresh: Block<{ count: number }>
  inprogress: Block<{ items: ProjectCard[]; count: number; stale: boolean; collected_at: number | null }>
  artifacts: Block<{ items: ArtifactCard[]; count: number; expired: number; expiring: number; sensitive: number; public_sensitive: number }>
}

export interface Incident {
  id: number
  resource_id: string
  rule: string
  opened_at: number
  closed_at: number | null
  severity: 'info' | 'warning' | 'critical'
  summary: string
  evidence: Record<string, unknown> | null
}

export const REFRESH_MS = 30_000
export const issueUrl = (n: number) => `https://github.com/Zzhihon/knowledge-graph/issues/${n}`

/** Fetch `load` now and every 30 s while visible; keeps the last good value when a refresh fails. */
export function usePoll<T>(load: () => Promise<T>, ms = REFRESH_MS) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState('')
  const [at, setAt] = useState<Date | null>(null)
  const busy = useRef(false)
  const refresh = useCallback(async () => {
    if (busy.current) return
    busy.current = true
    try {
      setData(await load())
      setError('')
      setAt(new Date())
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      busy.current = false
    }
  }, [load])
  useEffect(() => {
    refresh()
    const timer = setInterval(() => { if (!document.hidden) refresh() }, ms)
    const onVisible = () => { if (!document.hidden) refresh() }
    document.addEventListener('visibilitychange', onVisible)
    return () => { clearInterval(timer); document.removeEventListener('visibilitychange', onVisible) }
  }, [refresh, ms])
  return { data, error, at, refresh }
}

export const fetchOverviewPage = () => get<Overview>('/overview')
export const fetchOpenIncidents = () => get<{ items: Incident[]; count: number }>('/plane/incidents', { open: 1, limit: 100 })

export const SEVERITY: Record<string, string> = { critical: '严重', warning: '警告', info: '提示' }
export const severityTone = (s: string): [string, string] =>
  s === 'critical' ? ['--k-hot', '--k-hot-soft'] : s === 'warning' ? ['--k-amber-ink', '--k-amber-soft'] : ['--k-ink-4', '--k-line-soft']

/** 3 分钟前 / 2 小时前 for Unix seconds. */
export function agoUnix(ts: number | null | undefined) {
  if (!ts) return ''
  const s = Date.now() / 1000 - ts
  if (s < 90) return '刚刚'
  if (s < 3600) return `${Math.round(s / 60)} 分钟前`
  if (s < 86400) return `${Math.round(s / 3600)} 小时前`
  return `${Math.round(s / 86400)} 天前`
}
