import type { ReactNode } from 'react'

interface Props {
  title: string
  value: string | number
  subValue: string
  icon: ReactNode
  alert?: boolean
}

export default function StatCard({ title, value, subValue, icon, alert }: Props) {
  return (
    <div className="p-5 border border-k-line/80 bg-k-surface/30 rounded-xl">
      <div className="flex justify-between items-start mb-2">
        <div className="text-sm font-medium text-k-ink3">{title}</div>
        <div className="text-k-ink4">{icon}</div>
      </div>
      <div className={`text-3xl font-semibold mb-1 ${alert ? 'text-k-amber-ink' : 'text-k-ink'}`}>{value}</div>
      <div className="text-xs text-k-ink4">{subValue}</div>
    </div>
  )
}
