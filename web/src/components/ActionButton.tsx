import type { ReactNode } from 'react'

interface Props {
  icon: ReactNode
  label: string
  cmd: string
  onClick: () => void
  primary?: boolean
  disabled?: boolean
}

export default function ActionButton({ icon, label, cmd, onClick, primary, disabled }: Props) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`group flex items-center gap-2 px-3 py-1.5 text-sm rounded-lg transition-all ${
        disabled ? 'opacity-50 cursor-not-allowed' :
        primary
          ? 'bg-k-ink text-k-surface hover:bg-k-surface'
          : 'border border-k-line-strong hover:border-k-ink4 text-k-ink2 hover:bg-k-sunk'
      }`}
    >
      {icon}
      <span className="font-medium">{label}</span>
      <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded ml-1 opacity-0 group-hover:opacity-100 transition-opacity hidden sm:block ${
        primary ? 'bg-k-ink text-k-ink4' : 'bg-k-sunk text-k-ink3'
      }`}>
        {cmd}
      </span>
    </button>
  )
}
