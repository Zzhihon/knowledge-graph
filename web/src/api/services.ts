// The 本地服务 page's endpoints (agents/api_routes/services.py). Times are Unix seconds.
import { get } from './client'

export type ServiceGroup = 'mine' | 'tool' | 'system'
export type ServiceStatus = 'up' | 'degraded' | 'down' | 'asleep' | 'unknown'

export interface ServiceIncident { id: number; rule: string; severity: 'info' | 'warning' | 'critical'; summary: string; opened_at: number }
export interface ServiceHealth { url: string; code: number; ms: number | null; ok: boolean; error?: string }
export interface ServicePort { port: number; addresses: string[]; exposed: boolean; id: string; incidents: ServiceIncident[] }

export interface Service {
  id: string
  name: string
  manager: 'launchd' | 'port'
  label: string | null
  port: number | null
  group: ServiceGroup
  status: ServiceStatus | null
  status_ts: number | null
  about: string
  repo: string
  registered: boolean
  alert: boolean
  mode: 'keepalive' | 'scheduled' | 'ondemand' | null
  loaded: boolean | null
  command: string | null
  pid: number | null
  pids: number[] | null
  last_exit: number | null
  runs: number | null
  health: ServiceHealth | null
  expected_port: number | null
  addresses: string[] | null
  exposed: boolean | null
  inferred: boolean
  owner: string | null
  has_log: boolean
  first_seen: number
  last_restart: number | null
  ports: ServicePort[]
  incidents: ServiceIncident[]
}

// A service on another tailnet machine (the remote collector). `unknown` = host asleep/offline.
export interface RemoteService {
  id: string
  name: string
  host: string | null
  label: string | null
  about: string
  repo: string
  status: ServiceStatus | null
  status_ts: number | null
  reachable: boolean
  reason: string | null
  pid: number | null
  runs: number | null
  heartbeat_age_s: number | null
  push_last: { ts: string; ok: boolean; ch: string } | null
  push_failing: boolean
  incidents: ServiceIncident[]
}

// One watched route × departure × return date. price/gap are null until a fare is collected;
// trend is [hour (Unix s), lowest price] over the last 7 days.
export interface Fare {
  service: string
  host: string | null
  label: string
  from: string
  to: string
  depart: string
  return: string | null
  budget: number | null
  price: number | null
  platform: string | null
  flight_no: string | null
  fetched_at: string | null
  gap: number | null
  trend: [number, number][]
}

export interface ServicesOverview {
  groups: Record<ServiceGroup, Service[]>
  count: number
  collected_at: number | null
  incidents: number
  statuses: Record<ServiceStatus, number>
  remote: RemoteService[]
  fares: Fare[]
}

export interface LogFile { path: string; lines: string[]; error?: string; modified?: number }
export interface ServiceLog { id: string; files: LogFile[]; redacted: boolean }

export const servicesApi = {
  overview: () => get<ServicesOverview>('/plane/services'),
  // The server decodes the path segment back, so ids with "@" or "#" in a label survive.
  log: (id: string, lines = 20) => get<ServiceLog>(`/plane/services/${encodeURIComponent(id)}/log`, { lines }),
}
