import { useEffect, useState } from 'react'
import { FileText } from 'lucide-react'
import { get } from '../api/client'
import type { HealthReview, HealthItem } from '../types'

type IssueType = '过时' | '薄弱' | '草稿'

function classifyItem(_item: HealthItem, category: string): IssueType {
  if (category === 'outdated') return '过时'
  if (category === 'low_confidence') return '薄弱'
  return '草稿'
}

interface Props {
  onPreviewEntry?: (entryId: string) => void
}

export default function HealthView({ onPreviewEntry }: Props) {
  const [items, setItems] = useState<{ type: IssueType; item: HealthItem }[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    get<HealthReview>('/health/review')
      .then((data) => {
        const all: { type: IssueType; item: HealthItem }[] = []
        for (const [category, entries] of Object.entries(data)) {
          for (const item of entries as HealthItem[]) {
            all.push({ type: classifyItem(item, category), item })
          }
        }
        setItems(all)
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  return (
    <div>
      <div className="flex items-end justify-between mb-6">
        <div>
          <h1 className="text-2xl font-semibold text-zinc-100 mb-1">系统诊断与巡检</h1>
          <p className="text-zinc-500 text-sm">自动定位过时、薄弱节点及架构空白，维护图谱健康。</p>
        </div>
        <div className="flex gap-3">
          <button className="flex items-center gap-2 px-4 py-2 bg-zinc-900 border border-zinc-800 hover:bg-zinc-800 text-zinc-300 rounded-lg text-sm transition-colors">
            <FileText className="w-4 h-4" /> 生成报告 (Report)
          </button>
        </div>
      </div>

      <div className="bg-zinc-900/40 border border-zinc-800/80 rounded-xl overflow-hidden">
        <div className="px-4 py-3 border-b border-zinc-800/80 bg-zinc-900 flex justify-between items-center">
          <span className="text-xs font-medium text-zinc-400">异常节点列表</span>
          <span className="text-[10px] font-mono text-zinc-600 bg-zinc-950 px-2 py-0.5 rounded">kg review</span>
        </div>
        {loading ? (
          <div className="p-8 text-center text-zinc-500 text-sm">加载中...</div>
        ) : items.length === 0 ? (
          <div className="p-8 text-center text-emerald-400 text-sm">所有节点健康，暂无异常。</div>
        ) : (
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-zinc-800/50 text-zinc-500">
                <th className="px-5 py-3 font-medium">缺陷类型</th>
                <th className="px-5 py-3 font-medium">目标节点</th>
                <th className="px-5 py-3 font-medium">置信度</th>
                <th className="px-5 py-3 font-medium">时间戳</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800/50">
              {items.map(({ type, item }, i) => (
                <tr key={i} className="hover:bg-zinc-800/20 transition-colors">
                  <td className="px-5 py-3">
                    <span className={`inline-flex px-2 py-1 rounded text-[11px] font-medium border ${
                      type === '过时' ? 'bg-amber-500/10 text-amber-500 border-amber-500/20' :
                      type === '薄弱' ? 'bg-rose-500/10 text-rose-400 border-rose-500/20' :
                      'bg-zinc-500/10 text-zinc-400 border-zinc-500/20'
                    }`}>
                      {type}
                    </span>
                  </td>
                  <td className="px-5 py-3 font-medium">
                    <button
                      onClick={() => onPreviewEntry?.(item.id)}
                      className="text-zinc-300 hover:text-indigo-400 transition-colors text-left"
                    >
                      {item.title}
                    </button>
                  </td>
                  <td className="px-5 py-3">
                    {item.confidence != null ? (
                      <div className="flex items-center gap-2 w-24">
                        <div className="flex-1 h-1.5 bg-zinc-800 rounded-full overflow-hidden">
                          <div className={`h-full ${item.confidence > 0.6 ? 'bg-emerald-500/80' : 'bg-rose-500/80'}`} style={{ width: `${item.confidence * 100}%` }} />
                        </div>
                        <span className="text-xs text-zinc-500">{item.confidence.toFixed(2)}</span>
                      </div>
                    ) : <span className="text-zinc-600">-</span>}
                  </td>
                  <td className="px-5 py-3 text-zinc-500">{item.last_updated || '无记录'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
