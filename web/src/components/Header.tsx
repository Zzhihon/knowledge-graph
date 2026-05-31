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
}

export default function Header({ activeTab, isSyncing, onSync, onOpenIngest, onOpenExport, onOpenIntegrate, onOpenSearch }: Props) {
  return (
    <header className="h-14 border-b border-zinc-800/50 flex items-center justify-between px-6 bg-[#09090b]/80 backdrop-blur-md z-10">
      <div className="flex items-center gap-2 text-sm text-zinc-400">
        <Database className="w-4 h-4 text-zinc-500" />
        <span>Vault</span>
        <ChevronRight className="w-4 h-4 text-zinc-600" />
        <span className="text-zinc-100 font-medium">{TAB_LABELS[activeTab] ?? activeTab}</span>
      </div>

      <div className="flex items-center gap-3">
        {onOpenSearch && (
          <button
            onClick={onOpenSearch}
            className="flex items-center gap-2 px-3 py-1.5 rounded-lg border border-zinc-800 bg-zinc-900/50 text-zinc-500 hover:text-zinc-300 hover:border-zinc-700 transition-colors text-sm cursor-pointer"
          >
            <Search className="w-3.5 h-3.5" />
            <span className="hidden sm:inline text-xs">搜索知识库...</span>
            <kbd className="hidden sm:inline-flex items-center px-1.5 py-0.5 rounded bg-zinc-800 border border-zinc-700 text-[10px] text-zinc-600 font-mono ml-1">
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
