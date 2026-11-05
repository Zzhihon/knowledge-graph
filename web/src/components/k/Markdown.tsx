import { isValidElement, type ReactNode } from 'react'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import Mermaid from './Mermaid'
import { useReader } from '../../lib/library'

const CALLOUT: Record<string, [string, string, string]> = {
  note: ['--k-accent', '--k-accent-soft', '笔记'],
  info: ['--k-accent', '--k-accent-soft', '说明'],
  abstract: ['--k-accent', '--k-accent-soft', '概述'],
  summary: ['--k-accent', '--k-accent-soft', '概述'],
  tldr: ['--k-accent', '--k-accent-soft', '要点'],
  tip: ['--k-ok', '--k-ok-soft', '提示'],
  success: ['--k-ok', '--k-ok-soft', '成功'],
  check: ['--k-ok', '--k-ok-soft', '已确认'],
  question: ['--k-violet', '--k-violet-soft', '问题'],
  example: ['--k-violet', '--k-violet-soft', '例子'],
  warning: ['--k-amber-ink', '--k-amber-soft', '注意'],
  caution: ['--k-amber-ink', '--k-amber-soft', '注意'],
  important: ['--k-amber-ink', '--k-amber-soft', '重要'],
  danger: ['--k-hot', '--k-hot-soft', '危险'],
  failure: ['--k-hot', '--k-hot-soft', '失败'],
  bug: ['--k-hot', '--k-hot-soft', '缺陷'],
  quote: ['--k-ink-3', '--k-sunk', '引用'],
}

type MdNode = { type: string; value?: string; children?: MdNode[]; data?: Record<string, unknown> }

/** Obsidian callouts: `> [!type] 标题` becomes an <aside> carrying the type and title. */
function remarkCallouts() {
  const walk = (node: MdNode) => {
    if (node.type === 'blockquote') {
      const para = node.children?.[0]
      const text = para?.type === 'paragraph' ? para.children?.[0] : undefined
      const m = text?.type === 'text' ? text.value!.match(/^\[!(\w+)\][+-]?[ \t]*([^\n]*)\n?/) : null
      if (m && text && para) {
        text.value = text.value!.slice(m[0].length)
        if (!text.value && para.children!.length === 1) node.children!.shift()
        const kind = m[1].toLowerCase()
        node.data = { hName: 'aside', hProperties: { 'data-callout': kind, 'data-title': m[2].trim() } }
      }
    }
    node.children?.forEach(walk)
  }
  return (tree: MdNode) => walk(tree)
}

/** [[id]] and [[id|label]] become links the reader can open; `#kg:` keeps them apart from web links. */
const withWikiLinks = (md: string) =>
  md.replace(/\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]+))?\]\]/g, (_, id: string, label?: string) =>
    `[${(label ?? '').replace(/[[\]]/g, '') || '​' + id.trim()}](#kg:${encodeURIComponent(id.trim())})`)

function WikiLink({ id, children }: { id: string; children: ReactNode }) {
  const { index, open } = useReader()
  const hit = index[id]
  // A link written without a label shows the entry's title.
  const label = typeof children === 'string' && children.startsWith('​') ? hit?.[0] ?? children.slice(1) : children
  if (!hit) return <span className="k-wiki miss" title={`知识库里没有 ${id}`}>{label}</span>
  return <a className="k-wiki" title={id} onClick={(e) => { e.preventDefault(); open(hit[1]) }} href={`#${id}`}>{label}</a>
}

const components: Components = {
  a({ href, children }) {
    if (href?.startsWith('#kg:')) {
      const flat = Array.isArray(children) ? children.join('') : children
      return <WikiLink id={decodeURIComponent(href.slice(4))}>{flat}</WikiLink>
    }
    return <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>
  },
  aside({ node, children }) {
    const kind = String(node?.properties?.dataCallout ?? '')
    if (!kind) return <aside>{children}</aside>
    const [c, cs, fallback] = CALLOUT[kind] ?? CALLOUT.note
    const title = String(node?.properties?.dataTitle ?? '') || fallback
    return (
      <div className="k-callout" style={{ ['--c' as string]: `var(${c})`, ['--cs' as string]: `var(${cs})` }}>
        <div className="k-callout-title">{title}</div>
        {children}
      </div>
    )
  },
  pre({ children }) {
    const code = isValidElement<{ className?: string; children?: ReactNode }>(children) ? children : null
    if (code?.props.className?.includes('language-mermaid')) return <Mermaid code={String(code.props.children ?? '').trim()} />
    return <pre>{children}</pre>
  },
}

/** Markdown from the vault, rendered as reading prose. `inline` drops the paragraph box for one-line items. */
export default function Markdown({ source, inline = false, className = '' }: { source: string; inline?: boolean; className?: string }) {
  const Box = inline ? 'span' : 'div'
  return (
    <Box className={`${inline ? 'k-inline' : 'k-prose'} ${className}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkCallouts]}
        components={components}
        urlTransform={(url) => (url.startsWith('#kg:') || /^(https?:|mailto:|#)/.test(url) ? url : '')}
      >
        {withWikiLinks(source)}
      </ReactMarkdown>
    </Box>
  )
}
