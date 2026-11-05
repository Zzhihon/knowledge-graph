// The 出口 page's data: GET /api/plane/egress (see agents/api_routes/egress.py). Times are Unix seconds.
import { get } from './client'

export type EgressStatus = 'up' | 'down' | 'degraded' | 'unknown' | 'asleep' | 'nodata'

export interface EgressSpan { from: number; to: number; status: EgressStatus }
export interface EgressPoint { ts: number; total_ms: number | null; tls_ms: number | null; node_ms: number | null; status: EgressStatus }
export interface EgressExit { exit_ip: string; loc: string | null; colo: string | null; from: number; to: number; probes: number }
export interface EgressSegment { status: string; reason?: string; ms?: number; detail?: Record<string, unknown> }
export interface PurityCheck { status: 'ok' | 'warn' | 'hint' | 'unknown' | 'skipped'; checked_at: number | null; summary: string; detail?: Record<string, unknown> }
export interface EgressAvailability {
  target: number; window_s: number; ratio: number | null; good_s: number; bad_s: number; excluded_s: number
  budget_s: number; budget_left_s: number; budget_left_ratio: number | null
}
export interface PlaneIncident {
  id: number; resource_id: string; rule: string; opened_at: number; closed_at: number | null
  severity: 'info' | 'warning' | 'critical'; summary: string; evidence: Record<string, unknown> | null
}
export interface EgressLine {
  id: string
  name: string
  attrs: Record<string, unknown>
  interval_s: number
  status: EgressStatus | null
  fault: string | null
  reason: string | null
  last_ts: number | null
  segments: Record<string, EgressSegment> | null
  node: string | null
  bar: EgressSpan[]
  latency: EgressPoint[]
  latency_stats: { p50: number | null; p95: number | null; max: number | null; count: number }
  exits: EgressExit[]
  purity: { checks: Record<string, PurityCheck>; checked_at: number | null; status: string | null }
  availability: EgressAvailability
  availability_24h: EgressAvailability
  incidents: PlaneIncident[]
}
export interface EgressState { now: number; hours: number; lines: EgressLine[] }

export const fetchEgress = (params?: { hours?: number; now?: number }) => get<EgressState>('/plane/egress', params)
