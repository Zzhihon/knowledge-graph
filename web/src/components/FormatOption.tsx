interface Props {
  title: string
  desc: string
  selected: boolean
  onClick: () => void
}

export default function FormatOption({ title, desc, selected, onClick }: Props) {
  return (
    <div
      onClick={onClick}
      className={`cursor-pointer border p-4 rounded-xl flex gap-3 transition-all ${
        selected ? 'bg-indigo-500/10 border-indigo-500/50' : 'bg-zinc-900 border-zinc-800 hover:border-zinc-700'
      }`}
    >
      <div className={`mt-0.5 w-4 h-4 rounded-full border flex items-center justify-center shrink-0 ${
        selected ? 'border-indigo-400' : 'border-zinc-600'
      }`}>
        {selected && <div className="w-2 h-2 bg-indigo-400 rounded-full"></div>}
      </div>
      <div>
        <div className={`font-medium text-sm mb-1 ${selected ? 'text-indigo-300' : 'text-zinc-300'}`}>{title}</div>
        <div className="text-xs text-zinc-500">{desc}</div>
      </div>
    </div>
  )
}
