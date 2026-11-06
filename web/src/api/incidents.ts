// The 事故 page's endpoints (agents/api_routes/incidents.py). Times are Unix seconds.
import { get } from './client'

export type Severity = 'info' | 'warning' | 'critical'

export interface TimelineStep { from: string; to: string; status: string; count: number; reasons: string[] }

export interface HealAction {
  id: string
  at: number
  incident: number | null
  action: string
  mode: string
  result: string
  target: string
  detail: Record<string, unknown>
  undo: string
  undo_of: string | null
  undone: boolean
  undoable: boolean
}

export interface Incident {
  id: number
  rule: string
  resource_id: string
  resource_name: string
  severity: Severity
  summary: string
  opened_at: number
  closed_at: number | null
  started_at: number | null
  minutes: number
  closed_reason: string | null
  entry_id: string | null
  entry_path: string | null
  timeline: TimelineStep[]
  actions: HealAction[]
}

export interface Silence { rule: string; resource: string; since: number; until: number; reason: string }

export interface IncidentBoard {
  open: Incident[]
  closed: Incident[]
  silenced: Silence[]
  index: { stale: boolean; since: number | null; reason: string }
  now: number
}

export const incidentsApi = {
  board: (days = 7) => get<IncidentBoard>('/plane/incidents/board', { days }),
  // Not client.post: the server's refusal text ("已经撤销过了") is what the page shows.
  undo: async (id: string): Promise<{ ok: boolean; text: string }> => {
    const res = await fetch('/api/plane/heal/undo', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || `撤销失败：${res.status}`)
    return data
  },
}
