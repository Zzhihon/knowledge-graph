import { useState } from 'react'
import { Download, X } from 'lucide-react'
import FormatOption from './FormatOption'
import { post } from '../api/client'
import type { ExportResult } from '../types'

interface Props {
  onClose: () => void
}

export default function ExportModal({ onClose }: Props) {
  const [format, setFormat] = useState('guide')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<ExportResult | null>(null)

  const handleExport = async () => {
    setLoading(true)
    try {
      const res = await post<ExportResult>('/export', { format })
      setResult(res)
    } catch {
      // Error handling
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm">
      <div className="bg-zinc-950 border border-zinc-800 rounded-2xl w-full max-w-lg shadow-2xl">
        <div className="flex items-center justify-between p-5 border-b border-zinc-800/80">
          <h2 className="text-lg font-medium text-zinc-100 flex items-center gap-2">
            <Download className="w-5 h-5 text-indigo-400" /> 多格式数据导出
          </h2>
          <button onClick={onClose} className="text-zinc-500 hover:text-zinc-300"><X className="w-5 h-5" /></button>
        </div>
        <div className="p-6 space-y-6">
          {result ? (
            <div className="space-y-3">
              <p className="text-emerald-400 text-sm font-medium">导出成功</p>
              <p className="text-zinc-500 text-xs">文件路径: {result.file_path}</p>
              <pre className="bg-zinc-900 border border-zinc-800 rounded-lg p-4 text-xs text-zinc-300 max-h-64 overflow-auto whitespace-pre-wrap">
                {result.content.slice(0, 2000)}
                {result.content.length > 2000 && '\n...'}
              </pre>
            </div>
          ) : (
            <div className="space-y-3">
              <FormatOption title="个人学习指南 (Study Guide)" desc="按前置知识排序，包含练习题和你的薄弱点提示" selected={format === 'guide'} onClick={() => setFormat('guide')} />
              <FormatOption title="技术博客 (Blog Post)" desc="叙事流排版：提出问题 -> 原理分析 -> 关键洞察" selected={format === 'blog'} onClick={() => setFormat('blog')} />
              <FormatOption title="新人入职文档 (Onboarding Doc)" desc="输出团队上下文、核心规范与常见避坑 Checklist" selected={format === 'onboarding'} onClick={() => setFormat('onboarding')} />
            </div>
          )}
        </div>
        <div className="p-5 border-t border-zinc-800/80 flex justify-between items-center bg-zinc-900/30 rounded-b-2xl">
          <span className="text-xs font-mono text-zinc-500">kg export --format {format}</span>
          <div className="flex gap-3">
            <button onClick={onClose} className="px-4 py-2 text-sm text-zinc-400 hover:text-zinc-200">
              {result ? '关闭' : '取消'}
            </button>
            {!result && (
              <button
                onClick={handleExport}
                disabled={loading}
                className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white rounded-lg text-sm font-medium transition-colors"
              >
                {loading ? '导出中...' : '执行导出'}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
