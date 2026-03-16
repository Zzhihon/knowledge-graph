import { useState, useRef } from 'react'
import { Upload, FileText, X, Trash2, CheckCircle, GitMerge, Ban } from 'lucide-react'
import { postForm } from '../api/client'
import type { BatchIngestResult, IngestResult } from '../types'

interface Props {
  onClose: () => void
}

export default function IngestModal({ onClose }: Props) {
  const [files, setFiles] = useState<File[]>([])
  const [loading, setLoading] = useState(false)
  const [progress, setProgress] = useState('')
  const [singleResult, setSingleResult] = useState<IngestResult | null>(null)
  const [batchResult, setBatchResult] = useState<BatchIngestResult | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    const dropped = Array.from(e.dataTransfer.files)
    if (dropped.length > 0) setFiles(prev => [...prev, ...dropped])
  }

  const removeFile = (index: number) => {
    setFiles(prev => prev.filter((_, i) => i !== index))
  }

  const handleIngest = async () => {
    if (files.length === 0) return
    setLoading(true)

    try {
      if (files.length === 1) {
        setProgress('正在解析文件...')
        const form = new FormData()
        form.append('file', files[0])
        const res = await postForm<IngestResult>('/ingest', form)
        setSingleResult(res)
      } else {
        setProgress(`正在处理 ${files.length} 个文件...`)
        const form = new FormData()
        files.forEach(f => form.append('files', f))
        const res = await postForm<BatchIngestResult>('/ingest/batch', form)
        setBatchResult(res)
      }
    } catch {
      setProgress('处理失败，请重试。')
    } finally {
      setLoading(false)
    }
  }

  const hasResult = singleResult || batchResult

  const actionIcon = (action: string) => {
    switch (action) {
      case 'create': return <CheckCircle className="w-3.5 h-3.5 text-emerald-400" />
      case 'merge': return <GitMerge className="w-3.5 h-3.5 text-amber-400" />
      case 'skip': return <Ban className="w-3.5 h-3.5 text-zinc-500" />
      default: return <CheckCircle className="w-3.5 h-3.5 text-emerald-400" />
    }
  }

  const actionColor = (action: string) => {
    switch (action) {
      case 'create': return 'border-emerald-500/20 bg-emerald-950/20'
      case 'merge': return 'border-amber-500/20 bg-amber-950/20'
      case 'skip': return 'border-zinc-700/30 bg-zinc-900/30 opacity-60'
      default: return 'border-zinc-800/50 bg-zinc-900/50'
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm">
      <div className="bg-zinc-950 border border-zinc-800 rounded-2xl w-full max-w-2xl shadow-2xl max-h-[85vh] flex flex-col">
        <div className="flex items-center justify-between p-5 border-b border-zinc-800/80">
          <h2 className="text-lg font-medium text-zinc-100 flex items-center gap-2">
            <Upload className="w-5 h-5 text-emerald-400" /> 知识摄取与提取
          </h2>
          <button onClick={onClose} className="text-zinc-500 hover:text-zinc-300"><X className="w-5 h-5" /></button>
        </div>

        <div className="flex-1 overflow-y-auto">
          {hasResult ? (
            <div className="p-6 space-y-4">
              {/* Summary counts */}
              {singleResult && (
                <div className="flex gap-3 text-sm">
                  <span className="text-emerald-400">创建 {singleResult.created}</span>
                  <span className="text-amber-400">合并 {singleResult.merged}</span>
                  <span className="text-zinc-500">跳过 {singleResult.skipped}</span>
                </div>
              )}
              {batchResult && (
                <div className="space-y-2">
                  <div className="flex gap-3 text-sm">
                    <span className="text-zinc-300">文件 {batchResult.processed}/{batchResult.total_files}</span>
                    <span className="text-emerald-400">创建 {batchResult.entries_created}</span>
                    <span className="text-amber-400">合并 {batchResult.entries_merged}</span>
                    <span className="text-zinc-500">跳过 {batchResult.entries_skipped}</span>
                  </div>
                  {batchResult.errors.length > 0 && (
                    <div className="text-xs text-rose-400">
                      {batchResult.errors.map((err, i) => (
                        <p key={i}>{err.file}: {err.error}</p>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {/* Entry list */}
              <div className="space-y-2">
                {(singleResult?.entries ?? batchResult?.file_results?.flatMap(fr => fr.entries) ?? []).map((e, i) => (
                  <div
                    key={i}
                    className={`flex items-center gap-3 p-3 border rounded-lg text-sm ${actionColor(e.action)}`}
                  >
                    {actionIcon(e.action)}
                    <span className="text-zinc-300 font-medium truncate">{e.title}</span>
                    {e.quality_score != null && (
                      <span className="text-[10px] font-mono text-zinc-500 ml-auto shrink-0">
                        Q:{e.quality_score.toFixed(2)} N:{e.novelty_score?.toFixed(2) ?? '-'}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div className="p-6 space-y-4">
              {/* Drop zone */}
              <div
                onClick={() => inputRef.current?.click()}
                onDrop={handleDrop}
                onDragOver={(e) => e.preventDefault()}
                className="flex flex-col items-center justify-center border-2 border-dashed border-zinc-800 rounded-xl p-8 bg-zinc-900/30 hover:bg-zinc-900/50 hover:border-zinc-700 transition-colors cursor-pointer"
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
                <FileText className="w-10 h-10 text-zinc-600 mb-3" />
                <p className="text-sm font-medium text-zinc-300">拖拽文件到此处，或点击上传</p>
                <p className="text-xs text-zinc-500 mt-1">支持 .md / .txt / .pdf，可同时选择多个文件</p>
              </div>

              {/* File list */}
              {files.length > 0 && (
                <div className="space-y-2">
                  <p className="text-xs text-zinc-500 font-medium">待处理文件 ({files.length})</p>
                  {files.map((f, i) => (
                    <div key={i} className="flex items-center gap-3 p-2.5 bg-zinc-900/50 border border-zinc-800/50 rounded-lg text-sm">
                      <FileText className="w-4 h-4 text-zinc-500 shrink-0" />
                      <span className="text-zinc-300 truncate">{f.name}</span>
                      <span className="text-zinc-600 text-xs ml-auto shrink-0">
                        {(f.size / 1024).toFixed(1)} KB
                      </span>
                      <button
                        onClick={(e) => { e.stopPropagation(); removeFile(i) }}
                        className="text-zinc-600 hover:text-rose-400 shrink-0"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  ))}
                </div>
              )}

              {loading && (
                <p className="text-sm text-zinc-400 text-center">{progress}</p>
              )}
            </div>
          )}
        </div>

        <div className="p-5 border-t border-zinc-800/80 flex justify-between items-center bg-zinc-900/30 rounded-b-2xl">
          <span className="text-xs font-mono text-zinc-500">
            kg ingest {files.length > 1 ? `[${files.length} files]` : files[0]?.name ?? '[filename]'}
          </span>
          <div className="flex gap-3">
            <button onClick={onClose} className="px-4 py-2 text-sm text-zinc-400 hover:text-zinc-200">
              {hasResult ? '关闭' : '取消'}
            </button>
            {!hasResult && (
              <button
                onClick={handleIngest}
                disabled={files.length === 0 || loading}
                className="px-4 py-2 bg-emerald-600/20 text-emerald-400 hover:bg-emerald-600/30 disabled:opacity-50 rounded-lg text-sm font-medium transition-colors"
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
