import { createContext, useContext, type CSSProperties } from 'react'
import { get } from '../api/client'

export interface LibraryCard {
  id: string
  title: string
  type: string
  status: string
  tags: string[]
  updated: string
  summary: string
  diagram: boolean
  path: string
  board: string
  mtime: number
}

export interface LibraryOverview {
  counts: Record<string, number>
  /** id or file stem → [title, path, board] */
  index: Record<string, [string, string, string]>
  dailies: string[]
}

export interface VaultFile {
  path: string
  vault: string
  meta: Record<string, unknown>
  content: string
}

export const fetchOverview = () => get<LibraryOverview>('/library/overview')
export const fetchEntries = (board: string, q: string, limit: number) =>
  get<LibraryCard[]>('/library/entries', { board, q, limit })
export const fetchFile = (path: string) => get<VaultFile>('/library/file', { path })
export const fetchDaily = (date: string) => get<VaultFile>(`/library/daily/${date}`)

export const BOARDS: Record<string, string> = {
  '07-Projects': '项目',
  '10-Experience': '经验',
  '11-Experiments': '实验',
  '03-Debug': '排障',
  '04-Architecture': '架构',
  '01-Principles': '原则',
  '02-Patterns': '模式',
  '05-Research': '研究',
  '08-Problems': '问题',
  '09-Interview': '面试',
  '06-Team': '团队',
}

/** Chip colours per kind of thing, as [ink, soft] CSS variables. */
const TONE: Record<string, [string, string]> = {
  项目: ['--k-accent', '--k-accent-soft'],
  通识: ['--k-amber-ink', '--k-amber-soft'],
  原则: ['--k-amber-ink', '--k-amber-soft'],
  模式: ['--k-amber-ink', '--k-amber-soft'],
  架构: ['--k-accent', '--k-accent-soft'],
  研究: ['--k-amber-ink', '--k-amber-soft'],
  经验: ['--k-ok', '--k-ok-soft'],
  排障: ['--k-hot', '--k-hot-soft'],
  问题: ['--k-hot', '--k-hot-soft'],
  实验: ['--k-violet', '--k-violet-soft'],
  新计划: ['--k-violet', '--k-violet-soft'],
  进行中: ['--k-accent', '--k-accent-soft'],
  可以试试: ['--k-ok', '--k-ok-soft'],
  简报: ['--k-amber-ink', '--k-amber-soft'],
  待办: ['--k-hot', '--k-hot-soft'],
}
export function tone(kind: string): CSSProperties {
  const [c, cs] = TONE[kind] ?? ['--k-ink-4', '--k-line-soft']
  return { ['--c' as string]: `var(${c})`, ['--cs' as string]: `var(${cs})` }
}

/** The ink colour variable for a kind, for dots and rails. */
export const toneInk = (kind: string) => (TONE[kind] ?? ['--k-ink-4'])[0]

export const STATUS: Record<string, string> = {
  idea: '想法', planned: '计划中', trying: '进行中', concluded: '有结论', dropped: '放弃', draft: '草稿',
  validated: '已验证', active: '进行中', paused: '暂停', done: '完成', archived: '归档', superseded: '已被取代', proposed: '待确认',
}

export function ago(seconds: number): string {
  const s = Date.now() / 1000 - seconds
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))} 分钟前`
  if (s < 86400) return `${Math.round(s / 3600)} 小时前`
  return `${Math.round(s / 86400)} 天前`
}

/** What the reading views share: the [[link]] index and a way to open an entry. */
export interface ReaderApi {
  index: LibraryOverview['index']
  open: (path: string) => void
}
export const ReaderContext = createContext<ReaderApi>({ index: {}, open: () => {} })
export const useReader = () => useContext(ReaderContext)
