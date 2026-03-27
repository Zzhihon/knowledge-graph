import { useState, useRef, useCallback } from 'react'

export interface IntegrateEvent {
  seq: number
  event: string
  entry_id?: string
  phase?: string
  message?: string
  [key: string]: unknown
}

export interface IntegrateSummary {
  total_entries: number
  interview_linked: number
  interview_generated: number
  moc_updated: number
  relations_added: number
}

export type IntegratePhase = 'idle' | 'running' | 'complete'

export interface IntegrateState {
  phase: IntegratePhase
  events: IntegrateEvent[]
  currentMessage: string
  totalEntries: number
  processedEntries: number
  summary: IntegrateSummary | null
  errors: string[]
}

export interface IntegrateActions {
  start: (entryIds: string[], opts?: { dryRun?: boolean; skipInterview?: boolean }) => void
  startAll: (opts?: { dryRun?: boolean; skipInterview?: boolean; types?: string[]; domains?: string[] }) => void
  stop: () => void
  dismiss: () => void
}

const MAX_RETRIES = 3

export function useIntegrate(): [IntegrateState, IntegrateActions] {
  const [phase, setPhase] = useState<IntegratePhase>('idle')
  const [events, setEvents] = useState<IntegrateEvent[]>([])
  const [currentMessage, setCurrentMessage] = useState('')
  const [totalEntries, setTotalEntries] = useState(0)
  const [processedEntries, setProcessedEntries] = useState(0)
  const [summary, setSummary] = useState<IntegrateSummary | null>(null)
  const [errors, setErrors] = useState<string[]>([])

  const jobIdRef = useRef<string | null>(null)
  const lastSeqRef = useRef<number>(-1)
  const abortRef = useRef<AbortController | null>(null)
  const retryCountRef = useRef(0)

  const connectStream = useCallback(async (jobId: string) => {
    retryCountRef.current = 0

    while (true) {
      if (!jobIdRef.current) return

      const controller = new AbortController()
      abortRef.current = controller

      try {
        const res = await fetch(
          `/api/integrate/${jobId}/stream?last_event_id=${lastSeqRef.current}`,
          { signal: controller.signal },
        )

        if (!res.ok || !res.body) {
          if (res.status === 404) {
            setPhase('idle')
            jobIdRef.current = null
            return
          }
          throw new Error(`Stream request failed: ${res.status}`)
        }

        retryCountRef.current = 0

        const reader = res.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''

        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })

          const lines = buffer.split('\n')
          buffer = lines.pop() || ''

          let currentEvent = ''
          for (const line of lines) {
            if (line.startsWith('event: ')) {
              currentEvent = line.slice(7).trim()
            } else if (line.startsWith('data: ')) {
              try {
                const data = JSON.parse(line.slice(6))
                if (typeof data.seq === 'number') {
                  lastSeqRef.current = data.seq
                }

                const evt: IntegrateEvent = { ...data, event: currentEvent }
                setEvents(prev => [...prev, evt])

                switch (currentEvent) {
                  case 'start':
                    setTotalEntries(data.total || 0)
                    break
                  case 'phase':
                    setCurrentMessage(data.message || '')
                    break
                  case 'entry_done':
                    setProcessedEntries(data.index || 0)
                    break
                  case 'error':
                    setErrors(prev => [...prev, data.message as string])
                    break
                  case 'complete':
                    setSummary(data as IntegrateSummary)
                    setPhase('complete')
                    break
                  case 'cancelled':
                    setPhase('idle')
                    break
                }
              } catch { /* skip malformed */ }
            }
          }
        }

        setPhase(prev => (prev === 'running' ? 'complete' : prev))
        return
      } catch (err: unknown) {
        if (err instanceof Error && err.name === 'AbortError') return

        retryCountRef.current++
        if (retryCountRef.current > MAX_RETRIES || !jobIdRef.current) {
          setErrors(prev => [...prev, err instanceof Error ? err.message : '连接失败'])
          setPhase('idle')
          return
        }

        const delay = 1000 * Math.pow(2, retryCountRef.current - 1)
        await new Promise(r => setTimeout(r, delay))
      }
    }
  }, [])

  const start = useCallback(
    async (entryIds: string[], opts?: { dryRun?: boolean; skipInterview?: boolean }) => {
      setPhase('running')
      setEvents([])
      setCurrentMessage('启动整合网关...')
      setTotalEntries(0)
      setProcessedEntries(0)
      setSummary(null)
      setErrors([])
      lastSeqRef.current = -1
      retryCountRef.current = 0

      try {
        const res = await fetch('/api/integrate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            entry_ids: entryIds,
            dry_run: opts?.dryRun ?? false,
            skip_interview: opts?.skipInterview ?? false,
          }),
        })

        if (!res.ok) {
          setErrors(['请求失败: ' + res.status])
          setPhase('idle')
          return
        }

        const { job_id } = await res.json()
        jobIdRef.current = job_id
        connectStream(job_id)
      } catch (err: unknown) {
        setErrors([err instanceof Error ? err.message : '创建整合任务失败'])
        setPhase('idle')
      }
    },
    [connectStream],
  )

  const startAll = useCallback(
    async (opts?: { dryRun?: boolean; skipInterview?: boolean; types?: string[]; domains?: string[] }) => {
      setPhase('running')
      setEvents([])
      setCurrentMessage('收集全部条目...')
      setTotalEntries(0)
      setProcessedEntries(0)
      setSummary(null)
      setErrors([])
      lastSeqRef.current = -1
      retryCountRef.current = 0

      try {
        const res = await fetch('/api/integrate/all', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            dry_run: opts?.dryRun ?? false,
            skip_interview: opts?.skipInterview ?? false,
            types: opts?.types ?? null,
            domains: opts?.domains ?? null,
          }),
        })

        if (!res.ok) {
          setErrors(['请求失败: ' + res.status])
          setPhase('idle')
          return
        }

        const data = await res.json()
        if (!data.job_id) {
          setErrors([data.error || '没有符合条件的条目'])
          setPhase('idle')
          return
        }

        setTotalEntries(data.total || 0)
        setCurrentMessage(`启动整合: ${data.total} 个条目...`)
        jobIdRef.current = data.job_id
        connectStream(data.job_id)
      } catch (err: unknown) {
        setErrors([err instanceof Error ? err.message : '创建全量整合任务失败'])
        setPhase('idle')
      }
    },
    [connectStream],
  )

  const stop = useCallback(() => {
    abortRef.current?.abort()
    if (jobIdRef.current) {
      fetch(`/api/integrate/${jobIdRef.current}/cancel`, { method: 'POST' }).catch(() => {})
    }
    jobIdRef.current = null
    setPhase('idle')
  }, [])

  const dismiss = useCallback(() => {
    if (phase === 'complete') {
      setPhase('idle')
      setSummary(null)
      setEvents([])
      setErrors([])
      setCurrentMessage('')
      jobIdRef.current = null
    }
  }, [phase])

  const state: IntegrateState = {
    phase, events, currentMessage, totalEntries,
    processedEntries, summary, errors,
  }

  const actions: IntegrateActions = { start, startAll, stop, dismiss }

  return [state, actions]
}
