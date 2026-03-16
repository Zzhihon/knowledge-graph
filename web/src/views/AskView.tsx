import { useState, useRef, useEffect } from 'react'
import { Search, Sparkles, BrainCircuit, Send } from 'lucide-react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { get, post, streamSSE } from '../api/client'
import type { SearchResult, Source, Conversation } from '../types'

interface Message {
  role: 'user' | 'assistant'
  content: string
  sources?: Source[]
}

const WELCOME_MSG: Message = {
  role: 'assistant',
  content: '你好，我是你的 Vault 知识助理。你可以直接向我提问（如："滑动窗口和双指针的区别"），我会调用图谱进行 RAG 回答；或者切换到语义检索模式进行搜索。',
}

interface Props {
  conversationId: string | null
  onConversationCreated: (id: string) => void
  onPreviewEntry?: (entryId: string) => void
}

export default function AskView({ conversationId, onConversationCreated, onPreviewEntry }: Props) {
  const [messages, setMessages] = useState<Message[]>([WELCOME_MSG])
  const [input, setInput] = useState('')
  const [mode, setMode] = useState<'ask' | 'query'>('ask')
  const [loading, setLoading] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const activeConvId = useRef<string | null>(conversationId)

  // Keep ref in sync with prop
  activeConvId.current = conversationId

  // Load conversation when id changes
  useEffect(() => {
    if (!conversationId) {
      setMessages([WELCOME_MSG])
      return
    }

    let cancelled = false
    ;(async () => {
      try {
        const conv = await get<Conversation>(`/conversations/${conversationId}`)
        if (cancelled) return
        if (conv.messages.length > 0) {
          setMessages(
            conv.messages.map((m) => ({
              role: m.role as 'user' | 'assistant',
              content: m.content,
              sources: m.sources ?? undefined,
            })),
          )
        } else {
          setMessages([WELCOME_MSG])
        }
        setMode(conv.mode as 'ask' | 'query')
      } catch {
        if (!cancelled) setMessages([WELCOME_MSG])
      }
    })()

    return () => { cancelled = true }
  }, [conversationId])

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages])

  const persistMessage = async (convId: string, role: string, content: string, sources?: Source[]) => {
    try {
      await post('/conversations/' + convId + '/messages', {
        role,
        content,
        sources: sources ?? null,
      })
    } catch {
      // non-critical
    }
  }

  const handleSend = async (e: React.FormEvent) => {
    e.preventDefault()
    const q = input.trim()
    if (!q || loading) return

    setMessages((prev) => [...prev, { role: 'user', content: q }])
    setInput('')
    setLoading(true)

    // Ensure we have a conversation id
    let convId = activeConvId.current
    if (!convId) {
      try {
        const created = await post<{ id: string }>('/conversations', {
          title: q.slice(0, 50),
          mode,
        })
        convId = created.id
        activeConvId.current = convId
        onConversationCreated(convId)
      } catch {
        setMessages((prev) => [...prev, { role: 'assistant', content: '创建会话失败，请检查后端。' }])
        setLoading(false)
        return
      }
    }

    // Persist user message
    await persistMessage(convId, 'user', q)

    if (mode === 'query') {
      try {
        const results = await post<SearchResult[]>('/query', { query: q, top_k: 10 })
        const text = results.length
          ? results.map((r, i) => `${i + 1}. **${r.title}** (${r.domain}) — 相似度 ${r.score?.toFixed(2) ?? '?'}\n   ${r.snippet || ''}`).join('\n\n')
          : '未找到相关条目。'
        setMessages((prev) => [...prev, { role: 'assistant', content: text }])
        await persistMessage(convId, 'assistant', text)
      } catch {
        const errMsg = '检索失败，请检查后端是否运行。'
        setMessages((prev) => [...prev, { role: 'assistant', content: errMsg }])
        await persistMessage(convId, 'assistant', errMsg)
      } finally {
        setLoading(false)
      }
      return
    }

    // Ask mode: SSE streaming
    setMessages((prev) => [...prev, { role: 'assistant', content: '' }])
    const capturedConvId = convId
    let fullContent = ''

    abortRef.current = streamSSE(
      '/ask',
      { question: q },
      (text) => {
        fullContent += text
        setMessages((prev) => {
          const updated = [...prev]
          const last = updated[updated.length - 1]
          updated[updated.length - 1] = { ...last, content: last.content + text }
          return updated
        })
      },
      (sources) => {
        setMessages((prev) => {
          const updated = [...prev]
          const last = updated[updated.length - 1]
          updated[updated.length - 1] = { ...last, sources }
          return updated
        })
        setLoading(false)
        persistMessage(capturedConvId, 'assistant', fullContent, sources)
      },
    )
  }

  return (
    <div className="flex flex-col h-[calc(100vh-8rem)]">
      {/* Message area */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-6 space-y-5">
        {messages.map((msg, i) => (
          <div key={i} className={`flex gap-3 ${msg.role === 'user' ? 'flex-row-reverse' : ''}`}>
            <div className={`w-7 h-7 rounded-lg flex items-center justify-center shrink-0 mt-0.5 ${
              msg.role === 'user'
                ? 'bg-indigo-500/15 text-indigo-400 ring-1 ring-indigo-500/20'
                : 'bg-zinc-800/80 text-zinc-400 ring-1 ring-zinc-700/50'
            }`}>
              {msg.role === 'user' ? <Search className="w-3.5 h-3.5" /> : <BrainCircuit className="w-3.5 h-3.5" />}
            </div>
            <div className={`max-w-[85%] rounded-xl px-4 py-2.5 text-sm leading-relaxed ${
              msg.role === 'user'
                ? 'bg-indigo-500/10 text-indigo-100 ring-1 ring-indigo-500/15'
                : 'bg-zinc-800/30 ring-1 ring-zinc-700/40 text-zinc-300'
            }`}>
              {msg.role === 'assistant' && msg.content ? (
                <div className="prose-chat">
                  <Markdown remarkPlugins={[remarkGfm]}>
                    {msg.content}
                  </Markdown>
                </div>
              ) : (
                <div className="whitespace-pre-wrap">{msg.content || (loading && i === messages.length - 1 ? '思考中...' : '')}</div>
              )}
              {msg.sources && msg.sources.length > 0 && (
                <div className="mt-2.5 pt-2.5 border-t border-zinc-700/40 space-y-0.5">
                  <div className="text-[11px] font-medium text-zinc-500 mb-1.5">来源条目</div>
                  {msg.sources.map((s) => (
                    <button
                      key={s.id}
                      onClick={() => onPreviewEntry?.(s.id)}
                      className="flex items-center gap-1.5 w-full text-left text-[11px] text-indigo-400/80 hover:text-indigo-300 hover:bg-zinc-700/30 rounded px-1.5 py-1 -mx-1.5 transition-colors group"
                    >
                      <span className="truncate">{s.title}</span>
                      <span className="text-zinc-600 shrink-0 group-hover:text-zinc-500">({s.domain})</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>

      {/* Input area */}
      <div className="px-4 pb-4 pt-2">
        <div className="bg-zinc-900/80 ring-1 ring-zinc-700/60 rounded-2xl overflow-hidden focus-within:ring-indigo-500/40 transition-all">
          <form onSubmit={handleSend}>
            <div className="flex items-center px-4 pt-3 pb-2">
              <input
                type="text"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder={mode === 'ask' ? '向知识图谱提问...' : '输入关键词进行语义检索...'}
                className="flex-1 bg-transparent text-zinc-200 text-sm placeholder:text-zinc-600 focus:outline-none"
              />
            </div>
            <div className="flex items-center justify-between px-3 pb-2.5">
              {/* Mode toggle pill */}
              <div className="flex items-center bg-zinc-800/80 rounded-lg p-0.5 ring-1 ring-zinc-700/50">
                <button
                  type="button"
                  onClick={() => setMode('ask')}
                  className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium transition-all ${
                    mode === 'ask'
                      ? 'bg-zinc-700/80 text-zinc-100 shadow-sm'
                      : 'text-zinc-500 hover:text-zinc-300'
                  }`}
                >
                  <Sparkles className="w-3 h-3" />
                  <span>问答</span>
                </button>
                <button
                  type="button"
                  onClick={() => setMode('query')}
                  className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium transition-all ${
                    mode === 'query'
                      ? 'bg-zinc-700/80 text-zinc-100 shadow-sm'
                      : 'text-zinc-500 hover:text-zinc-300'
                  }`}
                >
                  <Search className="w-3 h-3" />
                  <span>检索</span>
                </button>
              </div>

              <button
                type="submit"
                disabled={!input.trim() || loading}
                className="flex items-center gap-1.5 px-3 py-1.5 bg-zinc-100 hover:bg-white text-zinc-900 disabled:opacity-40 disabled:cursor-not-allowed rounded-lg text-xs font-medium transition-colors"
              >
                <Send className="w-3 h-3" />
                发送
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>
  )
}
