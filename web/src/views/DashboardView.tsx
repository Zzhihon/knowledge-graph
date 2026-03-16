import { useEffect, useState } from 'react'
import { FileText, Clock, Activity, LayoutDashboard, BookOpen, Brain, Code2, Search, Play } from 'lucide-react'
import StatCard from '../components/StatCard'
import ArchitectureCard from '../components/ArchitectureCard'
import { get } from '../api/client'
import type { Stats } from '../types'

interface Props {
  setActiveTab: (tab: string) => void
}

export default function DashboardView({ setActiveTab }: Props) {
  const [stats, setStats] = useState<Stats | null>(null)

  useEffect(() => {
    get<Stats>('/stats').then(setStats).catch(() => {})
  }, [])

  const s = stats

  return (
    <div className="space-y-10 pb-10">
      <div>
        <h1 className="text-2xl font-semibold text-zinc-100 mb-2">欢迎回来，开始构建体系</h1>
        <p className="text-zinc-500 text-sm">Markdown 为源，AI 为引擎，间隔重复为节奏。</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <StatCard title="活跃知识节点" value={s?.total_items ?? '...'} subValue="基于 Obsidian" icon={<FileText className="w-5 h-5" />} />
        <StatCard title="待复习队列" value={s?.needs_review ?? '...'} subValue="今日需处理" icon={<Clock className="w-5 h-5" />} alert />
        <StatCard title="全局置信度" value={s ? `${(s.avg_confidence * 100).toFixed(0)}%` : '...'} subValue="SM-2 算法评估" icon={<Activity className="w-5 h-5" />} />
        <StatCard title="覆盖知识域" value={s?.domains.length ?? '...'} subValue={s?.domains.slice(0, 3).join('、') ?? ''} icon={<LayoutDashboard className="w-5 h-5" />} />
      </div>

      <div className="space-y-4">
        <h2 className="text-sm font-medium text-zinc-400">三层知识架构概览</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <ArchitectureCard title="01 原理层 (Principles)" desc="回答「为什么」。核心概念、底层机制与推导过程。" count={s?.layer_counts.principles ?? 0} icon={<BookOpen className="text-indigo-400" />} />
          <ArchitectureCard title="02 模式层 (Patterns)" desc="回答「怎么做」。最佳实践、代码模板与通用范式。" count={s?.layer_counts.patterns ?? 0} icon={<Brain className="text-emerald-400" />} />
          <ArchitectureCard title="08 实战层 (Problems)" desc="回答「用起来」。LeetCode 题解、Bug 复盘与真实案例。" count={s?.layer_counts.problems ?? 0} icon={<Code2 className="text-amber-400" />} />
        </div>
      </div>

      <div className="space-y-4">
        <h2 className="text-sm font-medium text-zinc-400">快速开始</h2>
        <div className="grid grid-cols-2 gap-4">
          <div
            onClick={() => setActiveTab('ask')}
            className="p-5 border border-zinc-800/80 bg-zinc-900/30 rounded-xl hover:border-indigo-500/50 hover:bg-indigo-500/5 cursor-pointer transition-all flex items-start gap-4"
          >
            <div className="p-2.5 bg-zinc-800/50 rounded-lg text-indigo-400"><Search className="w-5 h-5" /></div>
            <div>
              <h3 className="text-zinc-200 font-medium mb-1">查阅与问答</h3>
              <p className="text-zinc-500 text-sm mb-2">基于 Qdrant + 图谱的 RAG 增强检索</p>
              <span className="text-xs text-zinc-600 font-mono bg-zinc-900 px-1.5 py-0.5 rounded">kg ask / query</span>
            </div>
          </div>
          <div
            onClick={() => setActiveTab('quiz')}
            className="p-5 border border-zinc-800/80 bg-zinc-900/30 rounded-xl hover:border-emerald-500/50 hover:bg-emerald-500/5 cursor-pointer transition-all flex items-start gap-4"
          >
            <div className="p-2.5 bg-zinc-800/50 rounded-lg text-emerald-400"><Play className="w-5 h-5" /></div>
            <div>
              <h3 className="text-zinc-200 font-medium mb-1">开始今日测验</h3>
              <p className="text-zinc-500 text-sm mb-2">清空复习队列，对抗遗忘曲线</p>
              <span className="text-xs text-zinc-600 font-mono bg-zinc-900 px-1.5 py-0.5 rounded">kg quiz -i</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
