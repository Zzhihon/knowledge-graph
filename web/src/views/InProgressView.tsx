// 进行中: what is being pushed forward. Top: sessions waiting for an answer and stalled projects;
// then one card per project (sessions, last conclusion, git, PRs, Orca, knowledge-base state);
// a card opens the project's session timeline, where every session can be resumed by command.
import { useCallback, useEffect, useState } from 'react'
import { ArrowUpRight, Check, Copy, GitBranch, TerminalSquare } from 'lucide-react'
import { plane, type PlaneProject, type PlaneSession, type ProjectsBody, type Stage } from '../api/plane'
import { useReader } from '../lib/library'
import { Drawer, KV, Problem, Section, btn } from '../components/k/HubBits'
import { Empty } from './DailyView'

const REFRESH_MS = 30_000

const STAGE_TONE: Record<Stage, [string, string]> = {
  waiting: ['--k-amber-ink', '--k-amber-soft'],
  active: ['--k-ok', '--k-ok-soft'],
  stalled: ['--k-hot', '--k-hot-soft'],
  idle: ['--k-accent', '--k-accent-soft'],
  done: ['--k-ink-4', '--k-line-soft'],
}
const SESSION_LABEL: Record<string, string> = { working: '在写', waiting: '等你', done: '做完了', interrupted: '停在半路' }
const SESSION_TONE: Record<string, Stage> = { working: 'active', waiting: 'waiting', done: 'done', interrupted: 'idle' }
const SOURCE: Record<string, string> = { claude: 'Claude', codex: 'Codex' }

const tone = (stage: Stage) => {
  const [c, cs] = STAGE_TONE[stage] ?? STAGE_TONE.done
  return { ['--c' as string]: `var(${c})`, ['--cs' as string]: `var(${cs})` }
}

function ago(ts: number | null | undefined) {
  if (!ts) return ''
  const s = Date.now() / 1000 - ts
  if (s < 90) return '刚刚'
  if (s < 3600) return `${Math.round(s / 60)} 分钟前`
  if (s < 86400) return `${Math.round(s / 3600)} 小时前`
  return `${Math.round(s / 86400)} 天前`
}
const pad = (n: number) => String(n).padStart(2, '0')
function when(ts: number | null | undefined) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function CopyResume({ command, compact }: { command: string; compact?: boolean }) {
  const [done, setDone] = useState(false)
  if (!command) return null
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(command)
    } catch {
      const area = document.createElement('textarea')
      area.value = command
      document.body.appendChild(area)
      area.select()
      document.execCommand('copy')
      area.remove()
    }
    setDone(true)
    window.setTimeout(() => setDone(false), 1600)
  }
  return (
    <button onClick={(e) => { e.stopPropagation(); copy() }} className={btn} title={command} aria-label="复制恢复命令">
      {done ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
      {done ? '已复制' : compact ? '恢复' : '复制恢复命令'}
    </button>
  )
}

/** The end of a reply from its first whole line, so a clipped tail does not start mid-sentence. */
function ending(tail: string, head: string) {
  if (!tail) return head
  if (!tail.startsWith('…')) return tail
  const cut = tail.indexOf('\n')
  return cut > 0 && cut < tail.length - 40 ? tail.slice(cut + 1) : tail
}

function Waiting({ s, onOpen }: { s: PlaneSession; onOpen: () => void }) {
  return (
    <article className="k-card relative min-w-0 overflow-hidden px-5 pb-4 pt-4 sm:px-6">
      <span aria-hidden className="absolute inset-y-0 left-0 w-[3px]" style={{ background: 'var(--k-amber)' }} />
      <header className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px] text-k-ink4">
        <button onClick={onOpen} className="font-semibold text-k-ink2 hover:text-k-accent">{s.project}</button>
        <span>{SOURCE[s.source] ?? s.source}</span>
        <span className="k-mono">{s.session_id.slice(0, 8)}</span>
        <span className="ml-auto whitespace-nowrap">{ago(s.last_active)}</span>
      </header>
      <p className="mt-2 line-clamp-4 whitespace-pre-line break-words text-[14.5px] leading-[1.7] text-k-ink2">{ending(s.last_agent_tail, s.last_agent)}</p>
      <div className="mt-2 text-[12.5px] text-k-ink4">{s.waiting_reason}</div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <CopyResume command={s.resume} />
        {s.last_user && <span className="min-w-0 flex-1 truncate text-[13px] text-k-ink4" title={s.last_user}>你上一句：{s.last_user}</span>}
      </div>
    </article>
  )
}

function Stat({ n, unit, warn }: { n: number | string | null; unit: string; warn?: boolean }) {
  return (
    <span className="inline-flex items-baseline gap-1">
      <span className="k-num text-[17px] font-bold leading-none text-k-ink" style={warn ? { color: 'var(--k-hot)' } : undefined}>{n ?? '—'}</span>
      <span className="text-[12px] text-k-ink4">{unit}</span>
    </span>
  )
}

function KbLine({ kb }: { kb: PlaneProject['kb'] }) {
  const { open } = useReader()
  if (!kb) return <div className="text-[13px] text-k-ink4">知识库里还没有项目页</div>
  return (
    <div className="text-[13.5px] leading-[1.6] text-k-ink3">
      <button onClick={(e) => { e.stopPropagation(); open(kb.path) }} className="k-wiki mr-1.5 text-[13px]" title={kb.path}>知识库</button>
      {kb.state || kb.title}
      {kb.as_of && <span className="ml-1 text-[12px] text-k-ink4">（{kb.as_of.replace(/^当前状态/, '').replace(/^（|）$/g, '')}）</span>}
    </div>
  )
}

function ProjectCard({ p, onOpen }: { p: PlaneProject; onOpen: () => void }) {
  const repo = p.repos[0]
  return (
    <article onClick={onOpen} className="k-card k-card-hover flex min-w-0 cursor-pointer flex-col gap-3 px-5 py-4 sm:px-6"
      role="button" tabIndex={0} onKeyDown={(e) => e.key === 'Enter' && onOpen()}>
      <header className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <h3 className="min-w-0 truncate text-[17px] font-[650] text-k-ink">{p.name}</h3>
        <span className="k-chip" style={tone(p.stage)}>{p.stage_label}</span>
        <span className="ml-auto whitespace-nowrap text-[12.5px] text-k-ink4">{ago(p.last_action)}</span>
      </header>
      <div className="-mt-1.5 text-[13px] text-k-ink4">{p.reason}</div>
      <div className="flex flex-wrap gap-x-5 gap-y-2">
        <Stat n={p.sessions.recent} unit="个会话 24h 内" />
        <Stat n={p.dirty} unit="处未提交" warn={p.stage === 'stalled' && p.dirty > 0} />
        <Stat n={p.open_prs} unit="个未合并 PR" />
        <Stat n={p.orca ? p.orca.live : '?'} unit={p.orca ? `个终端${p.orca.unread ? ` · ${p.orca.unread} 未读` : ''}` : 'Orca 未知'} />
      </div>
      {p.last_conclusion && (
        <p className="line-clamp-3 whitespace-pre-line break-words text-[14px] leading-[1.7] text-k-ink2">
          <span className="mr-1.5 text-[12.5px] text-k-ink4">最后结论 · {ago(p.last_conclusion.ts)}</span>{p.last_conclusion.text}
        </p>
      )}
      {repo && (
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-[13px] text-k-ink3">
          <span className="inline-flex min-w-0 items-center gap-1"><GitBranch className="h-3.5 w-3.5 shrink-0 text-k-ink4" /><span className="k-mono truncate">{repo.branch || '（分离）'}</span></span>
          {repo.ahead ? <span>领先 {repo.ahead}</span> : null}
          {repo.behind ? <span>落后 {repo.behind}</span> : null}
          {repo.last_commit && <span className="text-k-ink4">提交于 {ago(repo.last_commit)}</span>}
          {p.repos.length > 1 && <span className="text-k-ink4">另有 {p.repos.length - 1} 个仓库</span>}
        </div>
      )}
      <KbLine kb={p.kb} />
    </article>
  )
}

function SessionRow({ s }: { s: PlaneSession }) {
  const [full, setFull] = useState(false)
  return (
    <li className="relative pb-6 pl-6">
      <span aria-hidden className="absolute left-[5px] top-2 h-full w-px bg-k-line" />
      <span aria-hidden className="absolute left-0 top-[7px] h-[11px] w-[11px] rounded-full border-2 border-k-bg" style={{ background: `var(${STAGE_TONE[SESSION_TONE[s.state] ?? 'done'][0]})` }} />
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px] text-k-ink4">
        <span className="k-chip" style={tone(SESSION_TONE[s.state] ?? 'done')}>{SESSION_LABEL[s.state] ?? s.state}</span>
        <span>{SOURCE[s.source] ?? s.source}</span>
        <span className="k-mono">{s.session_id.slice(0, 8)}</span>
        <span>{when(s.started)} → {when(s.last_active)}</span>
        <span>{s.turns} 轮</span>
        {s.branch && <span className="k-mono truncate">{s.branch}</span>}
      </div>
      {s.last_user && <p className="mt-2 break-words text-[13.5px] leading-[1.65] text-k-ink3"><span className="text-k-ink4">你：</span>{s.last_user}</p>}
      {s.last_agent && (
        <p className={`mt-1.5 whitespace-pre-line break-words text-[14px] leading-[1.7] text-k-ink2 ${full ? '' : 'line-clamp-4'}`}>
          <span className="text-k-ink4">Agent：</span>{s.last_agent}
        </p>
      )}
      {s.waiting && <p className="mt-1.5 text-[12.5px] text-k-amber-ink">{s.waiting_reason}</p>}
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <CopyResume command={s.resume} />
        {s.last_agent.length > 160 && <button onClick={() => setFull((v) => !v)} className="px-2 text-[13px] text-k-ink4 hover:text-k-ink">{full ? '收起' : '展开'}</button>}
        {s.last_agent_tail && full && s.last_agent_tail !== s.last_agent && (
          <p className="w-full whitespace-pre-line break-words rounded-control bg-k-sunk px-3 py-2 text-[13px] leading-[1.65] text-k-ink3">结尾：{s.last_agent_tail}</p>
        )}
      </div>
    </li>
  )
}

function ProjectDrawer({ p, onClose }: { p: PlaneProject; onClose: () => void }) {
  const [sessions, setSessions] = useState<PlaneSession[] | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    plane.sessions(p.name).then((b) => setSessions(b.items)).catch((e) => setError(String(e)))
  }, [p.name])
  return (
    <Drawer wide title={p.name} sub={`${p.stage_label} · ${p.reason}`} onClose={onClose}>
      <KV rows={[
        ['仓库', p.repos.length ? (
          <ul className="space-y-1">
            {p.repos.map((r) => (
              <li key={r.path} className="break-words">
                <span className="font-medium text-k-ink">{r.name}</span>
                <span className="k-mono ml-2 text-[13px]">{r.branch || '（分离）'}</span>
                <span className="ml-2 text-[13px] text-k-ink4">
                  {[r.dirty ? `${r.dirty} 处未提交` : '干净', r.ahead ? `领先 ${r.ahead}` : '', r.behind ? `落后 ${r.behind}` : '',
                    r.last_commit ? `提交于 ${ago(r.last_commit)}` : ''].filter(Boolean).join(' · ')}
                </span>
              </li>
            ))}
          </ul>
        ) : '没找到 git 仓库'],
        ['PR', p.open_prs === null ? '未知（GitHub 汇总没覆盖这个仓库）' : p.prs.length ? (
          <ul className="space-y-1">{p.prs.map((x) => <li key={x.number}><span className="k-mono">#{x.number}</span> {x.title}{x.draft && <span className="ml-1 text-k-ink4">（草稿）</span>}</li>)}</ul>
        ) : '没有'],
        ['Orca', p.orca === null ? '未知（这轮没读到 Orca）' : p.orca.worktrees.length ? (
          <ul className="space-y-1">
            {p.orca.worktrees.map((w) => (
              <li key={w.path} className="inline-flex w-full flex-wrap items-center gap-x-2">
                <TerminalSquare className="h-3.5 w-3.5 text-k-ink4" />
                <span className="font-medium text-k-ink">{w.name}</span><span className="k-mono text-[13px]">{w.branch}</span>
                <span className="text-[13px] text-k-ink4">{w.live} 个终端{w.unread ? ' · 未读' : ''} · {w.status}</span>
              </li>
            ))}
          </ul>
        ) : '没有打开的工作区'],
        ['知识库', <KbLine key="kb" kb={p.kb} />],
      ]} />
      <h3 className="mb-4 mt-8 text-[16px] font-[650] text-k-ink">会话时间线 <span className="text-[13px] font-normal text-k-ink4">最近 7 天</span></h3>
      {error && <Problem message={`读不到会话：${error}`} />}
      {!sessions && !error && <Empty>读取中…</Empty>}
      {sessions && !sessions.length && <Empty>最近 7 天没有会话</Empty>}
      {sessions && sessions.length > 0 && <ul>{sessions.map((s) => <SessionRow key={s.id} s={s} />)}</ul>}
    </Drawer>
  )
}

export default function InProgressView() {
  const [body, setBody] = useState<ProjectsBody | null>(null)
  const [error, setError] = useState('')
  const [open, setOpen] = useState<string | null>(null)
  const load = useCallback(() => {
    plane.projects().then((b) => { setBody(b); setError('') }).catch((e) => setError(String(e)))
  }, [])
  useEffect(() => {
    load()
    const t = window.setInterval(load, REFRESH_MS)
    return () => window.clearInterval(t)
  }, [load])

  const head = (
    <div className="flex flex-wrap items-baseline gap-x-4 gap-y-2">
      <h1 className="text-[28px] font-[650] tracking-[0.01em] text-k-ink">进行中</h1>
      {body && <span className="text-[15px] text-k-ink3">{body.count} 个项目 · {body.items.reduce((n, p) => n + p.sessions.working, 0)} 个会话在写</span>}
      {body && (
        <span className="ml-auto inline-flex items-center gap-2 text-[13px] text-k-ink4" title={body.collected_at ? when(body.collected_at) : undefined}>
          <span className="h-2 w-2 rounded-full" style={{ background: body.stale ? 'var(--k-hot)' : 'var(--k-ok)' }} />
          {body.collected_at ? `采集于 ${ago(body.collected_at)}${body.stale ? '，数据可能过期' : ''}` : '还没采集过'}
        </span>
      )}
    </div>
  )
  if (!body) {
    return <div className="mx-auto w-full max-w-[1180px] pb-16">{head}{error ? <div className="mt-6"><Problem message={`读不到状态库：${error}`} /></div> : <Empty>读取中…</Empty>}</div>
  }
  const { waiting, stalled, waiting_older } = body.attention
  const project = body.items.find((p) => p.name === open) ?? null

  return (
    <div className="mx-auto w-full max-w-[1180px] pb-16">
      {head}
      {error && <div className="mt-4"><Problem message={`刷新失败：${error}`} /></div>}
      {!body.collected_at && (
        <p className="k-card mt-6 px-6 py-6 text-[14.5px] leading-[1.75] text-k-ink3">
          还没有数据。运行 <code className="k-mono">kg plane collect sessions</code> 采集一次，或者在中台加上每 5 分钟的定时规则。
        </p>
      )}

      <Section title="等你回答" aside={waiting.length ? `${waiting.length} 个会话${waiting_older ? `，另有 ${waiting_older} 个超过 3 天没回` : ''}` : undefined}>
        {waiting.length ? (
          <div className="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-2">{waiting.map((s) => <Waiting key={s.id} s={s} onOpen={() => setOpen(s.project)} />)}</div>
        ) : <p className="k-card px-6 py-6 text-center text-[14.5px] text-k-ink4">没有在等你回答的会话。</p>}
      </Section>

      {stalled.length > 0 && (
        <Section title="停滞" aside="3 天没动，但还有没提交的改动或没合并的 PR">
          <div className="k-card divide-y divide-k-line-soft">
            {stalled.map((p) => (
              <button key={p.id} onClick={() => setOpen(p.name)} className="grid w-full grid-cols-[minmax(0,1fr)_auto] items-baseline gap-3 px-5 py-3 text-left hover:bg-k-sunk">
                <span className="min-w-0">
                  <span className="block truncate text-[14.5px] font-medium text-k-ink">{p.name}</span>
                  <span className="block truncate text-[13px] text-k-ink4">{p.reason}{p.repos[0] ? ` · ${p.repos[0].branch}` : ''}</span>
                </span>
                <ArrowUpRight className="h-4 w-4 text-k-ink4" />
              </button>
            ))}
          </div>
        </Section>
      )}

      <Section title="项目" aside="按等你、活跃、停滞、歇着、已收尾排">
        {body.items.length ? (
          <div className="grid grid-cols-[minmax(0,1fr)] gap-4 md:grid-cols-2">{body.items.map((p) => <ProjectCard key={p.id} p={p} onOpen={() => setOpen(p.name)} />)}</div>
        ) : <Empty>最近 7 天没有在推进的项目</Empty>}
      </Section>

      {project && <ProjectDrawer p={project} onClose={() => setOpen(null)} />}
    </div>
  )
}
