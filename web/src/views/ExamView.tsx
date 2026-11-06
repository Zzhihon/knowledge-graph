import { useState, useEffect, useRef, useCallback } from 'react'
import { Timer, ChevronLeft, ChevronRight, CheckCircle2, Play } from 'lucide-react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { post } from '../api/client'
import type { ExamPaper, ScoreResult } from '../types'

type ExamState = 'setup' | 'playing' | 'review' | 'finished'

const DIFFICULTY_COLORS: Record<string, string> = {
  easy: 'text-k-ok bg-k-ok/10 border-k-ok/20',
  medium: 'text-k-amber-ink bg-k-amber/10 border-k-amber/20',
  hard: 'text-k-hot bg-k-hot/10 border-k-hot/20',
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
        <div className="w-16 h-16 rounded-full bg-k-accent/10 flex items-center justify-center mb-6 border border-k-accent/20">
          <Play className="w-8 h-8 text-k-accent" />
        </div>
        <h2 className="text-xl font-semibold text-k-ink mb-2">模拟面试</h2>
        <p className="text-k-ink4 text-sm mb-6">从题库页面点击「模拟面试」按钮生成套卷</p>
        <button
          onClick={() => setActiveTab?.('problems')}
          className="px-5 py-2.5 bg-k-sunk hover:bg-k-muted text-k-ink rounded-lg text-sm font-medium transition-colors"
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
          <div className="w-16 h-16 rounded-full bg-k-ok/10 flex items-center justify-center mb-4 border border-k-ok/20">
            <CheckCircle2 className="w-8 h-8 text-k-ok" />
          </div>
          <h2 className="text-2xl font-semibold text-k-ink mb-2">面试完成</h2>
          <p className="text-k-ink4 text-sm">SM-2 复习间隔已自动更新</p>
        </div>

        <div className="grid grid-cols-3 gap-4 mb-8">
          <div className="p-4 rounded-xl border border-k-ok/20 bg-k-ok/5 text-center">
            <div className="text-2xl font-bold text-k-ok">{confident}</div>
            <div className="text-xs text-k-ink4 mt-1">掌握</div>
          </div>
          <div className="p-4 rounded-xl border border-k-amber/20 bg-k-amber/5 text-center">
            <div className="text-2xl font-bold text-k-amber-ink">{partial}</div>
            <div className="text-xs text-k-ink4 mt-1">模糊</div>
          </div>
          <div className="p-4 rounded-xl border border-k-hot/20 bg-k-hot/5 text-center">
            <div className="text-2xl font-bold text-k-hot">{forgot}</div>
            <div className="text-xs text-k-ink4 mt-1">忘记</div>
          </div>
        </div>

        {/* Problem review list */}
        <div className="border border-k-line/80 rounded-xl overflow-hidden mb-6">
          {exam?.problems.map((p, i) => (
            <div
              key={p.id}
              className="flex items-center justify-between px-4 py-3 border-b border-k-line/40 last:border-b-0 hover:bg-k-surface/40 cursor-pointer transition-colors"
              onClick={() => onPreviewEntry?.(p.id)}
            >
              <div className="flex items-center gap-3">
                <span className="text-xs font-mono text-k-ink4 w-5">{i + 1}</span>
                <span className="text-sm text-k-ink">{p.title}</span>
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
            className="px-5 py-2.5 bg-k-sunk hover:bg-k-muted text-k-ink rounded-lg text-sm font-medium transition-colors"
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
          <Timer className={`w-4 h-4 ${remainingSeconds <= 300 ? 'text-k-hot' : 'text-k-ink3'}`} />
          <span className={`font-mono text-sm ${remainingSeconds <= 300 ? 'text-k-hot' : 'text-k-ink2'}`}>
            {formatTime(remainingSeconds)} 剩余
          </span>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-sm text-k-ink3">
            进度 {currentIndex + 1}/{exam.problems.length}
          </span>
          <div className="flex gap-1">
            {exam.problems.map((_, i) => (
              <div
                key={i}
                className={`w-2.5 h-2.5 rounded-full transition-colors cursor-pointer ${
                  i === currentIndex
                    ? 'bg-k-accent'
                    : scores[i]
                    ? scores[i] === 'confident' ? 'bg-k-ok' : scores[i] === 'partial' ? 'bg-k-amber' : 'bg-k-hot'
                    : 'bg-k-muted'
                }`}
                onClick={() => { setCurrentIndex(i); setShowSolution(false) }}
              />
            ))}
          </div>
        </div>
      </div>

      {/* Problem content */}
      <div className="flex-1 overflow-y-auto border border-k-line/80 rounded-xl bg-k-surface/30 p-6">
        <div className="flex items-center gap-3 mb-4">
          <span className="text-lg font-semibold text-k-ink">{currentProblem.title}</span>
          <span className={`px-2 py-0.5 text-xs rounded-md border ${DIFFICULTY_COLORS[currentProblem.difficulty] || ''}`}>
            {currentProblem.difficulty}
          </span>
          {currentProblem.pattern.map(pat => (
            <span key={pat} className="px-2 py-0.5 text-xs bg-k-violet/10 text-k-violet rounded-md">
              {pat}
            </span>
          ))}
          <span className="text-xs text-k-ink4 ml-auto">
            ~{currentProblem.time_estimate} min
          </span>
        </div>

        <div className="prose-chat text-sm">
          <Markdown remarkPlugins={[remarkGfm]}>{currentProblem.content}</Markdown>
        </div>

        {!showSolution && (
          <button
            onClick={() => setShowSolution(true)}
            className="mt-6 w-full py-3 bg-k-sunk hover:bg-k-muted text-k-ink rounded-xl text-sm font-medium transition-colors"
          >
            显示解答
          </button>
        )}

        {showSolution && (
          <div className="mt-6 pt-6 border-t border-k-line">
            <h3 className="text-sm font-medium text-k-ink3 mb-4">自我评估</h3>
            <div className="grid grid-cols-3 gap-4">
              <button
                onClick={() => handleScore('forgot')}
                className={`py-3 flex flex-col items-center gap-1 rounded-xl transition-colors border ${
                  scores[currentIndex] === 'forgot'
                    ? 'bg-k-hot/20 border-k-hot/40 text-k-hot'
                    : 'bg-k-hot/10 hover:bg-k-hot/20 text-k-hot border-k-hot/10'
                }`}
              >
                <span className="text-sm font-medium">忘了</span>
                <span className="text-[10px] opacity-70">明天再复习</span>
              </button>
              <button
                onClick={() => handleScore('partial')}
                className={`py-3 flex flex-col items-center gap-1 rounded-xl transition-colors border ${
                  scores[currentIndex] === 'partial'
                    ? 'bg-k-amber/20 border-k-amber/40 text-k-amber-ink'
                    : 'bg-k-amber/10 hover:bg-k-amber/20 text-k-amber-ink border-k-amber/10'
                }`}
              >
                <span className="text-sm font-medium">模糊</span>
                <span className="text-[10px] opacity-70">7天后复习</span>
              </button>
              <button
                onClick={() => handleScore('confident')}
                className={`py-3 flex flex-col items-center gap-1 rounded-xl transition-colors border ${
                  scores[currentIndex] === 'confident'
                    ? 'bg-k-ok/20 border-k-ok/40 text-k-ok'
                    : 'bg-k-ok/10 hover:bg-k-ok/20 text-k-ok border-k-ok/10'
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
          className="flex items-center gap-1 px-4 py-2.5 bg-k-surface border border-k-line rounded-lg text-sm text-k-ink2 disabled:opacity-30 hover:bg-k-sunk transition-colors"
        >
          <ChevronLeft className="w-4 h-4" /> 上一题
        </button>

        <button
          onClick={handleFinish}
          className="px-5 py-2.5 bg-k-accent hover:bg-k-accent text-k-accent-contrast rounded-lg text-sm font-medium transition-colors"
        >
          完成作答
        </button>

        <button
          onClick={handleNext}
          disabled={currentIndex >= exam.problems.length - 1}
          className="flex items-center gap-1 px-4 py-2.5 bg-k-surface border border-k-line rounded-lg text-sm text-k-ink2 disabled:opacity-30 hover:bg-k-sunk transition-colors"
        >
          下一题 <ChevronRight className="w-4 h-4" />
        </button>
      </div>
    </div>
  )
}

function ScoreBadge({ score }: { score?: string }) {
  if (!score) return <span className="text-xs text-k-ink4">未评</span>
  const map: Record<string, { label: string; cls: string }> = {
    confident: { label: '掌握', cls: 'text-k-ok bg-k-ok/10' },
    partial: { label: '模糊', cls: 'text-k-amber-ink bg-k-amber/10' },
    forgot: { label: '忘记', cls: 'text-k-hot bg-k-hot/10' },
  }
  const info = map[score]
  if (!info) return null
  return <span className={`px-2 py-0.5 text-xs rounded-md ${info.cls}`}>{info.label}</span>
}
