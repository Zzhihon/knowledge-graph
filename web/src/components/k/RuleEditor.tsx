// Create or change one rule of agent-autoloop's rules.json, or the whole file as JSON. Nothing is
// written unless `agent-autoloop check` passes on the server; the refusal shows the checker's words.
import { useMemo, useState, type ReactNode } from 'react'
import { hub, HubApiError, type Rule, type RulesDoc, type WriteResult } from '../../api/hub'
import { cronInfo } from '../../lib/hub'
import { Drawer, Problem, btn, btnPrimary, field } from './HubBits'

const KNOWN = ['name', 'when', 'to', 'cwd', 'skill', 'prompt', 'group', 'parallel', 'tell', 'emits']
const words = (s: string) => s.split(/[,，\s]+/).map((x) => x.trim()).filter(Boolean)

interface Form {
  name: string; trigger: 'cron' | 'event'; cron: string; source: string; types: string
  to: string; cwd: string; skill: string; prompt: string; group: string; parallel: string
  tell: string; emits: boolean; emitSource: string; emitTypes: string
}

function formOf(rule: Rule | null, doc: RulesDoc): Form {
  const sources = Object.keys(doc.data?.sources ?? {})
  const agents = Object.keys(doc.data?.agents ?? {})
  const when = (rule?.when ?? { cron: '0 9 * * *' }) as Record<string, unknown>
  return {
    name: rule?.name ?? '',
    trigger: 'source' in when ? 'event' : 'cron',
    cron: typeof when.cron === 'string' ? when.cron : '0 9 * * *',
    source: typeof when.source === 'string' ? when.source : sources[0] ?? '',
    types: Array.isArray(when.types) ? when.types.join(', ') : '',
    to: rule?.to ?? agents[0] ?? '',
    cwd: rule?.cwd ?? '~/',
    skill: rule?.skill ?? '',
    prompt: rule?.prompt ?? '',
    group: rule?.group && rule.group !== rule.name ? rule.group : '',
    parallel: rule?.parallel ? String(rule.parallel) : '',
    tell: rule?.tell ?? '',
    emits: !!rule?.emits,
    emitSource: rule?.emits?.source ?? sources[0] ?? '',
    emitTypes: rule?.emits?.types?.join(', ') ?? '',
  }
}

/** The rule as rules.json wants it: empty optional fields left out, unknown fields of the original kept. */
function ruleOf(f: Form, original: Rule | null): Rule {
  const extra = Object.fromEntries(Object.entries(original ?? {}).filter(([k]) => !KNOWN.includes(k)))
  const rule: Rule = {
    ...extra,
    name: f.name.trim(),
    when: f.trigger === 'cron' ? { cron: f.cron.trim() } : { source: f.source, types: words(f.types) },
    to: f.to,
    cwd: f.cwd.trim(),
  }
  if (f.skill.trim()) rule.skill = f.skill.trim()
  if (f.prompt.trim()) rule.prompt = f.prompt.trim()
  if (f.group.trim()) rule.group = f.group.trim()
  if (f.parallel.trim()) rule.parallel = Number(f.parallel)
  if (f.tell) rule.tell = f.tell as Rule['tell']
  if (f.emits) rule.emits = { source: f.emitSource, types: words(f.emitTypes) }
  return rule
}

function Field({ label, hint, children, wide }: { label: string; hint?: ReactNode; children: ReactNode; wide?: boolean }) {
  return (
    <label className={`block min-w-0 ${wide ? 'sm:col-span-2' : ''}`}>
      <span className="mb-1 block text-[13px] font-medium text-k-ink3">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[12px] text-k-ink4">{hint}</span>}
    </label>
  )
}

export default function RuleEditor({ doc, original, onSaved, onClose }: {
  doc: RulesDoc; original: Rule | null; onSaved: (r: WriteResult) => void; onClose: () => void
}) {
  const [tab, setTab] = useState<'form' | 'file'>(doc.data ? 'form' : 'file')
  const [f, setF] = useState<Form>(() => formOf(original, doc))
  const [text, setText] = useState(doc.text)
  const [busy, setBusy] = useState<'' | 'check' | 'save'>('')
  const [problem, setProblem] = useState<HubApiError | null>(null)
  const [passed, setPassed] = useState(false)
  const set = <K extends keyof Form>(k: K, v: Form[K]) => { setF((x) => ({ ...x, [k]: v })); setPassed(false) }
  const rule = useMemo(() => ruleOf(f, original), [f, original])
  const sources = Object.entries(doc.data?.sources ?? {})
  const agents = Object.entries(doc.data?.agents ?? {})
  const cron = f.trigger === 'cron' ? cronInfo(f.cron) : null

  const run = async (what: 'check' | 'save') => {
    setBusy(what)
    setProblem(null)
    setPassed(false)
    try {
      if (what === 'check') {
        const r = tab === 'form' ? await hub.checkRule(original?.name ?? null, rule) : await hub.checkText(text)
        if (r.ok) setPassed(true)
        else setProblem(new HubApiError(422, '校验没通过', r.issues))
      } else {
        const r = tab === 'form' ? await hub.saveRule(original?.name ?? null, rule, doc.version) : await hub.saveText(text, doc.version)
        onSaved(r)
      }
    } catch (e) {
      setProblem(e instanceof HubApiError ? e : new HubApiError(0, String(e)))
    } finally {
      setBusy('')
    }
  }

  return (
    <Drawer wide title={original ? `修改规则：${original.name}` : '新建规则'} sub={doc.file} onClose={onClose}>
      <div className="k-seg" role="group" aria-label="编辑方式">
        <button aria-pressed={tab === 'form'} disabled={!doc.data} onClick={() => { setTab('form'); setPassed(false) }}>表单</button>
        <button aria-pressed={tab === 'file'} onClick={() => { setTab('file'); setPassed(false) }}>整个文件 JSON</button>
      </div>
      <p className="mt-3 text-[13px] leading-[1.7] text-k-ink4">
        保存前会用 <code className="k-mono">agent-autoloop check</code> 校验，不通过就不写入；写入前把当前文件备份成 <code className="k-mono">rules.json.bak-时间</code>，可以在"改动记录"里回滚。中台会自己读到新规则。
      </p>

      {tab === 'form' ? (
        <div className="mt-5 grid gap-4 sm:grid-cols-2">
          <Field label="名字"><input className={field} value={f.name} onChange={(e) => set('name', e.target.value)} placeholder="比如：写日报" /></Field>
          <Field label="交给哪个 Agent">
            <select className={field} value={f.to} onChange={(e) => set('to', e.target.value)}>
              {agents.map(([id, a]) => <option key={id} value={id}>{a.name}（{id}）</option>)}
              {!agents.some(([id]) => id === f.to) && <option value={f.to}>{f.to || '—'}</option>}
            </select>
          </Field>
          <Field label="触发方式" wide>
            <div className="k-seg" role="group">
              <button type="button" aria-pressed={f.trigger === 'cron'} onClick={() => set('trigger', 'cron')}>定时</button>
              <button type="button" aria-pressed={f.trigger === 'event'} onClick={() => set('trigger', 'event')}>事件</button>
            </div>
          </Field>
          {f.trigger === 'cron' ? (
            <Field label="cron（分 时 日 月 星期）" hint={cron?.text} wide>
              <input className={`${field} k-mono`} value={f.cron} onChange={(e) => set('cron', e.target.value)} placeholder="0 9 * * *" />
            </Field>
          ) : (
            <>
              <Field label="来源">
                <select className={field} value={f.source} onChange={(e) => set('source', e.target.value)}>
                  {sources.map(([id, s]) => <option key={id} value={id}>{s.name}（{id}）</option>)}
                </select>
              </Field>
              <Field label="事件类型" hint="逗号分隔，可以用 x.* 或 *">
                <input className={`${field} k-mono`} value={f.types} onChange={(e) => set('types', e.target.value)} placeholder="capture.ready" />
              </Field>
            </>
          )}
          <Field label="工作目录" hint="以 ~/ 或 / 开头" wide>
            <input className={`${field} k-mono`} value={f.cwd} onChange={(e) => set('cwd', e.target.value)} />
          </Field>
          <Field label="技能（skill）" hint="和提示词至少写一个；命令型 Agent 可以都不写">
            <input className={field} value={f.skill} onChange={(e) => set('skill', e.target.value)} />
          </Field>
          <Field label="同组（group）" hint="同一组一次只跑一轮；不写就是规则名">
            <input className={field} value={f.group} onChange={(e) => set('group', e.target.value)} />
          </Field>
          <Field label="提示词（prompt）" wide>
            <textarea className={`${field} min-h-[110px] leading-[1.7]`} value={f.prompt} onChange={(e) => set('prompt', e.target.value)} />
          </Field>
          <Field label="并行（parallel）" hint="写了就按对象分事项，这一组最多几轮">
            <input className={field} inputMode="numeric" value={f.parallel} onChange={(e) => set('parallel', e.target.value.replace(/\D/g, ''))} />
          </Field>
          <Field label="什么时候推送（tell）">
            <select className={field} value={f.tell} onChange={(e) => set('tell', e.target.value)}>
              <option value="">默认（有卡片时）</option>
              <option value="needed">有卡片时</option>
              <option value="always">每次</option>
              <option value="never">不推送</option>
            </select>
          </Field>
          <div className="sm:col-span-2">
            <label className="flex items-center gap-2 text-[13.5px] text-k-ink2">
              <input type="checkbox" checked={f.emits} onChange={(e) => set('emits', e.target.checked)} />
              回执里可以发事件（emits）
            </label>
            {f.emits && (
              <div className="mt-3 grid gap-4 sm:grid-cols-2">
                <Field label="发到哪个来源">
                  <select className={field} value={f.emitSource} onChange={(e) => set('emitSource', e.target.value)}>
                    {sources.map(([id, s]) => <option key={id} value={id}>{s.name}（{id}）</option>)}
                  </select>
                </Field>
                <Field label="能发的类型" hint="逗号分隔">
                  <input className={`${field} k-mono`} value={f.emitTypes} onChange={(e) => set('emitTypes', e.target.value)} />
                </Field>
              </div>
            )}
          </div>
          <details className="sm:col-span-2">
            <summary className="cursor-pointer text-[13px] text-k-ink4">会写成这样（JSON）</summary>
            <pre className="k-mono mt-2 overflow-x-auto rounded-tile border border-k-line bg-k-sunk p-3 text-[12.5px] leading-[1.6] text-k-ink2">{JSON.stringify(rule, null, 2)}</pre>
          </details>
        </div>
      ) : (
        <div className="mt-5">
          {doc.parseError && <div className="mb-3"><Problem message={doc.parseError} /></div>}
          <textarea className={`${field} k-mono min-h-[420px] text-[12.5px] leading-[1.6]`} spellCheck={false}
            value={text} onChange={(e) => { setText(e.target.value); setPassed(false) }} aria-label="rules.json" />
        </div>
      )}

      <div className="sticky bottom-0 -mx-5 mt-6 border-t border-k-line-soft bg-k-bg px-5 py-3 sm:-mx-7 sm:px-7">
        {problem && <div className="mb-3"><Problem message={problem.status === 409 ? `${problem.message}（关掉再打开会读到最新文件）` : problem.message} issues={problem.issues} fromHub={problem.fromHub} /></div>}
        {passed && <div className="mb-3 rounded-tile px-4 py-2 text-[14px] text-k-ok" style={{ background: 'var(--k-ok-soft)' }}>校验通过，还没写入。</div>}
        <div className="flex flex-wrap items-center gap-2">
          <button className={btn} disabled={!!busy} onClick={() => run('check')}>{busy === 'check' ? '校验中…' : '只校验'}</button>
          <button className={btnPrimary} disabled={!!busy || (tab === 'form' && !f.name.trim())} onClick={() => run('save')}>
            {busy === 'save' ? '校验并保存中…' : '校验并保存'}
          </button>
          <button className="ml-auto px-2 text-[13.5px] text-k-ink4 hover:text-k-ink" onClick={onClose}>取消</button>
        </div>
      </div>
    </Drawer>
  )
}
