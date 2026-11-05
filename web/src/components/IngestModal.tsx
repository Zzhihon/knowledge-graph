import { useState, useRef, useCallback } from 'react'
import { Upload, FileText, X, Trash2, CheckCircle, GitMerge, Ban, Link, Loader2, RotateCcw } from 'lucide-react'
import { postForm } from '../api/client'
import type { BatchIngestResult, IngestResult } from '../types'

type Tab = 'file' | 'url'
type StreamPhase = 'idle' | 'fetch' | 'extract' | 'quality' | 'complete'

interface StreamEntry {
  index: number
  total: number
  id: string
  title: string
  action: string
  directory: string
  novelty_score: number | null
  quality_score: number | null
  reason: string
}

interface URLStreamResult {
  url: string
  title: string
  content_length: number
  created: number
  merged: number
  skipped: number
  entries: StreamEntry[]
}

interface Props {
  onClose: () => void
  onIngestComplete?: (entryIds: string[]) => void
}

export default function IngestModal({ onClose, onIngestComplete }: Props) {
  const [activeTab, setActiveTab] = useState<Tab>('file')
  const [files, setFiles] = useState<File[]>([])
  const [urlInput, setUrlInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [progress, setProgress] = useState('')
  const [singleResult, setSingleResult] = useState<IngestResult | null>(null)
  const [batchResult, setBatchResult] = useState<BatchIngestResult | null>(null)
  const [error, setError] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  // SSE streaming state for URL ingest
  const [streamPhase, setStreamPhase] = useState<StreamPhase>('idle')
  const [streamEntries, setStreamEntries] = useState<StreamEntry[]>([])
  const [streamTotal, setStreamTotal] = useState(0)
  const [streamMeta, setStreamMeta] = useState<{ title: string; content_length: number } | null>(null)
  const [streamResult, setStreamResult] = useState<URLStreamResult | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    const dropped = Array.from(e.dataTransfer.files)
    if (dropped.length > 0) setFiles(prev => [...prev, ...dropped])
  }

  const removeFile = (index: number) => {
    setFiles(prev => prev.filter((_, i) => i !== index))
  }

  const handleFileIngest = async () => {
    if (files.length === 0) return
    setLoading(true)
    setError('')

    try {
      if (files.length === 1) {
        setProgress('正在解析文件...')
        const form = new FormData()
        form.append('file', files[0])
        const res = await postForm<IngestResult>('/ingest', form)
        setSingleResult(res)
        const ids = (res.entries || []).filter(e => e.action === 'create' && e.id).map(e => e.id)
        if (ids.length > 0) onIngestComplete?.(ids)
      } else {
        setProgress(`正在处理 ${files.length} 个文件...`)
        const form = new FormData()
        files.forEach(f => form.append('files', f))
        const res = await postForm<BatchIngestResult>('/ingest/batch', form)
        setBatchResult(res)
        const ids = (res.file_results || []).flatMap(fr => fr.entries).filter(e => e.action === 'create' && e.id).map(e => e.id)
        if (ids.length > 0) onIngestComplete?.(ids)
      }
    } catch {
      setError('处理失败，请重试。')
    } finally {
      setLoading(false)
    }
  }

  const handleSSEEvent = useCallback((event: string, data: Record<string, unknown>) => {
    switch (event) {
      case 'phase':
        setStreamPhase(data.phase as StreamPhase)
        setProgress(data.message as string)
        break
      case 'fetched':
        setStreamMeta({
          title: data.title as string,
          content_length: data.content_length as number,
        })
        break
      case 'extracted':
        setStreamTotal(data.total_entries as number)
        break
      case 'entry':
        setStreamEntries(prev => [...prev, data as unknown as StreamEntry])
        break
      case 'complete': {
        const result = data as unknown as URLStreamResult
        setStreamResult(result)
        setStreamPhase('complete')
        setLoading(false)
        // Trigger integration gateway for newly created entries
        const createdIds = (result.entries || [])
          .filter((e: StreamEntry) => e.action === 'create' && e.id)
          .map((e: StreamEntry) => e.id)
        if (createdIds.length > 0) onIngestComplete?.(createdIds)
        break
      }
      case 'error':
        setError(data.message as string)
        setStreamPhase('idle')
        setLoading(false)
        break
    }
  }, [])

  const handleURLIngest = async () => {
    if (!urlInput.trim()) return
    setLoading(true)
    setError('')
    setStreamPhase('idle')
    setStreamEntries([])
    setStreamTotal(0)
    setStreamMeta(null)
    setStreamResult(null)
    setProgress('正在连接...')

    const controller = new AbortController()
    abortRef.current = controller

    try {
      const res = await fetch('/api/ingest/url', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          url: urlInput.trim(),
          quality_check: true,
        }),
        signal: controller.signal,
      })

      if (!res.ok || !res.body) {
        setError(`请求失败: ${res.status}`)
        setLoading(false)
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
              handleSSEEvent(currentEvent, data)
            } catch { /* ignore parse errors */ }
          }
        }
      }

      // Stream ended without 'complete' event — could be a clean close
      if (streamPhase !== 'complete') {
        setLoading(false)
      }
    } catch (err: unknown) {
      if (err instanceof Error && err.name !== 'AbortError') {
        setError(`连接错误: ${err.message}`)
      }
      setLoading(false)
    }
  }

  const handleCancel = () => {
    abortRef.current?.abort()
    setStreamPhase('idle')
    setLoading(false)
  }

  const hasResult = singleResult || batchResult || streamResult

  const actionIcon = (action: string) => {
    switch (action) {
      case 'create': return <CheckCircle className="w-3.5 h-3.5 text-k-ok" />
      case 'merge': return <GitMerge className="w-3.5 h-3.5 text-k-amber-ink" />
      case 'skip': return <Ban className="w-3.5 h-3.5 text-k-ink4" />
      default: return <CheckCircle className="w-3.5 h-3.5 text-k-ok" />
    }
  }

  const actionColor = (action: string) => {
    switch (action) {
      case 'create': return 'border-k-ok/20 bg-k-ok-soft'
      case 'merge': return 'border-k-amber/20 bg-k-amber-soft'
      case 'skip': return 'border-k-line-strong/30 bg-k-surface/30 opacity-60'
      default: return 'border-k-line/50 bg-k-surface/50'
    }
  }

  const resetState = () => {
    setSingleResult(null)
    setBatchResult(null)
    setStreamResult(null)
    setStreamEntries([])
    setStreamTotal(0)
    setStreamMeta(null)
    setStreamPhase('idle')
    setError('')
    setProgress('')
  }

  const switchTab = (tab: Tab) => {
    if (hasResult) resetState()
    setActiveTab(tab)
  }

  // Determine entries to display in result view
  const resultEntries = streamResult
    ? streamResult.entries
    : singleResult?.entries ?? batchResult?.file_results?.flatMap(fr => fr.entries) ?? []

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-k-bg/60 backdrop-blur-sm">
      <div className="bg-k-bg border border-k-line rounded-2xl w-full max-w-2xl shadow-2xl max-h-[85vh] flex flex-col">
        <div className="flex items-center justify-between p-5 border-b border-k-line/80">
          <h2 className="text-lg font-medium text-k-ink flex items-center gap-2">
            <Upload className="w-5 h-5 text-k-ok" /> 知识摄取与提取
          </h2>
          <button onClick={onClose} className="text-k-ink4 hover:text-k-ink2"><X className="w-5 h-5" /></button>
        </div>

        {/* Tab switcher */}
        {!hasResult && streamPhase === 'idle' && (
          <div className="flex border-b border-k-line/80">
            <button
              onClick={() => switchTab('file')}
              className={`flex-1 py-2.5 text-sm font-medium transition-colors flex items-center justify-center gap-1.5 ${
                activeTab === 'file'
                  ? 'text-k-ok border-b-2 border-k-ok'
                  : 'text-k-ink4 hover:text-k-ink2'
              }`}
            >
              <FileText className="w-4 h-4" /> 上传文件
            </button>
            <button
              onClick={() => switchTab('url')}
              className={`flex-1 py-2.5 text-sm font-medium transition-colors flex items-center justify-center gap-1.5 ${
                activeTab === 'url'
                  ? 'text-k-ok border-b-2 border-k-ok'
                  : 'text-k-ink4 hover:text-k-ink2'
              }`}
            >
              <Link className="w-4 h-4" /> 链接导入
            </button>
          </div>
        )}

        <div className="flex-1 overflow-y-auto">
          {hasResult ? (
            <div className="p-6 space-y-4">
              {/* URL meta info */}
              {(streamResult || streamMeta) && (
                <div className="text-xs text-k-ink4 space-y-0.5">
                  <p className="truncate">标题: <span className="text-k-ink2">{streamResult?.title ?? streamMeta?.title}</span></p>
                  <p>内容长度: <span className="text-k-ink2">{(streamResult?.content_length ?? streamMeta?.content_length ?? 0).toLocaleString()} 字符</span></p>
                </div>
              )}

              {/* Summary counts */}
              {streamResult && (
                <div className="flex gap-3 text-sm">
                  <span className="text-k-ok">创建 {streamResult.created}</span>
                  <span className="text-k-amber-ink">合并 {streamResult.merged}</span>
                  <span className="text-k-ink4">跳过 {streamResult.skipped}</span>
                </div>
              )}
              {singleResult && !streamResult && (
                <div className="flex gap-3 text-sm">
                  <span className="text-k-ok">创建 {singleResult.created}</span>
                  <span className="text-k-amber-ink">合并 {singleResult.merged}</span>
                  <span className="text-k-ink4">跳过 {singleResult.skipped}</span>
                </div>
              )}
              {batchResult && (
                <div className="space-y-2">
                  <div className="flex gap-3 text-sm">
                    <span className="text-k-ink2">文件 {batchResult.processed}/{batchResult.total_files}</span>
                    <span className="text-k-ok">创建 {batchResult.entries_created}</span>
                    <span className="text-k-amber-ink">合并 {batchResult.entries_merged}</span>
                    <span className="text-k-ink4">跳过 {batchResult.entries_skipped}</span>
                  </div>
                  {batchResult.errors.length > 0 && (
                    <div className="text-xs text-k-hot">
                      {batchResult.errors.map((err, i) => (
                        <p key={i}>{err.file}: {err.error}</p>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {/* Entry list */}
              <div className="space-y-2">
                {resultEntries.map((e, i) => (
                  <div
                    key={i}
                    className={`flex items-center gap-3 p-3 border rounded-lg text-sm ${actionColor(e.action)}`}
                  >
                    {actionIcon(e.action)}
                    <span className="text-k-ink2 font-medium truncate">{e.title}</span>
                    {e.quality_score != null && (
                      <span className="text-[10px] font-mono text-k-ink4 ml-auto shrink-0">
                        Q:{e.quality_score.toFixed(2)} N:{e.novelty_score?.toFixed(2) ?? '-'}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          ) : streamPhase !== 'idle' ? (
            /* SSE streaming progress view */
            <div className="p-6 space-y-4">
              {/* Meta info once fetched */}
              {streamMeta && (
                <div className="text-xs text-k-ink4 space-y-0.5">
                  <p className="truncate">标题: <span className="text-k-ink2">{streamMeta.title}</span></p>
                  <p>内容长度: <span className="text-k-ink2">{streamMeta.content_length.toLocaleString()} 字符</span></p>
                </div>
              )}

              {/* Phase indicator */}
              <div className="flex items-center gap-2 text-sm text-k-ink3">
                <Loader2 className="w-4 h-4 animate-spin" />
                {progress}
              </div>

              {/* Progress bar during quality phase */}
              {streamPhase === 'quality' && streamTotal > 0 && (
                <div className="space-y-2">
                  <div className="flex justify-between text-xs text-k-ink4">
                    <span>条目处理进度</span>
                    <span>{streamEntries.length}/{streamTotal}</span>
                  </div>
                  <div className="h-1.5 bg-k-sunk rounded-full overflow-hidden">
                    <div
                      className="h-full bg-k-ok/60 rounded-full transition-all duration-300"
                      style={{ width: `${(streamEntries.length / streamTotal) * 100}%` }}
                    />
                  </div>
                </div>
              )}

              {/* Entries streaming in */}
              {streamEntries.length > 0 && (
                <div className="space-y-2">
                  {streamEntries.map((e, i) => (
                    <div
                      key={i}
                      className={`flex items-center gap-3 p-3 border rounded-lg text-sm ${actionColor(e.action)} animate-in fade-in duration-300`}
                    >
                      {actionIcon(e.action)}
                      <span className="text-k-ink2 font-medium truncate">{e.title}</span>
                      {e.quality_score != null && (
                        <span className="text-[10px] font-mono text-k-ink4 ml-auto shrink-0">
                          Q:{e.quality_score.toFixed(2)} N:{e.novelty_score?.toFixed(2) ?? '-'}
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              )}

              {error && (
                <div className="flex flex-col items-center gap-2">
                  <p className="text-sm text-k-hot">{error}</p>
                  <button
                    onClick={handleURLIngest}
                    className="flex items-center gap-1.5 text-xs text-k-ok hover:text-k-ok transition-colors"
                  >
                    <RotateCcw className="w-3 h-3" />
                    重试
                  </button>
                </div>
              )}
            </div>
          ) : (
            <div className="p-6 space-y-4">
              {activeTab === 'file' ? (
                <>
                  {/* Drop zone */}
                  <div
                    onClick={() => inputRef.current?.click()}
                    onDrop={handleDrop}
                    onDragOver={(e) => e.preventDefault()}
                    className="flex flex-col items-center justify-center border-2 border-dashed border-k-line rounded-xl p-8 bg-k-surface/30 hover:bg-k-surface/50 hover:border-k-line-strong transition-colors cursor-pointer"
                  >
                    <input
                      ref={inputRef}
                      type="file"
                      accept=".md,.txt,.pdf"
                      multiple
                      className="hidden"
                      onChange={(e) => {
                        if (e.target.files) setFiles(prev => [...prev, ...Array.from(e.target.files!)])
                      }}
                    />
                    <FileText className="w-10 h-10 text-k-ink4 mb-3" />
                    <p className="text-sm font-medium text-k-ink2">拖拽文件到此处，或点击上传</p>
                    <p className="text-xs text-k-ink4 mt-1">支持 .md / .txt / .pdf，可同时选择多个文件</p>
                  </div>

                  {/* File list */}
                  {files.length > 0 && (
                    <div className="space-y-2">
                      <p className="text-xs text-k-ink4 font-medium">待处理文件 ({files.length})</p>
                      {files.map((f, i) => (
                        <div key={i} className="flex items-center gap-3 p-2.5 bg-k-surface/50 border border-k-line/50 rounded-lg text-sm">
                          <FileText className="w-4 h-4 text-k-ink4 shrink-0" />
                          <span className="text-k-ink2 truncate">{f.name}</span>
                          <span className="text-k-ink4 text-xs ml-auto shrink-0">
                            {(f.size / 1024).toFixed(1)} KB
                          </span>
                          <button
                            onClick={(e) => { e.stopPropagation(); removeFile(i) }}
                            className="text-k-ink4 hover:text-k-hot shrink-0"
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                          </button>
                        </div>
                      ))}
                    </div>
                  )}
                </>
              ) : (
                <>
                  {/* URL input */}
                  <div className="space-y-3">
                    <p className="text-sm text-k-ink3">粘贴任意网页链接，自动抓取内容并提取知识条目。</p>
                    <input
                      type="url"
                      value={urlInput}
                      onChange={(e) => setUrlInput(e.target.value)}
                      onKeyDown={(e) => { if (e.key === 'Enter' && urlInput.trim()) handleURLIngest() }}
                      placeholder="https://mp.weixin.qq.com/s/..."
                      className="w-full px-4 py-3 bg-k-surface/50 border border-k-line rounded-xl text-sm text-k-ink placeholder-k-ink4 focus:outline-none focus:border-k-line-strong transition-colors"
                    />
                    <p className="text-xs text-k-ink4">支持微信公众号、博客文章、技术文档等任意公开网页</p>
                  </div>
                </>
              )}

              {loading && activeTab === 'file' && (
                <div className="flex items-center justify-center gap-2 text-sm text-k-ink3">
                  <Loader2 className="w-4 h-4 animate-spin" />
                  {progress}
                </div>
              )}

              {error && (
                <div className="flex flex-col items-center gap-2">
                  <p className="text-sm text-k-hot">{error}</p>
                  {activeTab === 'url' && (
                    <button
                      onClick={handleURLIngest}
                      disabled={!urlInput.trim()}
                      className="flex items-center gap-1.5 text-xs text-k-ok hover:text-k-ok disabled:opacity-40 transition-colors"
                    >
                      <RotateCcw className="w-3 h-3" />
                      重试
                    </button>
                  )}
                </div>
              )}
            </div>
          )}
        </div>

        <div className="p-5 border-t border-k-line/80 flex justify-between items-center bg-k-surface/30 rounded-b-2xl">
          <span className="text-xs font-mono text-k-ink4">
            {activeTab === 'file'
              ? `kg ingest ${files.length > 1 ? `[${files.length} files]` : files[0]?.name ?? '[filename]'}`
              : `kg ingest --url ${urlInput || '<url>'}`
            }
          </span>
          <div className="flex gap-3">
            {streamPhase !== 'idle' && streamPhase !== 'complete' ? (
              <button onClick={handleCancel} className="px-4 py-2 text-sm text-k-hot hover:text-k-hot">
                取消
              </button>
            ) : (
              <button onClick={onClose} className="px-4 py-2 text-sm text-k-ink3 hover:text-k-ink">
                {hasResult ? '关闭' : '取消'}
              </button>
            )}
            {!hasResult && streamPhase === 'idle' && (
              <button
                onClick={activeTab === 'file' ? handleFileIngest : handleURLIngest}
                disabled={(activeTab === 'file' ? files.length === 0 : !urlInput.trim()) || loading}
                className="px-4 py-2 bg-k-ok/20 text-k-ok hover:bg-k-ok/30 disabled:opacity-50 rounded-lg text-sm font-medium transition-colors"
              >
                {loading ? '解析中...' : '开始解析'}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
