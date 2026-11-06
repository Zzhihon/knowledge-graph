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

export interface ServicesOverview {
  groups: Record<ServiceGroup, Service[]>
  count: number
  collected_at: number | null
  incidents: number
  statuses: Record<ServiceStatus, number>
}

export interface LogFile { path: string; lines: string[]; error?: string; modified?: number }
export interface ServiceLog { id: string; files: LogFile[]; redacted: boolean }

export const servicesApi = {
  overview: () => get<ServicesOverview>('/plane/services'),
  // The server decodes the path segment back, so ids with "@" or "#" in a label survive.
  log: (id: string, lines = 20) => get<ServiceLog>(`/plane/services/${encodeURIComponent(id)}/log`, { lines }),
}
