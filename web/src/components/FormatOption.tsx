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
        selected ? 'bg-k-accent/10 border-k-accent/50' : 'bg-k-surface border-k-line hover:border-k-line-strong'
      }`}
    >
      <div className={`mt-0.5 w-4 h-4 rounded-full border flex items-center justify-center shrink-0 ${
        selected ? 'border-k-accent' : 'border-k-line-strong'
      }`}>
        {selected && <div className="w-2 h-2 bg-k-accent rounded-full"></div>}
      </div>
      <div>
        <div className={`font-medium text-sm mb-1 ${selected ? 'text-k-accent' : 'text-k-ink2'}`}>{title}</div>
        <div className="text-xs text-k-ink4">{desc}</div>
      </div>
    </div>
  )
}
