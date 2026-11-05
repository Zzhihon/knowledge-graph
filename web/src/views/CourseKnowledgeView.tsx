import { useState, useEffect, useCallback } from 'react'
import {
  GraduationCap, Play, Pause, CheckCircle, XCircle, Loader2, FileText,
  ChevronDown, ChevronRight, Plus, GitMerge, SkipForward, Search,
  ChevronLeft, ChevronRight as ChevronRightIcon, FolderOpen, RotateCcw,
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
  const [selectedFiles, setSelectedFiles] = useState<Set<string>>(new Set())

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
  const isProcessing = phase === 'process'
  const isIdle = phase === 'idle' || phase === 'complete'

  const toggleFileSelection = (courseFile: string) => {
    setSelectedFiles(prev => {
      const next = new Set(prev)
      if (next.has(courseFile)) next.delete(courseFile)
      else next.add(courseFile)
      return next
    })
  }

  const toggleSelectAll = () => {
    if (selectedFiles.size === files.length) {
      setSelectedFiles(new Set())
    } else {
      setSelectedFiles(new Set(files.map(f => f.course_file)))
    }
  }

  const handleStartSelected = () => {
    if (isProcessing || selectedFiles.size === 0) return
    startProcess(workers, dryRun, qualityCheck, Array.from(selectedFiles))
  }

  const handleStartAll = () => {
    if (isProcessing) return
    startProcess(workers, dryRun, qualityCheck)
  }

  const handleRetryFailed = () => {
    if (isProcessing || failedCourseFiles.length === 0) return
    startProcess(workers, dryRun, qualityCheck, failedCourseFiles)
  }

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
          <h1 className="text-xl font-semibold text-k-ink flex items-center gap-2">
            <GraduationCap className="w-5 h-5 text-k-accent" />
            课程知识
          </h1>
          <p className="text-sm text-k-ink4 mt-1">
            从课程课件中提取知识点，便于复习和应对考试
          </p>
        </div>
      </div>

      {/* Stats Cards */}
      {stats && (
        <div className="grid grid-cols-4 gap-4">
          <div className="p-4 rounded-xl border border-k-line/80 bg-k-surface/40">
            <div className="text-xs text-k-ink4 mb-1">课件文件</div>
            <div className="text-lg font-semibold text-k-ink">{stats.total_files}</div>
          </div>
          <div className="p-4 rounded-xl border border-k-line/80 bg-k-surface/40">
            <div className="text-xs text-k-ink4 mb-1">知识条目</div>
            <div className="text-lg font-semibold text-k-ink">{stats.total_entries}</div>
          </div>
          <div className="p-4 rounded-xl border border-k-line/80 bg-k-surface/40">
            <div className="text-xs text-k-ink4 mb-1">平均置信度</div>
            <div className="text-lg font-semibold text-k-ink">
              {stats.avg_confidence != null ? `${Math.round(stats.avg_confidence * 100)}%` : '—'}
            </div>
          </div>
          <div className="p-4 rounded-xl border border-k-line/80 bg-k-surface/40">
            <div className="text-xs text-k-ink4 mb-1">知识域覆盖</div>
            <div className="text-lg font-semibold text-k-ink">{Object.keys(stats.domain_counts).length}</div>
          </div>
        </div>
      )}

      {/* Source Directory + Files with checkboxes */}
      <div className="bg-k-surface/50 border border-k-line/50 rounded-xl p-5">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <FolderOpen className="w-4 h-4 text-k-accent" />
            <span className="text-sm font-medium text-k-ink2">课件源目录</span>
          </div>
          {files.length > 0 && isIdle && (
            <button
              onClick={toggleSelectAll}
              className="text-xs text-k-ink4 hover:text-k-ink2 transition-colors"
            >
              {selectedFiles.size === files.length ? '取消全选' : '全选'}
            </button>
          )}
        </div>
        <div className="text-xs text-k-ink4 font-mono mb-3 bg-k-bg/60 px-3 py-1.5 rounded-lg">
          {stats?.source_dir || files[0]?.file_path?.replace(/\/[^/]+$/, '') || '—'}
        </div>
        <div className="space-y-1">
          {files.map(f => (
            <div key={f.file_name} className="flex items-center justify-between py-1.5 px-2 rounded text-xs hover:bg-k-sunk/30">
              <div className="flex items-center gap-2">
                {isIdle && (
                  <input
                    type="checkbox"
                    checked={selectedFiles.has(f.course_file)}
                    onChange={() => toggleFileSelection(f.course_file)}
                    className="rounded border-k-line-strong bg-k-sunk text-k-accent focus:ring-k-accent w-3.5 h-3.5"
                  />
                )}
                <FileText className="w-3 h-3 text-k-accent shrink-0" />
                <span className="text-k-ink2">{f.file_name}</span>
                {f.last_status === 'failed' && (
                  <span className="text-[10px] text-k-hot bg-k-hot/10 px-1.5 py-0.5 rounded">失败</span>
                )}
                {f.last_status === 'success' && (
                  <span className="text-[10px] text-k-ok bg-k-ok/10 px-1.5 py-0.5 rounded">成功</span>
                )}
              </div>
              <div className="flex items-center gap-3 text-k-ink4">
                <span>{formatSize(f.size_bytes)}</span>
                {stats?.course_file_counts[f.course_file] != null && (
                  <span className="text-k-accent">
                    {stats.course_file_counts[f.course_file]} 条目
                  </span>
                )}
              </div>
            </div>
          ))}
          {!filesLoaded && files.length === 0 && (
            <div className="text-xs text-k-ink4 py-2 text-center">加载中...</div>
          )}
          {filesLoaded && files.length === 0 && (
            <div className="text-xs text-k-ink4 py-2 text-center">目录中无 PDF 文件</div>
          )}
        </div>
      </div>

      {/* Controls */}
      <div className="bg-k-surface/50 border border-k-line/50 rounded-xl p-5">
        <div className="flex items-end gap-4 flex-wrap">
          <div>
            <label className="block text-xs text-k-ink4 mb-1.5">并发数</label>
            <select
              value={workers}
              onChange={e => setWorkers(Number(e.target.value))}
              disabled={isProcessing}
              className="bg-k-sunk border border-k-line-strong rounded-lg px-3 py-1.5 text-sm text-k-ink focus:outline-none focus:ring-1 focus:ring-k-accent"
            >
              <option value={1}>1 线程</option>
              <option value={2}>2 线程</option>
              <option value={3}>3 线程</option>
              <option value={4}>4 线程</option>
              <option value={6}>6 线程</option>
            </select>
          </div>
          <label className="flex items-center gap-2 text-sm text-k-ink3 cursor-pointer pb-1">
            <input
              type="checkbox"
              checked={dryRun}
              onChange={e => setDryRun(e.target.checked)}
              disabled={isProcessing}
              className="rounded border-k-line-strong bg-k-sunk text-k-accent focus:ring-k-accent"
            />
            预览模式
          </label>
          <label className="flex items-center gap-2 text-sm text-k-ink3 cursor-pointer pb-1">
            <input
              type="checkbox"
              checked={qualityCheck}
              onChange={e => setQualityCheck(e.target.checked)}
              disabled={isProcessing}
              className="rounded border-k-line-strong bg-k-sunk text-k-accent focus:ring-k-accent"
            />
            质量评估
          </label>
          <div className="ml-auto flex items-center gap-2">
            {isIdle ? (
              <>
                {failedCourseFiles.length > 0 && (
                  <button
                    onClick={handleRetryFailed}
                    className="flex items-center gap-2 px-4 py-2 bg-k-amber/90 hover:bg-k-amber text-white rounded-lg text-sm font-medium transition-colors"
                  >
                    <RotateCcw className="w-4 h-4" />
                    重试失败 ({failedCourseFiles.length})
                  </button>
                )}
                <button
                  onClick={handleStartSelected}
                  disabled={selectedFiles.size === 0}
                  className="flex items-center gap-2 px-4 py-2 bg-k-accent/90 hover:bg-k-accent disabled:opacity-50 text-white rounded-lg text-sm font-medium transition-colors"
                >
                  <Play className="w-4 h-4" />
                  处理选中 ({selectedFiles.size})
                </button>
                <button
                  onClick={handleStartAll}
                  disabled={files.length === 0}
                  className="flex items-center gap-2 px-4 py-2 bg-k-accent/90 hover:bg-k-accent disabled:opacity-50 text-white rounded-lg text-sm font-medium transition-colors"
                >
                  <Play className="w-4 h-4" />
                  {dryRun ? '预览全部' : '提取全部'}
                </button>
              </>
            ) : (
              <button
                onClick={stopProcess}
                className="flex items-center gap-2 px-4 py-2 bg-k-hot/80 hover:bg-k-hot text-white rounded-lg text-sm font-medium transition-colors"
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
        <div className="bg-k-hot/10 border border-k-hot/30 rounded-xl p-4 text-sm text-k-hot">
          {error}
        </div>
      )}

      {/* Progress */}
      {(phase === 'process' || phase === 'complete') && totalFiles > 0 && (
        <div className="bg-k-surface/50 border border-k-line/50 rounded-xl p-5">
          <div className="flex items-center justify-between mb-3">
            <span className="text-sm text-k-ink2 flex items-center gap-2">
              {phase === 'process' && <Loader2 className="w-4 h-4 animate-spin text-k-accent" />}
              {phase === 'complete' && <CheckCircle className="w-4 h-4 text-k-ok" />}
              课件处理进度
            </span>
            <span className="text-sm text-k-ink3">
              {processedCount} / {totalFiles} 个文件 ({progressPct}%)
            </span>
          </div>
          <div className="w-full bg-k-sunk rounded-full h-2">
            <div
              className={`h-2 rounded-full transition-all duration-300 ${
                phase === 'complete' ? 'bg-k-ok' : 'bg-k-accent'
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
            { label: '创建', value: summary.created, color: 'text-k-ok', bg: 'bg-k-ok/10' },
            { label: '合并', value: summary.merged, color: 'text-k-amber-ink', bg: 'bg-k-amber/10' },
            { label: '跳过', value: summary.skipped, color: 'text-k-ink3', bg: 'bg-k-ink4/10' },
            { label: '失败', value: summary.failed, color: 'text-k-hot', bg: 'bg-k-hot/10' },
          ].map(s => (
            <div key={s.label} className={`${s.bg} border border-k-line/50 rounded-xl p-4 text-center`}>
              <div className={`text-2xl font-bold ${s.color}`}>{s.value}</div>
              <div className="text-xs text-k-ink4 mt-1">{s.label}</div>
            </div>
          ))}
        </div>
      )}

      {/* File Results */}
      {fileResults.length > 0 && (
        <div className="bg-k-surface/50 border border-k-line/50 rounded-xl p-5">
          <h3 className="text-sm font-medium text-k-ink2 mb-3 flex items-center gap-2">
            <FileText className="w-4 h-4 text-k-accent" />
            文件处理结果 ({processedCount}/{totalFiles})
          </h3>
          <div className="space-y-1.5 max-h-[400px] overflow-y-auto">
            {fileResults.map((fr, i) => {
              const hasEntries = fr.entries && fr.entries.length > 0
              const isExpanded = expandedFiles.has(fr.index)
              return (
                <div key={i} className="rounded border border-k-line/30">
                  <div
                    className={`flex items-center justify-between py-1.5 px-2 text-xs ${hasEntries ? 'cursor-pointer hover:bg-k-sunk/30' : ''}`}
                    onClick={() => hasEntries && toggleFile(fr.index)}
                  >
                    <div className="flex items-center gap-2 min-w-0">
                      {hasEntries ? (
                        isExpanded
                          ? <ChevronDown className="w-3 h-3 text-k-ink4 shrink-0" />
                          : <ChevronRight className="w-3 h-3 text-k-ink4 shrink-0" />
                      ) : fr.error ? (
                        <XCircle className="w-3 h-3 text-k-hot shrink-0" />
                      ) : (
                        <CheckCircle className="w-3 h-3 text-k-ok shrink-0" />
                      )}
                      <span className={`truncate ${fr.error ? 'text-k-hot' : 'text-k-ink2'}`}>
                        {fr.file_name}
                      </span>
                    </div>
                    <div className="flex items-center gap-2 shrink-0 ml-2">
                      {(fr.retry_count ?? 0) > 0 && (
                        <span className="text-[10px] text-k-accent bg-k-accent/10 px-1.5 py-0.5 rounded">
                          重试 {fr.retry_count} 次
                        </span>
                      )}
                      {fr.error ? (
                        <span className="text-k-hot">失败</span>
                      ) : (
                        <>
                          {(fr.created ?? 0) > 0 && <span className="text-k-ok">+{fr.created}</span>}
                          {(fr.merged ?? 0) > 0 && <span className="text-k-amber-ink">~{fr.merged}</span>}
                          {(fr.skipped ?? 0) > 0 && <span className="text-k-ink4">-{fr.skipped}</span>}
                        </>
                      )}
                    </div>
                  </div>
                  {isExpanded && (
                    <div className="border-t border-k-line/30 bg-k-bg/40 px-3 py-2 space-y-2">
                      {(fr.attempts && fr.attempts.length > 0) && (
                        <div className="space-y-1">
                          {fr.attempts.map((attempt, idx) => (
                            <div key={idx} className="text-[11px] text-k-ink4 flex items-center gap-2">
                              <span className={attempt.success ? 'text-k-ok' : 'text-k-amber-ink'}>
                                #{attempt.attempt}
                              </span>
                              <span>{attempt.strategy}</span>
                              <span>{attempt.quality_check ? '质量评估开启' : '质量评估关闭'}</span>
                              {attempt.error && <span className="text-k-ink4 truncate">{attempt.error}</span>}
                            </div>
                          ))}
                        </div>
                      )}
                      {fr.entries && fr.entries.map((entry, j) => (
                        <div key={j} className="flex items-start gap-2 text-xs">
                          {entry.action === 'create' && <Plus className="w-3 h-3 text-k-ok shrink-0 mt-0.5" />}
                          {entry.action === 'merge' && <GitMerge className="w-3 h-3 text-k-amber-ink shrink-0 mt-0.5" />}
                          {entry.action === 'skip' && <SkipForward className="w-3 h-3 text-k-ink4 shrink-0 mt-0.5" />}
                          <div className="min-w-0">
                            <span
                              className={`cursor-pointer hover:underline ${
                                entry.action === 'create' ? 'text-k-ink' :
                                entry.action === 'merge' ? 'text-k-amber-ink' : 'text-k-ink4'
                              }`}
                              onClick={() => entry.id && onPreviewEntry?.(entry.id)}
                            >
                              {entry.title}
                            </span>
                            <div className="flex items-center gap-2 mt-0.5">
                              {entry.domain && (
                                <span className="text-[10px] text-k-ink4 bg-k-sunk/60 px-1 py-0.5 rounded">{entry.domain}</span>
                              )}
                              {entry.type && (
                                <span className="text-[10px] text-k-ink4 bg-k-sunk/60 px-1 py-0.5 rounded">{entry.type}</span>
                              )}
                              {entry.merge_target && (
                                <span className="text-[10px] text-k-amber-ink">&rarr; {entry.merge_target}</span>
                              )}
                            </div>
                          </div>
                        </div>
                      ))}
                      {(!fr.entries || fr.entries.length === 0) && fr.error && (
                        <div className="text-xs text-k-hot">{fr.error}</div>
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
        <h2 className="text-sm font-medium text-k-ink3 mb-3">课程知识条目</h2>
        <div className="flex items-center gap-3 mb-3">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-k-ink4" />
            <input
              type="text"
              placeholder="搜索条目..."
              value={entriesSearch}
              onChange={e => { setEntriesSearch(e.target.value); setEntriesPage(1) }}
              className="w-full pl-10 pr-4 py-2 bg-k-bg border border-k-line rounded-lg text-sm text-k-ink2 outline-none focus:border-k-accent placeholder:text-k-ink4"
            />
          </div>
          <select
            value={entriesFilter}
            onChange={e => { setEntriesFilter(e.target.value); setEntriesPage(1) }}
            className="bg-k-bg border border-k-line rounded-lg px-3 py-2 text-sm text-k-ink2 outline-none focus:border-k-accent"
          >
            <option value="">所有课件</option>
            {files.map(f => (
              <option key={f.course_file} value={f.course_file}>{f.file_stem}</option>
            ))}
          </select>
        </div>

        <div className="border border-k-line/80 rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-k-line/80 bg-k-surface/40">
                <th className="text-left px-4 py-3 text-k-ink4 font-medium">标题</th>
                <th className="text-left px-4 py-3 text-k-ink4 font-medium w-24">类型</th>
                <th className="text-left px-4 py-3 text-k-ink4 font-medium w-28">知识域</th>
                <th className="text-left px-4 py-3 text-k-ink4 font-medium w-28">课件</th>
                <th className="text-right px-4 py-3 text-k-ink4 font-medium w-20">置信度</th>
              </tr>
            </thead>
            <tbody>
              {entries.length === 0 ? (
                <tr>
                  <td colSpan={5} className="text-center py-12 text-k-ink4">
                    {!entriesLoaded ? '加载中...' : '暂无课程知识条目，点击「开始提取」处理课件'}
                  </td>
                </tr>
              ) : (
                entries.map(e => (
                  <tr
                    key={e.id}
                    className="border-b border-k-line/40 hover:bg-k-surface/40 cursor-pointer transition-colors"
                    onClick={() => onPreviewEntry?.(e.id)}
                  >
                    <td className="px-4 py-3 text-k-ink">{e.title}</td>
                    <td className="px-4 py-3">
                      <span className="px-1.5 py-0.5 text-xs bg-k-sunk text-k-ink3 rounded">{e.type}</span>
                    </td>
                    <td className="px-4 py-3 text-xs text-k-ink4 truncate max-w-[120px]">{e.domain || '—'}</td>
                    <td className="px-4 py-3 text-xs text-k-ink4 truncate max-w-[120px]">{e.course_file || '—'}</td>
                    <td className="px-4 py-3 text-right">
                      {e.confidence != null
                        ? <span className={`text-xs font-mono ${
                            Math.round(e.confidence * 100) >= 80 ? 'text-k-ok' :
                            Math.round(e.confidence * 100) >= 50 ? 'text-k-amber-ink' : 'text-k-hot'
                          }`}>{Math.round(e.confidence * 100)}%</span>
                        : <span className="text-k-ink4">—</span>
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
            <span className="text-k-ink4">共 {entriesTotal} 条目</span>
            <div className="flex items-center gap-2">
              <button disabled={entriesPage <= 1} onClick={() => setEntriesPage(p => p - 1)}
                className="p-1.5 rounded-lg bg-k-surface border border-k-line disabled:opacity-30 hover:bg-k-sunk transition-colors">
                <ChevronLeft className="w-4 h-4" />
              </button>
              <span className="text-k-ink3 px-2">{entriesPage} / {entriesTotalPages}</span>
              <button disabled={entriesPage >= entriesTotalPages} onClick={() => setEntriesPage(p => p + 1)}
                className="p-1.5 rounded-lg bg-k-surface border border-k-line disabled:opacity-30 hover:bg-k-sunk transition-colors">
                <ChevronRightIcon className="w-4 h-4" />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
