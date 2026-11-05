// The daily brief is Markdown written by the kg-daily skill (integrations/autoloop/skills/kg-daily):
// a "> 一句话：" lead, then "## " sections whose list items start with "类型：". This splits it into
// what the daily view lays out; anything it does not recognise stays as Markdown in `other`.

export interface DailyItem { kind: string; md: string }
export interface DailyProject { id: string; isNew: boolean; md: string }
export interface DailyNews { title: string; md: string; source: string; url: string }

export interface Daily {
  lead: string
  projects: DailyProject[]
  fresh: DailyItem[]
  lab: DailyItem[]
  todo: string[]
  news: DailyNews[]
  newsNote: string
  diagram: string
  other: { title: string; md: string }[]
}

const items = (body: string) =>
  body.split('\n').filter((l) => /^\s*[-*]\s/.test(l)).map((l) => l.replace(/^\s*[-*]\s+/, '').trim())
const notItems = (body: string) => body.split('\n').filter((l) => l.trim() && !/^\s*[-*]\s/.test(l)).join('\n').trim()

function prefixed(line: string): DailyItem {
  const m = line.match(/^([^：:[*`]{1,6})[：:]\s*(.*)$/)
  return m ? { kind: m[1].trim(), md: m[2] } : { kind: '', md: line }
}

export function parseDaily(content: string): Daily {
  const out: Daily = { lead: '', projects: [], fresh: [], lab: [], todo: [], news: [], newsNote: '', diagram: '', other: [] }
  const [head, ...parts] = content.split(/\n(?=## )/)
  out.lead = (head.match(/^>\s*(.+)$/m)?.[1] ?? '').replace(/^一句话[：:]\s*/, '')
  for (const part of parts) {
    const nl = part.indexOf('\n')
    const title = part.slice(3, nl < 0 ? undefined : nl).trim()
    const body = nl < 0 ? '' : part.slice(nl + 1)
    if (/项目/.test(title)) {
      out.projects = items(body).map((l) => {
        const m = l.match(/^\[\[([^\]|]+)(?:\|[^\]]*)?\]\]\s*(（[^）]*）|\([^)]*\))?\s*[：:]\s*(.*)$/)
        return m ? { id: m[1].trim(), isNew: /新/.test(m[2] ?? ''), md: m[3] } : { id: '', isNew: false, md: l }
      })
    } else if (/沉淀/.test(title)) out.fresh = items(body).map(prefixed)
    else if (/实验/.test(title)) out.lab = items(body).map(prefixed)
    else if (/GitHub|待办/.test(title)) out.todo = items(body)
    else if (/图解/.test(title)) out.diagram = body.trim()
    else if (/外部|简报/.test(title)) {
      out.news = items(body).map((l) => {
        const link = l.match(/\[([^\]]+)\]\((https?:[^)]+)\)/)
        const t = l.match(/\*\*([^*]+)\*\*/)?.[1] ?? ''
        const md = l.replace(/\*\*[^*]+\*\*/, '').replace(/[（(]?\s*\[[^\]]+\]\([^)]+\)\s*[）)]?/, '').replace(/^\s*[—–\-:：]+\s*/, '').trim()
        return { title: t, md, source: link?.[1] ?? '', url: link?.[2] ?? '' }
      })
      if (!out.news.length) out.newsNote = notItems(body)
    } else out.other.push({ title, md: body.trim() })
  }
  return out
}

/** "14.5 天" style ages inside a to-do line, for the rank list's right column. */
export const ageOf = (line: string) => line.match(/压了?\s*([\d.]+)\s*天/)?.[1] ?? line.match(/([\d.]+)\s*天没动/)?.[1] ?? ''
