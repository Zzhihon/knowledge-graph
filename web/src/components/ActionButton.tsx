import type { ReactNode } from 'react'

interface Props {
  icon: ReactNode
  label: string
  cmd: string
  onClick: () => void
  primary?: boolean
}

export default function ActionButton({ icon, label, cmd, onClick, primary }: Props) {
  return (
    <button
      onClick={onClick}
      className={`group flex items-center gap-2 px-3 py-1.5 text-sm rounded-lg transition-all ${
        primary
          ? 'bg-zinc-100 text-zinc-900 hover:bg-white'
          : 'border border-zinc-700 hover:border-zinc-500 text-zinc-300 hover:bg-zinc-800'
      }`}
    >
      {icon}
      <span className="font-medium">{label}</span>
      <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded ml-1 opacity-0 group-hover:opacity-100 transition-opacity hidden sm:block ${
        primary ? 'bg-zinc-200 text-zinc-600' : 'bg-zinc-800 text-zinc-400'
      }`}>
        {cmd}
      </span>
    </button>
  )
}
