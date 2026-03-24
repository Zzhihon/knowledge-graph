import { useState, useEffect, useCallback } from 'react'
import {
  GraduationCap, Play, Pause, CheckCircle, XCircle, Loader2, FileText,
  ChevronDown, ChevronRight, Plus, GitMerge, SkipForward, Search,
  ChevronLeft, ChevronRight as ChevronRightIcon, FolderOpen,
} from 'lucide-react'
import { get } from '../api/client'
import type { CourseProcessState, CourseProcessActions } from '../hooks/useCourseProcess'
import type { CourseEntryListResponse, CourseEntryItem } from '../types'

interface Props {
  onPreviewEntry?: (entryId: string) => void
  processState: CourseProcessState
  processActions: CourseProcessActions
}

export default function CourseKnowledgeView({ onPreviewEntry, processState, processActions }: Props) {
  const { files, filesLoaded, stats, phase, fileResults, summary, totalFiles, error } = processState
  const { loadFiles, loadStats, startProcess, stopProcess } = processActions

  const [workers, setWorkers] = useState(3)
  const [dryRun, setDryRun] = useState(false)
  const [qualityCheck, setQualityCheck] = useState(true)
  const [expandedFiles, setExpandedFiles] = useState<Set<number>>(new Set())

  // Entry browsing state
  const [entries, setEntries] = useState<CourseEntryItem[]>([])
  const [entriesLoaded, setEntriesLoaded] = useState(false)
  const [entriesTotal, setEntriesTotal] = useState(0)
  const [entriesPage, setEntriesPage] = useState(1)
  const [entriesTotalPages, setEntriesTotalPages] = useState(1)
  const [entriesSearch, setEntriesSearch] = useState('')
  const [entriesFilter, setEntriesFilter] = useState('')

  // Load files + stats on first render
  useEffect(() => {
    if (!filesLoaded) loadFiles()
    loadStats()
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Reload entries when process completes
  useEffect(() => {
    if (phase === 'complete') {
      loadStats()
      fetchEntries()
    }
  }, [phase]) // eslint-disable-line react-hooks/exhaustive-deps

  const fetchEntries = useCallback(async () => {
    try {
      const params: Record<string, string> = {
        page: String(entriesPage),
        page_size: '15',
      }
      if (entriesSearch) params.search = entriesSearch
      if (entriesFilter) params.course_file = entriesFilter
      const res = await get<CourseEntryListResponse>('/course/entries', params)
      setEntries(res.items)
      setEntriesTotal(res.total)
      setEntriesTotalPages(res.total_pages)
      setEntriesLoaded(true)
    } catch {
      // backend may not be up
    }
  }, [entriesPage, entriesSearch, entriesFilter])

  useEffect(() => { fetchEntries() }, [fetchEntries])

  const toggleFile = (index: number) => {
    setExpandedFiles(prev => {
      const next = new Set(prev)
      if (next.has(index)) next.delete(index)
      else next.add(index)
      return next
    })
  }

  const processedCount = fileResults.filter(item => item.created > 0 || item.merged > 0 || item.skipped > 0 || !!item.error).length
  // Failed files: combine in-memory SSE results + persisted backend status
  const failedFromResults = fileResults
    .filter(item => !!item.error)
    .map(item => item.course_file || files.find(file => file.file_path === item.file_path)?.course_file || '')
    .filter(Boolean)
  const failedFromPersisted = files
    .filter(f => f.last_status === 'failed')
    .map(f => f.course_file)
  const failedCourseFiles = Array.from(new Set([...failedFromResults, ...failedFromPersisted]))
  const progressPct = totalFiles > 0 ? Math.round((processedCount / totalFiles) * 100) : 0

  const formatSize = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-zinc-100 flex items-center gap-2">
            <GraduationCap className="w-5 h-5 text-cyan-400" />
            课程知识
          </h1>
          <p className="text-sm text-zinc-500 mt-1">
            从课程课件中提取知识点，便于复习和应对考试
          </p>
        </div>
      </div>

      {/* Stats Cards */}
      {stats && (
        <div className="grid grid-cols-4 gap-4">
          <div className="p-4 rounded-xl border border-zinc-800/80 bg-zinc-900/40">
            <div className="text-xs text-zinc-500 mb-1">课件文件</div>
            <div className="text-lg font-semibold text-zinc-100">{stats.total_files}</div>
          </div>
          <div className="p-4 rounded-xl border border-zinc-800/80 bg-zinc-900/40">
            <div className="text-xs text-zinc-500 mb-1">知识条目</div>
            <div className="text-lg font-semibold text-zinc-100">{stats.total_entries}</div>
          </div>
          <div className="p-4 rounded-xl border border-zinc-800/80 bg-zinc-900/40">
            <div className="text-xs text-zinc-500 mb-1">平均置信度</div>
            <div className="text-lg font-semibold text-zinc-100">
              {stats.avg_confidence != null ? `${Math.round(stats.avg_confidence * 100)}%` : '—'}
            </div>
          </div>
          <div className="p-4 rounded-xl border border-zinc-800/80 bg-zinc-900/40">
            <div className="text-xs text-zinc-500 mb-1">知识域覆盖</div>
            <div className="text-lg font-semibold text-zinc-100">{Object.keys(stats.domain_counts).length}</div>
          </div>
        </div>
      )}

      {/* Source Directory + Files */}
      <div className="bg-zinc-900/50 border border-zinc-800/50 rounded-xl p-5">
        <div className="flex items-center gap-2 mb-3">
          <FolderOpen className="w-4 h-4 text-cyan-400" />
          <span className="text-sm font-medium text-zinc-300">课件源目录</span>
        </div>
        <div className="text-xs text-zinc-500 font-mono mb-3 bg-zinc-950/60 px-3 py-1.5 rounded-lg">
          {stats?.source_dir || files[0]?.file_path?.replace(/\/[^/]+$/, '') || '—'}
        </div>
        <div className="space-y-1">
          {files.map(f => (
            <div key={f.file_name} className="flex items-center justify-between py-1.5 px-2 rounded text-xs hover:bg-zinc-800/30">
              <div className="flex items-center gap-2">
                <FileText className="w-3 h-3 text-cyan-400 shrink-0" />
                <span className="text-zinc-300">{f.file_name}</span>
                {f.last_status === 'failed' && (
                  <span className="text-[10px] text-red-400 bg-red-500/10 px-1.5 py-0.5 rounded">失败</span>
                )}
                {f.last_status === 'success' && (
                  <span className="text-[10px] text-emerald-400 bg-emerald-500/10 px-1.5 py-0.5 rounded">成功</span>
                )}
              </div>
              <div className="flex items-center gap-3 text-zinc-500">
                <span>{formatSize(f.size_bytes)}</span>
                {stats?.course_file_counts[f.course_file] != null && (
                  <span className="text-cyan-400">
                    {stats.course_file_counts[f.course_file]} 条目
                  </span>
                )}
              </div>
            </div>
          ))}
          {!filesLoaded && files.length === 0 && (
            <div className="text-xs text-zinc-600 py-2 text-center">加载中...</div>
          )}
          {filesLoaded && files.length === 0 && (
            <div className="text-xs text-zinc-600 py-2 text-center">目录中无 PDF 文件</div>
          )}
        </div>
      </div>

      {/* Controls */}
      <div className="bg-zinc-900/50 border border-zinc-800/50 rounded-xl p-5">
        <div className="flex items-end gap-4 flex-wrap">
          <div>
            <label className="block text-xs text-zinc-500 mb-1.5">并发数</label>
            <select
              value={workers}
              onChange={e => setWorkers(Number(e.target.value))}
              disabled={phase !== 'idle' && phase !== 'complete'}
              className="bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-1.5 text-sm text-zinc-200 focus:outline-none focus:ring-1 focus:ring-cyan-500"
            >
              <option value={1}>1 线程</option>
              <option value={2}>2 线程</option>
              <option value={3}>3 线程</option>
              <option value={4}>4 线程</option>
              <option value={6}>6 线程</option>
            </select>
          </div>
          <label className="flex items-center gap-2 text-sm text-zinc-400 cursor-pointer pb-1">
            <input
              type="checkbox"
              checked={dryRun}
              onChange={e => setDryRun(e.target.checked)}
              disabled={phase !== 'idle' && phase !== 'complete'}
              className="rounded border-zinc-600 bg-zinc-800 text-cyan-500 focus:ring-cyan-500"
            />
            预览模式
          </label>
          <label className="flex items-center gap-2 text-sm text-zinc-400 cursor-pointer pb-1">
            <input
              type="checkbox"
              checked={qualityCheck}
              onChange={e => setQualityCheck(e.target.checked)}
              disabled={phase !== 'idle' && phase !== 'complete'}
              className="rounded border-zinc-600 bg-zinc-800 text-cyan-500 focus:ring-cyan-500"
            />
            质量评估
          </label>
          <div className="ml-auto flex items-center gap-2">
            {(phase === 'idle' || phase === 'complete') ? (
              <>
                <button
                  onClick={() => startProcess(workers, dryRun, qualityCheck, failedCourseFiles)}
                  disabled={failedCourseFiles.length === 0}
                  className="flex items-center gap-2 px-4 py-2 bg-amber-600/90 hover:bg-amber-600 disabled:opacity-50 text-white rounded-lg text-sm font-medium transition-colors"
                >
                  <Play className="w-4 h-4" />
                  重试失败文件 {failedCourseFiles.length > 0 && `(${failedCourseFiles.length})`}
                </button>
                <button
                  onClick={() => startProcess(workers, dryRun, qualityCheck)}
                  disabled={files.length === 0}
                  className="flex items-center gap-2 px-4 py-2 bg-cyan-600/90 hover:bg-cyan-600 disabled:opacity-50 text-white rounded-lg text-sm font-medium transition-colors"
                >
                  <Play className="w-4 h-4" />
                  {dryRun ? '预览提取' : '开始提取'}
                </button>
              </>
            ) : (
              <button
                onClick={stopProcess}
                className="flex items-center gap-2 px-4 py-2 bg-red-500/80 hover:bg-red-500 text-white rounded-lg text-sm font-medium transition-colors"
              >
                <Pause className="w-4 h-4" />
                停止
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="bg-red-500/10 border border-red-500/30 rounded-xl p-4 text-sm text-red-400">
          {error}
        </div>
      )}

      {/* Progress */}
      {(phase === 'process' || phase === 'complete') && totalFiles > 0 && (
        <div className="bg-zinc-900/50 border border-zinc-800/50 rounded-xl p-5">
          <div className="flex items-center justify-between mb-3">
            <span className="text-sm text-zinc-300 flex items-center gap-2">
              {phase === 'process' && <Loader2 className="w-4 h-4 animate-spin text-cyan-400" />}
              {phase === 'complete' && <CheckCircle className="w-4 h-4 text-emerald-400" />}
              课件处理进度
            </span>
            <span className="text-sm text-zinc-400">
              {processedCount} / {totalFiles} 个文件 ({progressPct}%)
            </span>
          </div>
          <div className="w-full bg-zinc-800 rounded-full h-2">
            <div
              className={`h-2 rounded-full transition-all duration-300 ${
                phase === 'complete' ? 'bg-emerald-500' : 'bg-cyan-500'
              }`}
              style={{ width: `${progressPct}%` }}
            />
          </div>
        </div>
      )}

      {/* Summary */}
      {summary && (
        <div className="grid grid-cols-4 gap-4">
          {[
            { label: '创建', value: summary.created, color: 'text-emerald-400', bg: 'bg-emerald-500/10' },
            { label: '合并', value: summary.merged, color: 'text-yellow-400', bg: 'bg-yellow-500/10' },
            { label: '跳过', value: summary.skipped, color: 'text-zinc-400', bg: 'bg-zinc-500/10' },
            { label: '失败', value: summary.failed, color: 'text-red-400', bg: 'bg-red-500/10' },
          ].map(s => (
            <div key={s.label} className={`${s.bg} border border-zinc-800/50 rounded-xl p-4 text-center`}>
              <div className={`text-2xl font-bold ${s.color}`}>{s.value}</div>
              <div className="text-xs text-zinc-500 mt-1">{s.label}</div>
            </div>
          ))}
        </div>
      )}

      {/* File Results */}
      {fileResults.length > 0 && (
        <div className="bg-zinc-900/50 border border-zinc-800/50 rounded-xl p-5">
          <h3 className="text-sm font-medium text-zinc-300 mb-3 flex items-center gap-2">
            <FileText className="w-4 h-4 text-cyan-400" />
            文件处理结果 ({processedCount}/{totalFiles})
          </h3>
          <div className="space-y-1.5 max-h-[400px] overflow-y-auto">
            {fileResults.map((fr, i) => {
              const hasEntries = fr.entries && fr.entries.length > 0
              const isExpanded = expandedFiles.has(fr.index)
              return (
                <div key={i} className="rounded border border-zinc-800/30">
                  <div
                    className={`flex items-center justify-between py-1.5 px-2 text-xs ${hasEntries ? 'cursor-pointer hover:bg-zinc-800/30' : ''}`}
                    onClick={() => hasEntries && toggleFile(fr.index)}
                  >
                    <div className="flex items-center gap-2 min-w-0">
                      {hasEntries ? (
                        isExpanded
                          ? <ChevronDown className="w-3 h-3 text-zinc-500 shrink-0" />
                          : <ChevronRight className="w-3 h-3 text-zinc-500 shrink-0" />
                      ) : fr.error ? (
                        <XCircle className="w-3 h-3 text-red-400 shrink-0" />
                      ) : (
                        <CheckCircle className="w-3 h-3 text-emerald-400 shrink-0" />
                      )}
                      <span className={`truncate ${fr.error ? 'text-red-400' : 'text-zinc-300'}`}>
                        {fr.file_name}
                      </span>
                    </div>
                    <div className="flex items-center gap-2 shrink-0 ml-2">
                      {(fr.retry_count ?? 0) > 0 && (
                        <span className="text-[10px] text-cyan-400 bg-cyan-500/10 px-1.5 py-0.5 rounded">
                          重试 {fr.retry_count} 次
                        </span>
                      )}
                      {fr.error ? (
                        <span className="text-red-400">失败</span>
                      ) : (
                        <>
                          {(fr.created ?? 0) > 0 && <span className="text-emerald-400">+{fr.created}</span>}
                          {(fr.merged ?? 0) > 0 && <span className="text-yellow-400">~{fr.merged}</span>}
                          {(fr.skipped ?? 0) > 0 && <span className="text-zinc-500">-{fr.skipped}</span>}
                        </>
                      )}
                    </div>
                  </div>
                  {isExpanded && (
                    <div className="border-t border-zinc-800/30 bg-zinc-950/40 px-3 py-2 space-y-2">
                      {(fr.attempts && fr.attempts.length > 0) && (
                        <div className="space-y-1">
                          {fr.attempts.map((attempt, idx) => (
                            <div key={idx} className="text-[11px] text-zinc-500 flex items-center gap-2">
                              <span className={attempt.success ? 'text-emerald-400' : 'text-amber-400'}>
                                #{attempt.attempt}
                              </span>
                              <span>{attempt.strategy}</span>
                              <span>{attempt.quality_check ? '质量评估开启' : '质量评估关闭'}</span>
                              {attempt.error && <span className="text-zinc-600 truncate">{attempt.error}</span>}
                            </div>
                          ))}
                        </div>
                      )}
                      {fr.entries && fr.entries.map((entry, j) => (
                        <div key={j} className="flex items-start gap-2 text-xs">
                          {entry.action === 'create' && <Plus className="w-3 h-3 text-emerald-400 shrink-0 mt-0.5" />}
                          {entry.action === 'merge' && <GitMerge className="w-3 h-3 text-yellow-400 shrink-0 mt-0.5" />}
                          {entry.action === 'skip' && <SkipForward className="w-3 h-3 text-zinc-500 shrink-0 mt-0.5" />}
                          <div className="min-w-0">
                            <span
                              className={`cursor-pointer hover:underline ${
                                entry.action === 'create' ? 'text-zinc-200' :
                                entry.action === 'merge' ? 'text-yellow-300' : 'text-zinc-500'
                              }`}
                              onClick={() => entry.id && onPreviewEntry?.(entry.id)}
                            >
                              {entry.title}
                            </span>
                            <div className="flex items-center gap-2 mt-0.5">
                              {entry.domain && (
                                <span className="text-[10px] text-zinc-600 bg-zinc-800/60 px-1 py-0.5 rounded">{entry.domain}</span>
                              )}
                              {entry.type && (
                                <span className="text-[10px] text-zinc-600 bg-zinc-800/60 px-1 py-0.5 rounded">{entry.type}</span>
                              )}
                              {entry.merge_target && (
                                <span className="text-[10px] text-yellow-600">&rarr; {entry.merge_target}</span>
                              )}
                            </div>
                          </div>
                        </div>
                      ))}
                      {(!fr.entries || fr.entries.length === 0) && fr.error && (
                        <div className="text-xs text-red-400">{fr.error}</div>
                      )}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Course Entry List */}
      <div>
        <h2 className="text-sm font-medium text-zinc-400 mb-3">课程知识条目</h2>
        <div className="flex items-center gap-3 mb-3">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-zinc-500" />
            <input
              type="text"
              placeholder="搜索条目..."
              value={entriesSearch}
              onChange={e => { setEntriesSearch(e.target.value); setEntriesPage(1) }}
              className="w-full pl-10 pr-4 py-2 bg-zinc-950 border border-zinc-800 rounded-lg text-sm text-zinc-300 outline-none focus:border-cyan-500 placeholder:text-zinc-600"
            />
          </div>
          <select
            value={entriesFilter}
            onChange={e => { setEntriesFilter(e.target.value); setEntriesPage(1) }}
            className="bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2 text-sm text-zinc-300 outline-none focus:border-cyan-500"
          >
            <option value="">所有课件</option>
            {files.map(f => (
              <option key={f.course_file} value={f.course_file}>{f.file_stem}</option>
            ))}
          </select>
        </div>

        <div className="border border-zinc-800/80 rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-zinc-800/80 bg-zinc-900/40">
                <th className="text-left px-4 py-3 text-zinc-500 font-medium">标题</th>
                <th className="text-left px-4 py-3 text-zinc-500 font-medium w-24">类型</th>
                <th className="text-left px-4 py-3 text-zinc-500 font-medium w-28">知识域</th>
                <th className="text-left px-4 py-3 text-zinc-500 font-medium w-28">课件</th>
                <th className="text-right px-4 py-3 text-zinc-500 font-medium w-20">置信度</th>
              </tr>
            </thead>
            <tbody>
              {entries.length === 0 ? (
                <tr>
                  <td colSpan={5} className="text-center py-12 text-zinc-600">
                    {!entriesLoaded ? '加载中...' : '暂无课程知识条目，点击「开始提取」处理课件'}
                  </td>
                </tr>
              ) : (
                entries.map(e => (
                  <tr
                    key={e.id}
                    className="border-b border-zinc-800/40 hover:bg-zinc-900/40 cursor-pointer transition-colors"
                    onClick={() => onPreviewEntry?.(e.id)}
                  >
                    <td className="px-4 py-3 text-zinc-200">{e.title}</td>
                    <td className="px-4 py-3">
                      <span className="px-1.5 py-0.5 text-xs bg-zinc-800 text-zinc-400 rounded">{e.type}</span>
                    </td>
                    <td className="px-4 py-3 text-xs text-zinc-500 truncate max-w-[120px]">{e.domain || '—'}</td>
                    <td className="px-4 py-3 text-xs text-zinc-500 truncate max-w-[120px]">{e.course_file || '—'}</td>
                    <td className="px-4 py-3 text-right">
                      {e.confidence != null
                        ? <span className={`text-xs font-mono ${
                            Math.round(e.confidence * 100) >= 80 ? 'text-emerald-400' :
                            Math.round(e.confidence * 100) >= 50 ? 'text-amber-400' : 'text-rose-400'
                          }`}>{Math.round(e.confidence * 100)}%</span>
                        : <span className="text-zinc-600">—</span>
                      }
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {entriesTotalPages > 1 && (
          <div className="flex items-center justify-between text-sm mt-3">
            <span className="text-zinc-500">共 {entriesTotal} 条目</span>
            <div className="flex items-center gap-2">
              <button disabled={entriesPage <= 1} onClick={() => setEntriesPage(p => p - 1)}
                className="p-1.5 rounded-lg bg-zinc-900 border border-zinc-800 disabled:opacity-30 hover:bg-zinc-800 transition-colors">
                <ChevronLeft className="w-4 h-4" />
              </button>
              <span className="text-zinc-400 px-2">{entriesPage} / {entriesTotalPages}</span>
              <button disabled={entriesPage >= entriesTotalPages} onClick={() => setEntriesPage(p => p + 1)}
                className="p-1.5 rounded-lg bg-zinc-900 border border-zinc-800 disabled:opacity-30 hover:bg-zinc-800 transition-colors">
                <ChevronRightIcon className="w-4 h-4" />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
