import { useState, useEffect, useRef, useCallback } from 'react'
import { Timer, ChevronLeft, ChevronRight, CheckCircle2, Play } from 'lucide-react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { post } from '../api/client'
import type { ExamPaper, ScoreResult } from '../types'

type ExamState = 'setup' | 'playing' | 'review' | 'finished'

const DIFFICULTY_COLORS: Record<string, string> = {
  easy: 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20',
  medium: 'text-amber-400 bg-amber-500/10 border-amber-500/20',
  hard: 'text-rose-400 bg-rose-500/10 border-rose-500/20',
}

interface Props {
  examData: ExamPaper | null
  onPreviewEntry?: (entryId: string) => void
  setActiveTab?: (tab: string) => void
}

export default function ExamView({ examData, onPreviewEntry, setActiveTab }: Props) {
  const [examState, setExamState] = useState<ExamState>(examData ? 'playing' : 'setup')
  const [exam, setExam] = useState<ExamPaper | null>(examData)
  const [currentIndex, setCurrentIndex] = useState(0)
  const [showSolution, setShowSolution] = useState(false)
  const [remainingSeconds, setRemainingSeconds] = useState(0)
  const [scores, setScores] = useState<Record<number, string>>({})
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // Initialize from examData prop
  useEffect(() => {
    if (examData && examData.problems.length > 0) {
      setExam(examData)
      setRemainingSeconds(examData.total_time * 60)
      setCurrentIndex(0)
      setShowSolution(false)
      setScores({})
      setExamState('playing')
    }
  }, [examData])

  // Timer
  useEffect(() => {
    if (examState === 'playing' && remainingSeconds > 0) {
      timerRef.current = setInterval(() => {
        setRemainingSeconds(prev => {
          if (prev <= 1) {
            if (timerRef.current) clearInterval(timerRef.current)
            return 0
          }
          return prev - 1
        })
      }, 1000)
    }
    return () => {
      if (timerRef.current) clearInterval(timerRef.current)
    }
  }, [examState, remainingSeconds])

  const formatTime = useCallback((seconds: number) => {
    const m = Math.floor(seconds / 60)
    const s = seconds % 60
    return `${m}:${s.toString().padStart(2, '0')}`
  }, [])

  const handleScore = async (response: 'confident' | 'partial' | 'forgot') => {
    if (!exam) return
    const problem = exam.problems[currentIndex]
    setScores(prev => ({ ...prev, [currentIndex]: response }))

    // Update SM-2 in background
    post<ScoreResult>('/quiz/score', { file_path: problem.file_path, response }).catch(() => {})
  }

  const handleFinish = () => {
    if (timerRef.current) clearInterval(timerRef.current)
    setExamState('finished')
  }

  const handleNext = () => {
    if (!exam) return
    setShowSolution(false)
    if (currentIndex < exam.problems.length - 1) {
      setCurrentIndex(prev => prev + 1)
    }
  }

  const handlePrev = () => {
    setShowSolution(false)
    if (currentIndex > 0) {
      setCurrentIndex(prev => prev - 1)
    }
  }

  // Setup: no exam data yet
  if (examState === 'setup') {
    return (
      <div className="h-[60vh] flex flex-col items-center justify-center">
        <div className="w-16 h-16 rounded-full bg-indigo-500/10 flex items-center justify-center mb-6 border border-indigo-500/20">
          <Play className="w-8 h-8 text-indigo-400" />
        </div>
        <h2 className="text-xl font-semibold text-zinc-100 mb-2">模拟面试</h2>
        <p className="text-zinc-500 text-sm mb-6">从题库页面点击「模拟面试」按钮生成套卷</p>
        <button
          onClick={() => setActiveTab?.('problems')}
          className="px-5 py-2.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-200 rounded-lg text-sm font-medium transition-colors"
        >
          前往题库
        </button>
      </div>
    )
  }

  // Finished state
  if (examState === 'finished') {
    const confident = Object.values(scores).filter(s => s === 'confident').length
    const partial = Object.values(scores).filter(s => s === 'partial').length
    const forgot = Object.values(scores).filter(s => s === 'forgot').length

    return (
      <div className="max-w-lg mx-auto mt-10">
        <div className="flex flex-col items-center mb-8">
          <div className="w-16 h-16 rounded-full bg-emerald-500/10 flex items-center justify-center mb-4 border border-emerald-500/20">
            <CheckCircle2 className="w-8 h-8 text-emerald-500" />
          </div>
          <h2 className="text-2xl font-semibold text-zinc-100 mb-2">面试完成</h2>
          <p className="text-zinc-500 text-sm">SM-2 复习间隔已自动更新</p>
        </div>

        <div className="grid grid-cols-3 gap-4 mb-8">
          <div className="p-4 rounded-xl border border-emerald-500/20 bg-emerald-500/5 text-center">
            <div className="text-2xl font-bold text-emerald-400">{confident}</div>
            <div className="text-xs text-zinc-500 mt-1">掌握</div>
          </div>
          <div className="p-4 rounded-xl border border-amber-500/20 bg-amber-500/5 text-center">
            <div className="text-2xl font-bold text-amber-400">{partial}</div>
            <div className="text-xs text-zinc-500 mt-1">模糊</div>
          </div>
          <div className="p-4 rounded-xl border border-rose-500/20 bg-rose-500/5 text-center">
            <div className="text-2xl font-bold text-rose-400">{forgot}</div>
            <div className="text-xs text-zinc-500 mt-1">忘记</div>
          </div>
        </div>

        {/* Problem review list */}
        <div className="border border-zinc-800/80 rounded-xl overflow-hidden mb-6">
          {exam?.problems.map((p, i) => (
            <div
              key={p.id}
              className="flex items-center justify-between px-4 py-3 border-b border-zinc-800/40 last:border-b-0 hover:bg-zinc-900/40 cursor-pointer transition-colors"
              onClick={() => onPreviewEntry?.(p.id)}
            >
              <div className="flex items-center gap-3">
                <span className="text-xs font-mono text-zinc-500 w-5">{i + 1}</span>
                <span className="text-sm text-zinc-200">{p.title}</span>
                <span className={`px-2 py-0.5 text-xs rounded-md border ${DIFFICULTY_COLORS[p.difficulty] || ''}`}>
                  {p.difficulty}
                </span>
              </div>
              <ScoreBadge score={scores[i]} />
            </div>
          ))}
        </div>

        <div className="flex gap-3 justify-center">
          <button
            onClick={() => setActiveTab?.('problems')}
            className="px-5 py-2.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-200 rounded-lg text-sm font-medium transition-colors"
          >
            返回题库
          </button>
        </div>
      </div>
    )
  }

  // Playing state
  if (!exam || exam.problems.length === 0) return null
  const currentProblem = exam.problems[currentIndex]

  return (
    <div className="flex flex-col h-[calc(100vh-12rem)]">
      {/* Top bar: timer + progress */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <Timer className={`w-4 h-4 ${remainingSeconds <= 300 ? 'text-rose-400' : 'text-zinc-400'}`} />
          <span className={`font-mono text-sm ${remainingSeconds <= 300 ? 'text-rose-400' : 'text-zinc-300'}`}>
            {formatTime(remainingSeconds)} 剩余
          </span>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-sm text-zinc-400">
            进度 {currentIndex + 1}/{exam.problems.length}
          </span>
          <div className="flex gap-1">
            {exam.problems.map((_, i) => (
              <div
                key={i}
                className={`w-2.5 h-2.5 rounded-full transition-colors cursor-pointer ${
                  i === currentIndex
                    ? 'bg-indigo-500'
                    : scores[i]
                    ? scores[i] === 'confident' ? 'bg-emerald-500' : scores[i] === 'partial' ? 'bg-amber-500' : 'bg-rose-500'
                    : 'bg-zinc-700'
                }`}
                onClick={() => { setCurrentIndex(i); setShowSolution(false) }}
              />
            ))}
          </div>
        </div>
      </div>

      {/* Problem content */}
      <div className="flex-1 overflow-y-auto border border-zinc-800/80 rounded-xl bg-zinc-900/30 p-6">
        <div className="flex items-center gap-3 mb-4">
          <span className="text-lg font-semibold text-zinc-100">{currentProblem.title}</span>
          <span className={`px-2 py-0.5 text-xs rounded-md border ${DIFFICULTY_COLORS[currentProblem.difficulty] || ''}`}>
            {currentProblem.difficulty}
          </span>
          {currentProblem.pattern.map(pat => (
            <span key={pat} className="px-2 py-0.5 text-xs bg-violet-500/10 text-violet-400 rounded-md">
              {pat}
            </span>
          ))}
          <span className="text-xs text-zinc-500 ml-auto">
            ~{currentProblem.time_estimate} min
          </span>
        </div>

        <div className="prose-chat text-sm">
          <Markdown remarkPlugins={[remarkGfm]}>{currentProblem.content}</Markdown>
        </div>

        {!showSolution && (
          <button
            onClick={() => setShowSolution(true)}
            className="mt-6 w-full py-3 bg-zinc-800 hover:bg-zinc-700 text-zinc-200 rounded-xl text-sm font-medium transition-colors"
          >
            显示解答
          </button>
        )}

        {showSolution && (
          <div className="mt-6 pt-6 border-t border-zinc-800">
            <h3 className="text-sm font-medium text-zinc-400 mb-4">自我评估</h3>
            <div className="grid grid-cols-3 gap-4">
              <button
                onClick={() => handleScore('forgot')}
                className={`py-3 flex flex-col items-center gap-1 rounded-xl transition-colors border ${
                  scores[currentIndex] === 'forgot'
                    ? 'bg-rose-500/20 border-rose-500/40 text-rose-300'
                    : 'bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 border-rose-500/10'
                }`}
              >
                <span className="text-sm font-medium">忘了</span>
                <span className="text-[10px] opacity-70">明天再复习</span>
              </button>
              <button
                onClick={() => handleScore('partial')}
                className={`py-3 flex flex-col items-center gap-1 rounded-xl transition-colors border ${
                  scores[currentIndex] === 'partial'
                    ? 'bg-amber-500/20 border-amber-500/40 text-amber-300'
                    : 'bg-amber-500/10 hover:bg-amber-500/20 text-amber-400 border-amber-500/10'
                }`}
              >
                <span className="text-sm font-medium">模糊</span>
                <span className="text-[10px] opacity-70">7天后复习</span>
              </button>
              <button
                onClick={() => handleScore('confident')}
                className={`py-3 flex flex-col items-center gap-1 rounded-xl transition-colors border ${
                  scores[currentIndex] === 'confident'
                    ? 'bg-emerald-500/20 border-emerald-500/40 text-emerald-300'
                    : 'bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-400 border-emerald-500/10'
                }`}
              >
                <span className="text-sm font-medium">记得</span>
                <span className="text-[10px] opacity-70">30天后复习</span>
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Navigation bar */}
      <div className="flex items-center justify-between mt-4">
        <button
          onClick={handlePrev}
          disabled={currentIndex === 0}
          className="flex items-center gap-1 px-4 py-2.5 bg-zinc-900 border border-zinc-800 rounded-lg text-sm text-zinc-300 disabled:opacity-30 hover:bg-zinc-800 transition-colors"
        >
          <ChevronLeft className="w-4 h-4" /> 上一题
        </button>

        <button
          onClick={handleFinish}
          className="px-5 py-2.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-sm font-medium transition-colors"
        >
          完成作答
        </button>

        <button
          onClick={handleNext}
          disabled={currentIndex >= exam.problems.length - 1}
          className="flex items-center gap-1 px-4 py-2.5 bg-zinc-900 border border-zinc-800 rounded-lg text-sm text-zinc-300 disabled:opacity-30 hover:bg-zinc-800 transition-colors"
        >
          下一题 <ChevronRight className="w-4 h-4" />
        </button>
      </div>
    </div>
  )
}

function ScoreBadge({ score }: { score?: string }) {
  if (!score) return <span className="text-xs text-zinc-600">未评</span>
  const map: Record<string, { label: string; cls: string }> = {
    confident: { label: '掌握', cls: 'text-emerald-400 bg-emerald-500/10' },
    partial: { label: '模糊', cls: 'text-amber-400 bg-amber-500/10' },
    forgot: { label: '忘记', cls: 'text-rose-400 bg-rose-500/10' },
  }
  const info = map[score]
  if (!info) return null
  return <span className={`px-2 py-0.5 text-xs rounded-md ${info.cls}`}>{info.label}</span>
}
