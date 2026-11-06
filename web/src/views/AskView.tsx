import { useState, useRef, useEffect, useCallback } from 'react'
import { Search, Sparkles, BrainCircuit, Send, Loader2, BookPlus, RotateCcw } from 'lucide-react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { get, post, streamSSE } from '../api/client'
import { artifacts } from '../api/artifacts'
import type { SearchResult, Source, Conversation } from '../types'

type AskPhase = 'idle' | 'search' | 'generate' | 'fallback'

interface Message {
  role: 'user' | 'assistant'
  content: string
  sources?: Source[]
  ingestedId?: string
  isError?: boolean
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
  const [, setAskPhase] = useState<AskPhase>('idle')
  const [statusMsg, setStatusMsg] = useState('')
  const scrollRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const activeConvId = useRef<string | null>(conversationId)
  const lastQuestionRef = useRef('')

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

  const executeQuestion = useCallback(async (q: string, isRetry = false) => {
    if (!q || loading) return

    lastQuestionRef.current = q

    if (isRetry) {
      // Remove the last error message before retrying
      setMessages((prev) => {
        const last = prev[prev.length - 1]
        return last?.isError ? prev.slice(0, -1) : prev
      })
    } else {
      setMessages((prev) => [...prev, { role: 'user', content: q }])
    }
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

    // Persist user message (skip on retry — already persisted)
    if (!isRetry) {
      await persistMessage(convId, 'user', q)
    }

    if (mode === 'query') {
      try {
        const results = await post<SearchResult[]>('/query', { query: q, top_k: 10 })
        const text = results.length
          ? results.map((r, i) => `${i + 1}. **${r.title}** (${r.domain}) — 相似度 ${r.score?.toFixed(2) ?? '?'}\n   ${r.snippet || ''}`).join('\n\n')
          : '未找到相关条目。'
        setMessages((prev) => [...prev, { role: 'assistant', content: text }])
        await persistMessage(convId, 'assistant', text)
      } catch {
        setMessages((prev) => [...prev, { role: 'assistant', content: '检索失败，请检查后端是否运行。', isError: true }])
      } finally {
        setLoading(false)
      }
      return
    }

    // Ask mode: SSE streaming
    setMessages((prev) => [...prev, { role: 'assistant', content: '' }])
    setAskPhase('search')
    setStatusMsg('正在检索相关条目...')
    const capturedConvId = convId
    let fullContent = ''
    let ingestedEntryId: string | undefined

    abortRef.current = streamSSE('/ask', { question: q }, {
      onToken: (text) => {
        fullContent += text
        setMessages((prev) => {
          const updated = [...prev]
          const last = updated[updated.length - 1]
          updated[updated.length - 1] = { ...last, content: last.content + text }
          return updated
        })
      },
      onDone: (sources) => {
        setMessages((prev) => {
          const updated = [...prev]
          const last = updated[updated.length - 1]
          updated[updated.length - 1] = { ...last, sources, ingestedId: ingestedEntryId }
          return updated
        })
        setLoading(false)
        setAskPhase('idle')
        setStatusMsg('')
        persistMessage(capturedConvId, 'assistant', fullContent, sources)
      },
      onStatus: (message, phase) => {
        setStatusMsg(message)
        if (phase === 'generate' || phase === 'fallback') {
          setAskPhase(phase as AskPhase)
        }
      },
      onIngested: (entryId) => {
        ingestedEntryId = entryId
      },
      onError: (message) => {
        setMessages((prev) => {
          const updated = [...prev]
          const last = updated[updated.length - 1]
          // Replace the empty streaming message with an error message
          updated[updated.length - 1] = { ...last, content: message, isError: true }
          return updated
        })
        setLoading(false)
        setAskPhase('idle')
        setStatusMsg('')
      },
    })
  }, [loading, mode, onConversationCreated])

  const handleSend = (e: React.FormEvent) => {
    e.preventDefault()
    const q = input.trim()
    if (!q) return
    setInput('')
    executeQuestion(q)
  }

  const handleRetry = () => {
    const q = lastQuestionRef.current
    if (q) executeQuestion(q, true)
  }

  return (
    <div className="flex flex-col h-[calc(100vh-8rem)]">
      {/* Message area */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-6 space-y-5">
        {messages.map((msg, i) => (
          <div key={i} className={`flex gap-3 ${msg.role === 'user' ? 'flex-row-reverse' : ''}`}>
            <div className={`w-7 h-7 rounded-lg flex items-center justify-center shrink-0 mt-0.5 ${
              msg.role === 'user'
                ? 'bg-k-accent/15 text-k-accent ring-1 ring-k-accent/20'
                : 'bg-k-sunk/80 text-k-ink3 ring-1 ring-k-line-strong/50'
            }`}>
              {msg.role === 'user' ? <Search className="w-3.5 h-3.5" /> : <BrainCircuit className="w-3.5 h-3.5" />}
            </div>
            <div className={`max-w-[85%] rounded-xl px-4 py-2.5 text-sm leading-relaxed ${
              msg.role === 'user'
                ? 'bg-k-accent/10 text-k-accent ring-1 ring-k-accent/15'
                : 'bg-k-sunk/30 ring-1 ring-k-line-strong/40 text-k-ink2'
            }`}>
              {/* Status indicator for streaming assistant message */}
              {msg.role === 'assistant' && !msg.content && loading && i === messages.length - 1 && (
                <div className="flex items-center gap-2 text-k-ink4 text-xs py-1">
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  <span>{statusMsg || '思考中...'}</span>
                </div>
              )}
              {msg.role === 'assistant' && msg.content ? (
                <div className="prose-chat">
                  <Markdown remarkPlugins={[remarkGfm]}>
                    {msg.content}
                  </Markdown>
                </div>
              ) : msg.role !== 'assistant' ? (
                <div className="whitespace-pre-wrap">{msg.content}</div>
              ) : null}
              {/* Auto-ingest notification */}
              {msg.ingestedId && (
                <div className="mt-2 flex items-center gap-1.5 text-[11px] text-k-ok/80">
                  <BookPlus className="w-3 h-3" />
                  <span>已自动写入知识库: {msg.ingestedId}</span>
                </div>
              )}
              {/* Retry button on error messages */}
              {msg.isError && (
                <button
                  onClick={handleRetry}
                  disabled={loading}
                  className="mt-2 flex items-center gap-1.5 text-xs text-k-accent hover:text-k-accent disabled:opacity-40 transition-colors"
                >
                  <RotateCcw className="w-3 h-3" />
                  重试
                </button>
              )}
              {msg.sources && msg.sources.length > 0 && (
                <div className="mt-2.5 pt-2.5 border-t border-k-line-strong/40 space-y-0.5">
                  <div className="text-[11px] font-medium text-k-ink4 mb-1.5">来源</div>
                  {msg.sources.map((s) => s.kind === 'artifact' ? (
                    // An artifact page (#6): open its share link, or the sandboxed local preview.
                    <a
                      key={s.id}
                      href={s.url || artifacts.previewUrl(s.id)}
                      target="_blank"
                      rel="noreferrer noopener"
                      className="flex items-center gap-1.5 w-full text-left text-[11px] text-k-accent/80 hover:text-k-accent hover:bg-k-muted/30 rounded px-1.5 py-1 -mx-1.5 transition-colors"
                    >
                      <span className="truncate">{s.title}</span>
                      <span className="text-k-ink4 shrink-0">（工件）</span>
                    </a>
                  ) : (
                    <button
                      key={s.id}
                      onClick={() => onPreviewEntry?.(s.id)}
                      className="flex items-center gap-1.5 w-full text-left text-[11px] text-k-accent/80 hover:text-k-accent hover:bg-k-muted/30 rounded px-1.5 py-1 -mx-1.5 transition-colors group"
                    >
                      <span className="truncate">{s.title}</span>
                      <span className="text-k-ink4 shrink-0 group-hover:text-k-ink4">({s.domain})</span>
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
        <div className="bg-k-surface/80 ring-1 ring-k-line-strong/60 rounded-2xl overflow-hidden focus-within:ring-k-accent/40 transition-all">
          <form onSubmit={handleSend}>
            <div className="flex items-center px-4 pt-3 pb-2">
              <input
                type="text"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder={mode === 'ask' ? '向知识图谱提问...' : '输入关键词进行语义检索...'}
                className="flex-1 bg-transparent text-k-ink text-sm placeholder:text-k-ink4 focus:outline-none"
              />
            </div>
            <div className="flex items-center justify-between px-3 pb-2.5">
              {/* Mode toggle pill */}
              <div className="flex items-center bg-k-sunk/80 rounded-lg p-0.5 ring-1 ring-k-line-strong/50">
                <button
                  type="button"
                  onClick={() => setMode('ask')}
                  className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium transition-all ${
                    mode === 'ask'
                      ? 'bg-k-muted/80 text-k-ink shadow-sm'
                      : 'text-k-ink4 hover:text-k-ink2'
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
                      ? 'bg-k-muted/80 text-k-ink shadow-sm'
                      : 'text-k-ink4 hover:text-k-ink2'
                  }`}
                >
                  <Search className="w-3 h-3" />
                  <span>检索</span>
                </button>
              </div>

              <button
                type="submit"
                disabled={!input.trim() || loading}
                className="flex items-center gap-1.5 px-3 py-1.5 bg-k-ink hover:bg-k-surface text-k-surface disabled:opacity-40 disabled:cursor-not-allowed rounded-lg text-xs font-medium transition-colors"
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
