import type { ReactNode } from 'react'

interface Props {
  title: string
  desc: string
  count: number
  icon: ReactNode
}

export default function ArchitectureCard({ title, desc, count, icon }: Props) {
  return (
    <div className="p-5 border border-k-line/80 bg-k-surface/30 rounded-xl">
      <div className="mb-3">{icon}</div>
      <h3 className="text-k-ink font-medium mb-1">{title}</h3>
      <p className="text-xs text-k-ink4 mb-4 h-8">{desc}</p>
      <div className="flex items-center justify-between text-xs pt-3 border-t border-k-line">
        <span className="text-k-ink3">收录节点</span>
        <span className="font-mono text-k-ink2">{count}</span>
      </div>
    </div>
  )
}
