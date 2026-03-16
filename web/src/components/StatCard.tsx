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
    <div className="p-5 border border-zinc-800/80 bg-zinc-900/30 rounded-xl">
      <div className="flex justify-between items-start mb-2">
        <div className="text-sm font-medium text-zinc-400">{title}</div>
        <div className="text-zinc-500">{icon}</div>
      </div>
      <div className={`text-3xl font-semibold mb-1 ${alert ? 'text-amber-400' : 'text-zinc-100'}`}>{value}</div>
      <div className="text-xs text-zinc-500">{subValue}</div>
    </div>
  )
}
