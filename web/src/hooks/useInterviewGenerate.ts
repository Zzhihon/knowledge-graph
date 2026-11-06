import { useState, useRef, useCallback } from 'react'

export interface GenQuestionResult {
  index: number
  total: number
  title: string
  category: string
  difficulty: string
  file_path: string
}

export interface GenSummary {
  total_created: number
  total_failed: number
}

export type GenPhase = 'idle' | 'generating' | 'complete'

export interface InterviewGenState {
  phase: GenPhase
  model: string | null
  totalExpected: number
  results: GenQuestionResult[]
  errors: string[]
  summary: GenSummary | null
}

export interface InterviewGenActions {
  start: (params: {
    category: string | null
    project: string | null
    skill_domain: string | null
    focus_topic: string | null
    count: number
  }) => void
  stop: () => void
  dismiss: () => void
}

const MAX_RETRIES = 3

export function useInterviewGenerate(): [InterviewGenState, InterviewGenActions] {
  const [phase, setPhase] = useState<GenPhase>('idle')
  const [model, setModel] = useState<string | null>(null)
  const [totalExpected, setTotalExpected] = useState(0)
  const [results, setResults] = useState<GenQuestionResult[]>([])
  const [errors, setErrors] = useState<string[]>([])
  const [summary, setSummary] = useState<GenSummary | null>(null)

  const jobIdRef = useRef<string | null>(null)
  const lastSeqRef = useRef<number>(-1)
  const abortRef = useRef<AbortController | null>(null)
  const retryCountRef = useRef(0)

  const connectStream = useCallback(async (jobId: string) => {
    retryCountRef.current = 0

    // eslint-disable-next-line no-constant-condition
    while (true) {
      if (!jobIdRef.current) return

      const controller = new AbortController()
      abortRef.current = controller

      try {
        const res = await fetch(
          `/api/interview/generate/${jobId}/stream?last_event_id=${lastSeqRef.current}`,
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

        // Connected successfully — reset retry counter
        retryCountRef.current = 0

        const reader = res.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''

        // eslint-disable-next-line no-constant-condition
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
                switch (currentEvent) {
                  case 'start':
                    setTotalExpected(data.total_expected || 0)
                    setModel(data.model || null)
                    break
                  case 'question_done':
                    setResults(prev => [...prev, data as GenQuestionResult])
                    break
                  case 'error':
                    setErrors(prev => [...prev, data.message as string])
                    break
                  case 'complete':
                    setSummary(data as GenSummary)
                    setPhase('complete')
                    break
                  case 'cancelled':
                    setPhase('idle')
                    break
                }
              } catch {
                /* skip malformed data lines */
              }
            }
          }
        }

        // Stream ended naturally
        setPhase(prev => (prev === 'generating' ? 'complete' : prev))
        return
      } catch (err: unknown) {
        if (err instanceof Error && err.name === 'AbortError') return

        retryCountRef.current++
        if (retryCountRef.current > MAX_RETRIES || !jobIdRef.current) {
          setErrors(prev => [
            ...prev,
            err instanceof Error ? err.message : '连接失败',
          ])
          setPhase('idle')
          return
        }

        // Exponential backoff: 1s, 2s, 4s
        const delay = 1000 * Math.pow(2, retryCountRef.current - 1)
        await new Promise(r => setTimeout(r, delay))
        // loop continues -> retry with updated lastSeqRef
      }
    }
  }, [])

  const start = useCallback(
    async (params: {
      category: string | null
      project: string | null
      skill_domain: string | null
      focus_topic: string | null
      count: number
    }) => {
      // Reset state
      setPhase('generating')
      setModel(null)
      setTotalExpected(0)
      setResults([])
      setErrors([])
      setSummary(null)
      lastSeqRef.current = -1
      retryCountRef.current = 0

      try {
        const res = await fetch('/api/interview/generate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(params),
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
        setErrors([err instanceof Error ? err.message : '创建任务失败'])
        setPhase('idle')
      }
    },
    [connectStream],
  )

  const stop = useCallback(() => {
    abortRef.current?.abort()
    // Fire-and-forget cancel
    if (jobIdRef.current) {
      fetch(`/api/interview/generate/${jobIdRef.current}/cancel`, {
        method: 'POST',
      }).catch(() => {})
    }
    jobIdRef.current = null
    setPhase('idle')
  }, [])

  const dismiss = useCallback(() => {
    if (phase === 'complete') {
      setPhase('idle')
      setSummary(null)
      setResults([])
      setErrors([])
      jobIdRef.current = null
    }
  }, [phase])

  const state: InterviewGenState = {
    phase,
    model,
    totalExpected,
    results,
    errors,
    summary,
  }

  const actions: InterviewGenActions = { start, stop, dismiss }

  return [state, actions]
}
