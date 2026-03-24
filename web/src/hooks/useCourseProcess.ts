import { useState, useRef, useCallback } from 'react'
import type {
  CourseFileInfo,
  CourseFileResult,
  CourseProcessSummary,
  CourseStats,
} from '../types'

export type CoursePhase = 'idle' | 'process' | 'complete'

export interface CourseProcessState {
  phase: CoursePhase
  files: CourseFileInfo[]
  filesLoaded: boolean
  stats: CourseStats | null
  totalFiles: number
  fileResults: CourseFileResult[]
  summary: CourseProcessSummary | null
  error: string | null
}

export interface CourseProcessActions {
  loadFiles: () => void
  loadStats: () => void
  startProcess: (workers: number, dryRun: boolean, qualityCheck: boolean, courseFiles?: string[]) => void
  stopProcess: () => void
}

export function useCourseProcess(): [CourseProcessState, CourseProcessActions] {
  const [phase, setPhase] = useState<CoursePhase>('idle')
  const [files, setFiles] = useState<CourseFileInfo[]>([])
  const [filesLoaded, setFilesLoaded] = useState(false)
  const [stats, setStats] = useState<CourseStats | null>(null)
  const [totalFiles, setTotalFiles] = useState(0)
  const [fileResults, setFileResults] = useState<CourseFileResult[]>([])
  const [summary, setSummary] = useState<CourseProcessSummary | null>(null)
  const [error, setError] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  const loadFiles = useCallback(async (force = false) => {
    if (filesLoaded && !force) return
    try {
      const res = await fetch('/api/course/files')
      const data = await res.json()
      setFiles(data.files || [])
      setFilesLoaded(true)
    } catch {
      setError('无法加载课程文件列表')
    }
  }, [filesLoaded])

  const loadStats = useCallback(async () => {
    try {
      const res = await fetch('/api/course/stats')
      const data = await res.json()
      setStats(data)
    } catch {
      // ignore
    }
  }, [])

  const handleEvent = useCallback((event: string, data: Record<string, unknown>) => {
    switch (event) {
      case 'phase':
        if (data.phase === 'process') {
          setPhase('process')
          setTotalFiles(data.total_files as number)
        }
        break
      case 'file_done':
        setFileResults(prev => {
          const existing = prev.find(item => item.file_path === data.file_path)
          if (!existing) return [...prev, data as unknown as CourseFileResult]
          return prev.map(item => item.file_path === data.file_path
            ? { ...item, ...(data as unknown as CourseFileResult) }
            : item)
        })
        break
      case 'file_retry':
        setFileResults(prev => {
          const existing = prev.find(item => item.file_path === data.file_path)
          const nextAttempt = data as unknown as NonNullable<CourseFileResult['attempts']>[number]
          if (!existing) {
            return [...prev, {
              index: data.index as number,
              total: data.total as number,
              file_name: data.file_name as string,
              file_path: data.file_path as string,
              course_file: '',
              created: 0,
              merged: 0,
              skipped: 0,
              entries: [],
              retry_count: (data.attempt as number) - 1,
              attempts: [nextAttempt],
            }]
          }
          return prev.map(item => {
            if (item.file_path !== data.file_path) return item
            return {
              ...item,
              retry_count: (data.attempt as number) - 1,
              attempts: [...(item.attempts || []), nextAttempt],
            }
          })
        })
        break
      case 'file_failed':
        setFileResults(prev => [...prev, { ...data, error: data.error as string } as unknown as CourseFileResult])
        break
      case 'complete':
        setSummary(data as unknown as CourseProcessSummary)
        setPhase('complete')
        // Refresh files to pick up persisted status
        void loadFiles(true)
        break
      case 'error':
        setError(data.message as string)
        setPhase('idle')
        break
    }
  }, [loadFiles])

  const startProcess = useCallback(async (workers: number, dryRun: boolean, qualityCheck: boolean, courseFiles?: string[]) => {
    setPhase('process')
    setFileResults([])
    setSummary(null)
    setError(null)
    setTotalFiles(0)

    const controller = new AbortController()
    abortRef.current = controller

    try {
      const res = await fetch('/api/course/process', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          workers,
          dry_run: dryRun,
          quality_check: qualityCheck,
          course_files: courseFiles && courseFiles.length > 0 ? courseFiles : null,
        }),
        signal: controller.signal,
      })

      if (!res.ok || !res.body) {
        setError(`请求失败: ${res.status}`)
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
              handleEvent(currentEvent, data)
            } catch { /* ignore parse errors */ }
          }
        }
      }

      // If stream ends without explicit 'complete' event
      setPhase(prev => prev === 'process' ? 'complete' : prev)
    } catch (err: unknown) {
      if (err instanceof Error && err.name !== 'AbortError') {
        setError(`连接错误: ${err.message}`)
      }
    }
  }, [handleEvent])

  const stopProcess = useCallback(() => {
    abortRef.current?.abort()
    setPhase('idle')
  }, [])

  const state: CourseProcessState = {
    phase, files, filesLoaded, stats, totalFiles,
    fileResults, summary, error,
  }

  const actions: CourseProcessActions = { loadFiles: () => { void loadFiles(false) }, loadStats, startProcess, stopProcess }

  return [state, actions]
}
