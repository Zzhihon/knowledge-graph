import type { ReactNode } from 'react'
import { Database, ChevronRight, RefreshCw, Upload, Download, Workflow, Search } from 'lucide-react'
import ActionButton from './ActionButton'

const TAB_LABELS: Record<string, string> = {
  dashboard: '概览仪表盘',
  ask: '智能问答与检索',
  quiz: '间隔重复测验',
  problems: '面试题库',
  exam: '模拟面试',
  course: '课程知识',
  health: '知识健康巡检',
  graph: '图谱与演进链路',
  network: '知识网络图谱',
  topics: '知识主题总览',
}

interface Props {
  activeTab: string
  isSyncing: boolean
  onSync: () => void
  onOpenIngest: () => void
  onOpenExport: () => void
  onOpenIntegrate?: () => void
  onOpenSearch?: () => void
  /** Shown first on the right, e.g. the 异常 badge. */
  extra?: ReactNode
}

export default function Header({ activeTab, isSyncing, onSync, onOpenIngest, onOpenExport, onOpenIntegrate, onOpenSearch, extra }: Props) {
  return (
    <header className="h-14 border-b border-k-line/50 flex items-center justify-between px-6 bg-k-bg/80 backdrop-blur-md z-10">
      <div className="flex items-center gap-2 text-sm text-k-ink3">
        <Database className="w-4 h-4 text-k-ink4" />
        <span>Vault</span>
        <ChevronRight className="w-4 h-4 text-k-ink4" />
        <span className="text-k-ink font-medium">{TAB_LABELS[activeTab] ?? activeTab}</span>
      </div>

      <div className="flex items-center gap-3">
        {extra}
        {onOpenSearch && (
          <button
            onClick={onOpenSearch}
            className="flex items-center gap-2 px-3 py-1.5 rounded-lg border border-k-line bg-k-surface/50 text-k-ink4 hover:text-k-ink2 hover:border-k-line-strong transition-colors text-sm cursor-pointer"
          >
            <Search className="w-3.5 h-3.5" />
            <span className="hidden sm:inline text-xs">搜索知识库...</span>
            <kbd className="hidden sm:inline-flex items-center px-1.5 py-0.5 rounded bg-k-sunk border border-k-line-strong text-[10px] text-k-ink4 font-mono ml-1">
              ⌘K
            </kbd>
          </button>
        )}
        <ActionButton
          icon={<RefreshCw className={`w-4 h-4 ${isSyncing ? 'animate-spin' : ''}`} />}
          label={isSyncing ? '同步中...' : '同步索引'}
          cmd="kg sync"
          onClick={onSync}
          disabled={isSyncing}
        />
        <ActionButton
          icon={<Upload className="w-4 h-4" />}
          label="导入知识"
          cmd="kg ingest"
          onClick={onOpenIngest}
        />
        {onOpenIntegrate && (
          <ActionButton
            icon={<Workflow className="w-4 h-4" />}
            label="整合网关"
            cmd="kg integrate"
            onClick={onOpenIntegrate}
          />
        )}
        <ActionButton
          icon={<Download className="w-4 h-4" />}
          label="导出文档"
          cmd="kg export"
          onClick={onOpenExport}
          primary
        />
      </div>
    </header>
  )
}
