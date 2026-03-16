import { useState } from 'react'
import { Play, CheckCircle2 } from 'lucide-react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { get, post } from '../api/client'
import type { QuizEntry, ScoreResult } from '../types'

type QuizState = 'setup' | 'loading' | 'playing' | 'finished'

interface Props {
  onPreviewEntry?: (entryId: string) => void
}

export default function QuizView({ onPreviewEntry }: Props) {
  const [quizState, setQuizState] = useState<QuizState>('setup')
  const [cards, setCards] = useState<QuizEntry[]>([])
  const [currentIndex, setCurrentIndex] = useState(0)
  const [isFlipped, setIsFlipped] = useState(false)
  const [domain, setDomain] = useState('algorithm')
  const [count, setCount] = useState(5)

  const startQuiz = async () => {
    setQuizState('loading')
    try {
      const params: Record<string, string> = { count: String(count) }
      if (domain !== 'all') params.domain = domain
      const entries = await get<QuizEntry[]>('/quiz/entries', params)
      if (entries.length === 0) {
        setQuizState('finished')
        return
      }
      setCards(entries)
      setCurrentIndex(0)
      setIsFlipped(false)
      setQuizState('playing')
    } catch {
      setQuizState('setup')
    }
  }

  const handleScore = async (response: 'confident' | 'partial' | 'forgot') => {
    const card = cards[currentIndex]
    // Fire and forget — update frontmatter in background
    post<ScoreResult>('/quiz/score', { file_path: card.file_path, response }).catch(() => {})

    setIsFlipped(false)
    if (currentIndex < cards.length - 1) {
      setCurrentIndex((prev) => prev + 1)
    } else {
      setQuizState('finished')
    }
  }

  if (quizState === 'setup' || quizState === 'loading') {
    return (
      <div className="max-w-md mx-auto mt-10 p-8 border border-zinc-800/80 bg-zinc-900/40 rounded-2xl">
        <div className="flex items-center gap-3 mb-6">
          <div className="p-2 bg-indigo-500/10 text-indigo-400 rounded-lg"><Play className="w-5 h-5" /></div>
          <h2 className="text-xl font-semibold text-zinc-100">配置测验任务</h2>
        </div>
        <div className="space-y-5">
          <div>
            <label className="block text-sm text-zinc-400 mb-2">选择知识域</label>
            <select value={domain} onChange={(e) => setDomain(e.target.value)} className="w-full bg-zinc-950 border border-zinc-800 rounded-lg p-2.5 text-zinc-300 outline-none focus:border-indigo-500 text-sm">
              <option value="all">全量域 (All)</option>
              <option value="algorithm">算法 (Algorithm)</option>
              <option value="golang">Golang</option>
            </select>
          </div>
          <div>
            <label className="block text-sm text-zinc-400 mb-2">抽取题目数量</label>
            <input type="number" value={count} onChange={(e) => setCount(Number(e.target.value))} className="w-full bg-zinc-950 border border-zinc-800 rounded-lg p-2.5 text-zinc-300 outline-none focus:border-indigo-500 text-sm" />
          </div>
          <div className="pt-4 border-t border-zinc-800 flex justify-between items-center">
            <span className="text-xs font-mono text-zinc-500">kg quiz --domain {domain} --count {count}</span>
            <button onClick={startQuiz} disabled={quizState === 'loading'} className="px-5 py-2 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white rounded-lg text-sm font-medium transition-colors">
              {quizState === 'loading' ? '加载中...' : '开始测验'}
            </button>
          </div>
        </div>
      </div>
    )
  }

  if (quizState === 'finished') {
    return (
      <div className="h-[60vh] flex flex-col items-center justify-center">
        <div className="w-16 h-16 rounded-full bg-emerald-500/10 flex items-center justify-center mb-6 border border-emerald-500/20">
          <CheckCircle2 className="w-8 h-8 text-emerald-500" />
        </div>
        <h2 className="text-2xl font-semibold text-zinc-100 mb-2">测验完成</h2>
        <p className="text-zinc-500 text-sm mb-8">复习间隔已根据 SM-2 算法自动更新至 Markdown 元数据中。</p>
        <button onClick={() => { setQuizState('setup'); setCurrentIndex(0) }} className="px-5 py-2.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-200 rounded-lg text-sm font-medium transition-colors">
          返回配置页
        </button>
      </div>
    )
  }

  const currentCard = cards[currentIndex]

  return (
    <div className="flex flex-col items-center justify-center py-4 h-[calc(100vh-12rem)]">
      <div className="w-full max-w-2xl h-full flex flex-col">
        <div className="flex items-center justify-between mb-6">
          <span className="text-xs font-mono text-zinc-500 bg-zinc-900 px-2 py-1 rounded">kg quiz -i</span>
          <span className="text-sm font-medium text-zinc-400">进度 {currentIndex + 1} / {cards.length}</span>
        </div>

        <div className="relative perspective-1000 flex-1">
          <div className={`w-full h-full absolute transition-all duration-500 transform-style-3d ${isFlipped ? 'rotate-y-180' : ''}`}>

            {/* Front (question) */}
            <div className={`absolute inset-0 backface-hidden flex flex-col bg-zinc-900/50 border border-zinc-800 rounded-2xl p-8 shadow-xl ${isFlipped ? 'invisible' : 'visible'}`}>
              <div className="flex gap-2 mb-6">
                <span className="px-2 py-1 bg-zinc-800 text-zinc-400 text-xs rounded-md">{currentCard.layer}</span>
                {currentCard.tags.map((t) => (
                  <span key={t} className="px-2 py-1 bg-indigo-500/10 text-indigo-400 text-xs rounded-md">{t}</span>
                ))}
              </div>
              <div className="flex-1 overflow-y-auto flex items-center justify-center">
                <div className="prose-chat text-center">
                  <Markdown remarkPlugins={[remarkGfm]}>{currentCard.question || currentCard.title}</Markdown>
                </div>
              </div>
              <div className="flex gap-3 mt-6">
                <button onClick={() => setIsFlipped(true)} className="flex-1 py-3.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-200 rounded-xl text-sm font-medium transition-colors">
                  显示答案
                </button>
                {onPreviewEntry && (
                  <button
                    onClick={() => onPreviewEntry(currentCard.id)}
                    className="px-4 py-3.5 text-xs text-indigo-400 hover:text-indigo-300 bg-indigo-500/10 hover:bg-indigo-500/15 rounded-xl transition-colors ring-1 ring-indigo-500/20"
                  >
                    查看原文
                  </button>
                )}
              </div>
            </div>

            {/* Back (answer + scoring) */}
            <div className={`absolute inset-0 backface-hidden rotate-y-180 flex flex-col bg-zinc-900/80 border border-zinc-700 rounded-2xl p-8 shadow-xl ${!isFlipped ? 'invisible' : 'visible'}`}>
              <div className="mb-4 text-sm font-medium text-zinc-500">{currentCard.title}</div>
              <div className="w-full h-px bg-zinc-800 mb-6"></div>
              <div className="flex-1 overflow-y-auto">
                <div className="prose-chat text-sm">
                  <Markdown remarkPlugins={[remarkGfm]}>{currentCard.answer}</Markdown>
                </div>
              </div>
              <div className="grid grid-cols-3 gap-4 mt-6">
                <button onClick={() => handleScore('forgot')} className="py-3 flex flex-col items-center gap-1 bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 rounded-xl transition-colors border border-rose-500/10">
                  <span className="text-sm font-medium">忘了 (Forgot)</span>
                  <span className="text-[10px] opacity-70">明天再复习</span>
                </button>
                <button onClick={() => handleScore('partial')} className="py-3 flex flex-col items-center gap-1 bg-amber-500/10 hover:bg-amber-500/20 text-amber-400 rounded-xl transition-colors border border-amber-500/10">
                  <span className="text-sm font-medium">模糊 (Partial)</span>
                  <span className="text-[10px] opacity-70">7天后复习</span>
                </button>
                <button onClick={() => handleScore('confident')} className="py-3 flex flex-col items-center gap-1 bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-400 rounded-xl transition-colors border border-emerald-500/10">
                  <span className="text-sm font-medium">记得 (Confident)</span>
                  <span className="text-[10px] opacity-70">30天后复习</span>
                </button>
              </div>
            </div>

          </div>
        </div>
      </div>
    </div>
  )
}
