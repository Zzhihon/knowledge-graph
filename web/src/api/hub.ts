// The agent-autoloop hub through kg-web's /api/hub (the owner key stays on the server), and the
// eval review page's /api/eval. Errors keep the server's own words, so the page can show what the hub said.

export interface Usage { input?: number; output?: number; cached?: number; cost?: number }
export interface Tally { running: number; queued: number; retrying: number; unknown: number; done: number; doneToday: number; nothing: number; usage: Usage }
export interface Spend { rounds: number; tokens: number; cost: number; missingTokens: number; missingCost: number }

export interface HubCard {
  id: number
  version: number
  kind: 'ask' | 'notice' | string
  state: string
  title: string
  text: string
  options: string[]
  recommend: number | null
  choice: number | null
  answer: string | null
  by: string | null
  result: string | null
  link: string | null
  rule: string
  createdAt: string
  decidedAt: string | null
  closedAt: string | null
}

export interface RunEvent { source: string; type: string; subject: string | null; summary: string | null; link: string | null }
export interface HubRun {
  id: number
  rule: string
  agent: string
  state: string
  attempts: number
  createdAt: string
  claimedAt: string | null
  finishedAt: string | null
  notBefore: string | null
  summary: string
  error: string
  link: string
  session: string | null
  usage: Usage | null
  stopping: boolean
  events: RunEvent[]
}

export interface HubLine {
  id: string
  name: string
  group: string
  agent: string
  source: string
  types: string[]
  every: string | null
  next: string | null
  totals: Tally
}

export interface Presence { id: string; name: string; seenAt: string | null; online: boolean }
export interface HubAgent extends Presence { use: string; worker: string; totals: Tally }
export interface Paused { since?: string; by?: string; reason?: string }

export interface HubState {
  now: string
  timezone: string
  spending: { today: Spend; week: Spend }
  totals: Omit<Tally, 'usage'> & { usage: Usage; attention: number }
  cards: HubCard[]
  recentCards: HubCard[]
  sources: { id: string; name: string; events: number }[]
  lines: HubLine[]
  elsewhere: { id: string; name: string; source: string; agent: string; cron: string; every: string; next: string; where: string }[]
  agents: HubAgent[]
  channels: Presence[]
  runs: HubRun[]
  hasMore: boolean
  nextBefore: number | null
  unrouted: unknown
  /** Added by kg-web: rule name → cron, read from rules.json. */
  schedules: Record<string, string>
  /** Added by kg-web: whether pause / run now work yet, and what is paused. */
  control: { available: boolean; paused: Record<string, Paused> }
}

export interface Fact { version?: number; seq: number; at: number; type: string; changes?: unknown[]; [k: string]: unknown }

export type Rule = {
  name: string
  when: { cron: string } | { source: string; types: string[] }
  to: string
  cwd: string
  skill?: string
  prompt?: string
  group?: string
  parallel?: number
  tell?: 'always' | 'needed' | 'never'
  emits?: { source: string; types: string[] }
  [k: string]: unknown
}
export interface RulesFile {
  timezone?: string
  sources: Record<string, { name: string; adapter?: string }>
  agents: Record<string, { name: string; worker: string; use: string; [k: string]: unknown }>
  channels?: Record<string, { name: string }>
  rules: Rule[]
  elsewhere?: unknown[]
  [k: string]: unknown
}
export interface RulesDoc {
  file: string
  version: string
  text: string
  data: RulesFile | null
  parseError: string | null
  backups: { name: string; size: number; mtime: number; ours: boolean }[]
}
export interface AuditEntry {
  at: string
  by: string
  action: string
  note: string
  backup: string
  before: string
  after: string
  changes: { added: string[]; removed: string[]; changed: Record<string, string[]>; sections: string[] }
  diff: string
}
export interface WriteResult { version: string; backup: string; audit: AuditEntry }

/** A refusal from kg-web or the hub, with the checker's issues when there are any. */
export class HubApiError extends Error {
  status: number
  issues: string[]
  fromHub: boolean
  constructor(status: number, message: string, issues: string[] = [], fromHub = false) {
    super(message)
    this.status = status
    this.issues = issues
    this.fromHub = fromHub
  }
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  let res: Response
  try {
    res = await fetch(`/api${path}`, {
      method,
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch (e) {
    throw new HubApiError(0, `连不上 kg-web：${e instanceof Error ? e.message : String(e)}`)
  }
  const text = await res.text()
  let data: any = null
  try { data = text ? JSON.parse(text) : null } catch { /* not JSON */ }
  if (!res.ok) {
    const detail = data?.error ?? (typeof data?.detail === 'string' ? data.detail : null)
    const message = detail ?? (res.status === 404 ? '这个接口还没挂上（/api/hub 未注册？）' : `HTTP ${res.status}`)
    throw new HubApiError(res.status, message, data?.issues ?? [], data?.source === 'hub')
  }
  return data as T
}

export const hub = {
  state: (before?: number) => call<HubState>('GET', before ? `/hub/state?before=${before}` : '/hub/state'),
  why: (run: number) => call<Fact[]>('GET', `/hub/runs/${run}/why`),
  answer: (card: number, body: { choice: number } | { text: string }) =>
    call<{ changed: boolean; state: string }>('POST', `/hub/cards/${card}/answer`, body),
  rules: () => call<RulesDoc>('GET', '/hub/rules'),
  audit: (limit = 50) => call<{ entries: AuditEntry[] }>('GET', `/hub/rules/audit?limit=${limit}`),
  checkRule: (original: string | null, rule: Rule) =>
    call<{ ok: boolean; issues: string[] }>('POST', '/hub/rules/check', { original, rule }),
  checkText: (text: string) => call<{ ok: boolean; issues: string[] }>('POST', '/hub/rules/check', { text }),
  saveRule: (original: string | null, rule: Rule, version: string) =>
    call<WriteResult>('POST', '/hub/rules/save', { original, rule, version }),
  saveText: (text: string, version: string) => call<WriteResult>('PUT', '/hub/rules/text', { text, version }),
  rollback: (version: string, backup?: string) => call<WriteResult>('POST', '/hub/rules/rollback', { version, backup }),
  control: (name: string, action: 'pause' | 'resume' | 'run', reason = '') =>
    call<unknown>('POST', '/hub/rules/control', { name, action, reason }),
}

export interface EvalItem {
  n: number
  q: string
  source: string
  project: string
  unsure?: boolean
  expected: string[]
  candidates: string[]
  state: 'draft' | 'ok' | 'skip'
  none?: boolean
}
export interface EvalDraft { items: EvalItem[]; entries: Record<string, { title: string; board: string; path: string }> }

export const evalSet = {
  draft: () => call<EvalDraft>('GET', '/eval/draft'),
  save: (items: EvalItem[]) => call<{ saved: number }>('POST', '/eval/draft', { items }),
  publish: (items: EvalItem[]) => call<{ saved: number; published: number; labelled: number }>('POST', '/eval/publish', { items }),
}
