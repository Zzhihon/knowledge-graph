import { useEffect, useRef, useState } from 'react'
import { X, ArrowUpRight } from 'lucide-react'
import Markdown from './Markdown'
import { BOARDS, STATUS, fetchFile, tone, useReader, type VaultFile } from '../../lib/library'

const list = (v: unknown): string[] => (Array.isArray(v) ? v.map(String) : v ? [String(v)] : [])
const linkId = (s: string) => s.replace(/^\[\[|\]\]$/g, '').split('|')[0].trim()

/** One entry as a reading page, sliding in over the current view. */
export default function ReaderSheet({ path, onClose }: { path: string; onClose: () => void }) {
  const { index, open } = useReader()
  const [file, setFile] = useState<VaultFile | null>(null)
  const [error, setError] = useState('')
  const scroller = useRef<HTMLDivElement>(null)

  useEffect(() => {
    setFile(null)
    setError('')
    fetchFile(path).then(setFile).catch((e) => setError(String(e.message ?? e)))
    scroller.current?.scrollTo({ top: 0 })
  }, [path])
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const meta = file?.meta ?? {}
  const board = BOARDS[path.split('/')[0]] ?? '笔记'
  const title = String(meta.title ?? path.split('/').pop()?.replace(/\.md$/, ''))
  const tags = list(meta.tags)
  const related = list(meta.related).map(linkId).filter((id) => index[id])
  // The body usually repeats the title as its first heading.
  const body = (file?.content ?? '').replace(/^\s*#\s+.+\n/, '')

  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true" aria-label={title}>
      <div className="absolute inset-0 bg-[color-mix(in_srgb,var(--k-ink)_22%,transparent)]" onClick={onClose} />
      <div ref={scroller} className="k-app relative h-full w-full max-w-[860px] overflow-y-auto border-l border-k-line bg-k-bg" style={{ boxShadow: 'var(--k-shadow-pop)' }}>
        <div className="sticky top-0 z-10 flex items-center gap-3 border-b border-k-line-soft bg-k-bg px-8 py-3 backdrop-blur">
          <span className="k-chip" style={tone(board)}>{board}</span>
          <span className="k-mono min-w-0 truncate text-[12.5px] text-k-ink4">{path}</span>
          <button onClick={onClose} className="ml-auto rounded-full p-1.5 text-k-ink3 hover:bg-k-sunk" aria-label="关闭"><X className="h-5 w-5" /></button>
        </div>

        <article className="mx-auto max-w-[720px] px-8 pb-20 pt-8">
          {error && <p className="text-k-hot">读不到：{error}</p>}
          {!file && !error && <p className="text-k-ink4">读取中…</p>}
          {file && (
            <>
              <h1 className="text-[28px] font-bold leading-[1.4] text-k-ink" style={{ textWrap: 'balance' }}>{title}</h1>
              <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-[13px] text-k-ink4">
                {meta.status ? <span className="k-chip plain" style={tone('项目')}>{STATUS[String(meta.status)] ?? String(meta.status)}</span> : null}
                {meta.updated ? <span>更新于 {String(meta.updated)}</span> : null}
                {meta.confidence != null && meta.confidence !== '' ? <span>可信度 {String(meta.confidence)}</span> : null}
                {tags.slice(0, 6).map((t) => <span key={t}>#{t}</span>)}
              </div>
              <div className="mt-7 border-t border-k-line-soft pt-6">
                <Markdown source={body} />
              </div>
              {related.length > 0 && (
                <section className="mt-10 border-t border-k-line pt-5">
                  <h2 className="mb-3 text-[14px] font-[650] text-k-ink3">相关条目</h2>
                  <div className="grid gap-2 sm:grid-cols-2">
                    {related.map((id) => (
                      <button key={id} onClick={() => open(index[id][1])} className="k-card k-card-hover px-4 py-3 text-left">
                        <span className="k-chip" style={tone(BOARDS[index[id][2]] ?? '')}>{BOARDS[index[id][2]] ?? '条目'}</span>
                        <div className="mt-1.5 text-[14.5px] font-semibold leading-[1.5] text-k-ink">{index[id][0]}</div>
                      </button>
                    ))}
                  </div>
                </section>
              )}
              <footer className="mt-10 flex flex-wrap items-center gap-3 border-t border-k-line-soft pt-4 text-[13px] text-k-ink4">
                <span className="k-mono">{String(meta.id ?? '')}</span>
                <a className="ml-auto inline-flex items-center gap-1 rounded-full border border-k-line px-3 py-1 text-k-ink3 hover:border-k-accent hover:text-k-accent"
                  href={`obsidian://open?vault=${encodeURIComponent(file.vault)}&file=${encodeURIComponent(file.path)}`}>
                  在 Obsidian 打开<ArrowUpRight className="h-3.5 w-3.5" />
                </a>
              </footer>
            </>
          )}
        </article>
      </div>
    </div>
  )
}
