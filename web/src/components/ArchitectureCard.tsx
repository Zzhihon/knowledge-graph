import type { ReactNode } from 'react'

interface Props {
  title: string
  desc: string
  count: number
  icon: ReactNode
}

export default function ArchitectureCard({ title, desc, count, icon }: Props) {
  return (
    <div className="p-5 border border-zinc-800/80 bg-zinc-900/30 rounded-xl">
      <div className="mb-3">{icon}</div>
      <h3 className="text-zinc-100 font-medium mb-1">{title}</h3>
      <p className="text-xs text-zinc-500 mb-4 h-8">{desc}</p>
      <div className="flex items-center justify-between text-xs pt-3 border-t border-zinc-800">
        <span className="text-zinc-400">收录节点</span>
        <span className="font-mono text-zinc-300">{count}</span>
      </div>
    </div>
  )
}
