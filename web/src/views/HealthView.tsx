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
          <h1 className="text-2xl font-semibold text-k-ink mb-1">系统诊断与巡检</h1>
          <p className="text-k-ink4 text-sm">自动定位过时、薄弱节点及架构空白，维护图谱健康。</p>
        </div>
        <div className="flex gap-3">
          <button className="flex items-center gap-2 px-4 py-2 bg-k-surface border border-k-line hover:bg-k-sunk text-k-ink2 rounded-lg text-sm transition-colors">
            <FileText className="w-4 h-4" /> 生成报告 (Report)
          </button>
        </div>
      </div>

      <div className="bg-k-surface/40 border border-k-line/80 rounded-xl overflow-hidden">
        <div className="px-4 py-3 border-b border-k-line/80 bg-k-surface flex justify-between items-center">
          <span className="text-xs font-medium text-k-ink3">异常节点列表</span>
          <span className="text-[10px] font-mono text-k-ink4 bg-k-bg px-2 py-0.5 rounded">kg review</span>
        </div>
        {loading ? (
          <div className="p-8 text-center text-k-ink4 text-sm">加载中...</div>
        ) : items.length === 0 ? (
          <div className="p-8 text-center text-k-ok text-sm">所有节点健康，暂无异常。</div>
        ) : (
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-k-line/50 text-k-ink4">
                <th className="px-5 py-3 font-medium">缺陷类型</th>
                <th className="px-5 py-3 font-medium">目标节点</th>
                <th className="px-5 py-3 font-medium">置信度</th>
                <th className="px-5 py-3 font-medium">时间戳</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-k-line/50">
              {items.map(({ type, item }, i) => (
                <tr key={i} className="hover:bg-k-sunk/20 transition-colors">
                  <td className="px-5 py-3">
                    <span className={`inline-flex px-2 py-1 rounded text-[11px] font-medium border ${
                      type === '过时' ? 'bg-k-amber/10 text-k-amber-ink border-k-amber/20' :
                      type === '薄弱' ? 'bg-k-hot/10 text-k-hot border-k-hot/20' :
                      'bg-k-ink4/10 text-k-ink3 border-k-ink4/20'
                    }`}>
                      {type}
                    </span>
                  </td>
                  <td className="px-5 py-3 font-medium">
                    <button
                      onClick={() => onPreviewEntry?.(item.id)}
                      className="text-k-ink2 hover:text-k-accent transition-colors text-left"
                    >
                      {item.title}
                    </button>
                  </td>
                  <td className="px-5 py-3">
                    {item.confidence != null ? (
                      <div className="flex items-center gap-2 w-24">
                        <div className="flex-1 h-1.5 bg-k-sunk rounded-full overflow-hidden">
                          <div className={`h-full ${item.confidence > 0.6 ? 'bg-k-ok/80' : 'bg-k-hot/80'}`} style={{ width: `${item.confidence * 100}%` }} />
                        </div>
                        <span className="text-xs text-k-ink4">{item.confidence.toFixed(2)}</span>
                      </div>
                    ) : <span className="text-k-ink4">-</span>}
                  </td>
                  <td className="px-5 py-3 text-k-ink4">{item.last_updated || '无记录'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
