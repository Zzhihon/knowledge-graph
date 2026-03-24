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

export function useInterviewGenerate(): [InterviewGenState, InterviewGenActions] {
  const [phase, setPhase] = useState<GenPhase>('idle')
  const [model, setModel] = useState<string | null>(null)
  const [totalExpected, setTotalExpected] = useState(0)
  const [results, setResults] = useState<GenQuestionResult[]>([])
  const [errors, setErrors] = useState<string[]>([])
  const [summary, setSummary] = useState<GenSummary | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  const start = useCallback(async (params: {
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

    const controller = new AbortController()
    abortRef.current = controller

    try {
      const res = await fetch('/api/interview/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(params),
        signal: controller.signal,
      })

      if (!res.ok || !res.body) {
        setErrors(['请求失败: ' + res.status])
        setPhase('idle')
        return
      }

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
              }
            } catch { /* skip */ }
          }
        }
      }

      // If stream ends without explicit 'complete' event
      setPhase(prev => prev === 'generating' ? 'complete' : prev)
    } catch (err: unknown) {
      if (err instanceof Error && err.name !== 'AbortError') {
        setErrors(prev => [...prev, err.message])
      }
      setPhase('idle')
    }
  }, [])

  const stop = useCallback(() => {
    abortRef.current?.abort()
    setPhase('idle')
  }, [])

  const dismiss = useCallback(() => {
    if (phase === 'complete') {
      setPhase('idle')
      setSummary(null)
      setResults([])
      setErrors([])
    }
  }, [phase])

  const state: InterviewGenState = {
    phase, model, totalExpected, results, errors, summary,
  }

  const actions: InterviewGenActions = { start, stop, dismiss }

  return [state, actions]
}
