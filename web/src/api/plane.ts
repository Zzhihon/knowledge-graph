// The in-progress board through /api/plane/projects and /api/plane/sessions (the `sessions`
// collector's output in the local state store). Times are Unix seconds.
import { get } from './client'

export type Stage = 'waiting' | 'active' | 'stalled' | 'idle' | 'done'
export type SessionState = 'working' | 'waiting' | 'done' | 'interrupted'

export interface PlaneSession {
  id: string
  source: 'claude' | 'codex' | string
  session_id: string
  project: string
  cwd: string
  branch: string
  started: number | null
  last_active: number
  turns: number
  scheduled: boolean
  last_user: string
  last_agent: string
  last_agent_tail: string
  last_agent_ts: number | null
  last_event: string
  last_tool: string
  writing: boolean
  state: SessionState
  waiting: boolean
  waiting_reason: string
  resume: string
  orca_worktree: string | null
}

export interface PlaneRepo {
  path: string
  name: string
  branch: string
  upstream: string
  ahead: number | null
  behind: number | null
  dirty: number
  changed: number
  untracked: number
  last_commit: number | null
  github: string
}

export interface OrcaWorktree { path: string; name: string; branch: string; live: number; unread: boolean; status: string; last_output: number | null }

export interface PlaneProject {
  id: string
  name: string
  stage: Stage
  stage_label: string
  reason: string
  last_action: number | null
  sessions: { total: number; recent: number; working: number; waiting: number; done: number; interrupted: number }
  last_conclusion: { text: string; ts: number; session: string; source: string } | null
  repos: PlaneRepo[]
  dirty: number
  open_prs: number | null
  prs: { number: number; title: string; draft: boolean; updated: string }[]
  prs_read_at: string | null
  orca: { live: number; unread: number; worktrees: OrcaWorktree[] } | null
  kb: { path: string; title: string; status: string; state: string; as_of: string; updated: string } | null
  collected_at: number
}

interface Fresh { collected_at: number | null; stale: boolean }

export interface ProjectsBody extends Fresh {
  items: PlaneProject[]
  count: number
  attention: { waiting: PlaneSession[]; stalled: PlaneProject[]; waiting_older: number }
}

export interface SessionsBody extends Fresh { items: PlaneSession[]; count: number }

export const plane = {
  projects: () => get<ProjectsBody>('/plane/projects'),
  sessions: (project?: string) => get<SessionsBody>('/plane/sessions', { project }),
}
