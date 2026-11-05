// Shared words, formats and polling for the automation pages (中台, 规则与运行).
import { useCallback, useEffect, useRef, useState, type CSSProperties } from 'react'
import { hub, type HubState } from '../api/hub'

export const STATE: Record<string, string> = {
  queued: '排队', claimed: '在跑', done: '做完了', failed: '失败', cancelled: '已停止', nothing: '没事可做',
  unknown: '不确定', retrying: '等重试', open: '待处理', closed: '已结案',
}
/** [ink, soft] CSS variables per run state. */
const STATE_TONE: Record<string, [string, string]> = {
  done: ['--k-ok', '--k-ok-soft'],
  claimed: ['--k-accent', '--k-accent-soft'],
  queued: ['--k-amber-ink', '--k-amber-soft'],
  retrying: ['--k-amber-ink', '--k-amber-soft'],
  failed: ['--k-hot', '--k-hot-soft'],
  unknown: ['--k-hot', '--k-hot-soft'],
  cancelled: ['--k-ink-4', '--k-line-soft'],
  nothing: ['--k-ink-4', '--k-line-soft'],
}
export const stateTone = (state: string): CSSProperties => {
  const [c, cs] = STATE_TONE[state] ?? ['--k-ink-4', '--k-line-soft']
  return { ['--c' as string]: `var(${c})`, ['--cs' as string]: `var(${cs})` }
}
export const stateInk = (state: string) => `var(${(STATE_TONE[state] ?? ['--k-ink-4'])[0]})`

export const FACT: Record<string, string> = {
  rules: '规则有变动', ingest: '收到事件', events: '收到事件', tick: '定时到点', claimNext: 'Agent 领走', started: '开始执行',
  heartbeat: '仍在运行', finish: '结束', answer: '回答了卡片', resolve: '结案', cancel: '要求停止', retry: '重试', tell: '发了通知',
  sweep: '检查超时', createCard: '发了卡片', nextCard: '通道取走卡片', delivered: '送达', failed: '没送出去',
}
export const EVENT: Record<string, string> = {
  'capture.ready': '会话读好了', 'brief.ready': '日报资料齐了', 'digest.ready': 'GitHub 快照读好了', 'try.*': '试跑',
  'schedule.tick': '定时到点',
}
export const BY: Record<string, string> = { owner: '你', 本人: '你', cli: '你', mcp: '你（MCP）', panel: '你' }
export const tr = (map: Record<string, string>, k: string) => map[k] ?? k

const pad = (n: number) => String(n).padStart(2, '0')
export const hm = (iso: string) => { const d = new Date(iso); return `${pad(d.getHours())}:${pad(d.getMinutes())}` }
export const sameDay = (a: Date, b: Date) => a.toDateString() === b.toDateString()
/** 今天 14:05, 昨天 09:00, or 11-03 09:00. */
export function when(iso: string | null | undefined) {
  if (!iso) return ''
  const d = new Date(iso), now = new Date()
  const y = new Date(now); y.setDate(now.getDate() - 1)
  const day = sameDay(d, now) ? '今天' : sameDay(d, y) ? '昨天' : `${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
  return `${day} ${hm(iso)}`
}
export function ago(iso: string | null | undefined) {
  if (!iso) return ''
  const s = (Date.now() - Date.parse(iso)) / 1000
  if (s < 0) return `${Math.round(-s / 60)} 分钟后`
  if (s < 90) return '刚刚'
  if (s < 3600) return `${Math.round(s / 60)} 分钟前`
  if (s < 86400) return `${Math.round(s / 3600)} 小时前`
  return `${Math.round(s / 86400)} 天前`
}
export function until(iso: string | null | undefined) {
  if (!iso) return ''
  const s = (Date.parse(iso) - Date.now()) / 1000
  if (s <= 0) return '到点了'
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))} 分钟后`
  if (s < 86400) return `${Math.round(s / 3600)} 小时后`
  return `${Math.round(s / 86400)} 天后`
}
export function dur(from: string | null, to: string | null) {
  if (!from || !to) return ''
  const s = Math.max(0, (Date.parse(to) - Date.parse(from)) / 1000)
  if (s < 60) return `${Math.round(s)} 秒`
  if (s < 3600) return `${Math.round(s / 60)} 分钟`
  return `${(s / 3600).toFixed(1)} 小时`
}
export const money = (n: number | undefined | null) => (n ? `$${n < 1 ? n.toFixed(3) : n.toFixed(2)}` : '$0')

/** "30 2 * * *" → 每天 02:30 at minute 150; anything not at a fixed daily time gets words only. */
export function cronInfo(cron: string): { text: string; minutes: number[] } {
  const [m, h, dom, mon, dow] = cron.trim().split(/\s+/)
  const nums = (f: string) => (/^\d+(,\d+)*$/.test(f) ? f.split(',').map(Number) : null)
  const hs = nums(h), ms = nums(m)
  const daily = dom === '*' && mon === '*'
  if ((hs && hs.some((x) => x > 23)) || (ms && ms.some((x) => x > 59))) return { text: '时或分超出范围（时 0–23，分 0–59）', minutes: [] }
  if (hs && ms && daily) {
    const minutes = hs.flatMap((hh) => ms.map((mm) => hh * 60 + mm)).sort((a, b) => a - b)
    const times = minutes.map((x) => `${pad(Math.floor(x / 60))}:${pad(x % 60)}`).join('、')
    const days = dow === '*' ? '每天' : dow === '1-5' ? '工作日' : `星期 ${dow}`
    return { text: `${days} ${times}`, minutes: dow === '*' || dow === '1-5' ? minutes : [] }
  }
  if (m === '*' && h === '*') return { text: '每分钟', minutes: [] }
  if (/^\*\/\d+$/.test(m) && h === '*') return { text: `每 ${m.slice(2)} 分钟`, minutes: [] }
  return { text: `定时 ${cron}`, minutes: [] }
}

/** The hub's state, refreshed every 10 s while the page is visible. */
export function useHubState() {
  const [state, setState] = useState<HubState | null>(null)
  const [error, setError] = useState('')
  const [at, setAt] = useState<Date | null>(null)
  const busy = useRef(false)
  const refresh = useCallback(async () => {
    if (busy.current) return
    busy.current = true
    try {
      setState(await hub.state())
      setError('')
      setAt(new Date())
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      busy.current = false
    }
  }, [])
  useEffect(() => {
    refresh()
    const timer = setInterval(() => { if (!document.hidden) refresh() }, 10_000)
    const onVisible = () => { if (!document.hidden) refresh() }
    document.addEventListener('visibilitychange', onVisible)
    return () => { clearInterval(timer); document.removeEventListener('visibilitychange', onVisible) }
  }, [refresh])
  return { state, error, at, refresh }
}

/** id → display name for agents, channels and sources. */
export const namesOf = (s: HubState | null): Record<string, string> =>
  s ? Object.fromEntries([...s.agents, ...s.channels, ...s.sources].map((x) => [x.id, x.name])) : {}
