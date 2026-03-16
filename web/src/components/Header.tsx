import { Database, ChevronRight, RefreshCw, Upload, Download } from 'lucide-react'
import ActionButton from './ActionButton'

const TAB_LABELS: Record<string, string> = {
  dashboard: '概览仪表盘',
  ask: '智能问答与检索',
  quiz: '间隔重复测验',
  problems: '面试题库',
  exam: '模拟面试',
  health: '知识健康巡检',
  graph: '图谱与演进链路',
}

interface Props {
  activeTab: string
  isSyncing: boolean
  onSync: () => void
  onOpenIngest: () => void
  onOpenExport: () => void
}

export default function Header({ activeTab, isSyncing, onSync, onOpenIngest, onOpenExport }: Props) {
  return (
    <header className="h-14 border-b border-zinc-800/50 flex items-center justify-between px-6 bg-[#09090b]/80 backdrop-blur-md z-10">
      <div className="flex items-center gap-2 text-sm text-zinc-400">
        <Database className="w-4 h-4 text-zinc-500" />
        <span>Vault</span>
        <ChevronRight className="w-4 h-4 text-zinc-600" />
        <span className="text-zinc-100 font-medium">{TAB_LABELS[activeTab] ?? activeTab}</span>
      </div>

      <div className="flex items-center gap-3">
        <ActionButton
          icon={<RefreshCw className={`w-4 h-4 ${isSyncing ? 'animate-spin' : ''}`} />}
          label="同步索引"
          cmd="kg sync"
          onClick={onSync}
        />
        <ActionButton
          icon={<Upload className="w-4 h-4" />}
          label="导入知识"
          cmd="kg ingest"
          onClick={onOpenIngest}
        />
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
