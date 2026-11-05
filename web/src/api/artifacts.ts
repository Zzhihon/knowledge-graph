// The artifact center's /api/artifacts: what `kg plane collect artifacts` recorded, and the
// sandboxed preview of a local file inside the registered folders.
import { get } from './client'

export type Visibility = 'public' | 'private' | 'local'

export interface ArtifactHit { kind: string; label: string; count: number; samples: string[] }
export interface ArtifactIncident { id: number; rule: string; severity: 'info' | 'warning' | 'critical' | string; summary: string; opened_at: number }

export interface ArtifactAttrs {
  title: string
  origin: string
  adapter: 'orca' | 'local' | 'claude' | string
  visibility: Visibility
  url: string | null
  local_file: string | null
  created_at: number | null
  updated_at: number | null
  expires_at: number | null
  project: string | null
  session: string | null
  content_hash: string | null
  same_file: string[]
  file_name?: string
  bytes?: number
}

export interface Artifact {
  id: string
  name: string
  attrs: ArtifactAttrs
  status: string | null
  status_ts: number | null
  first_seen: number
  last_seen: number
  gone_at: number | null
  sensitive: boolean | null
  hits: ArtifactHit[]
  previewable: boolean
  incidents: ArtifactIncident[]
}

export interface ArtifactList { items: Artifact[]; count: number; roots: string[]; claude_import: { exists: boolean } }

export const artifacts = {
  list: (gone = false) => get<ArtifactList>('/artifacts', { gone: gone ? 1 : undefined }),
  previewUrl: (id: string) => `/api/artifacts/preview?id=${encodeURIComponent(id)}`,
}
