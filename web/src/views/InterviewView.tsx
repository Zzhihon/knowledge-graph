import { useState, useEffect, useCallback } from 'react'
import {
  Briefcase, Loader2, ChevronLeft, ChevronRight, Search, Filter,
  Sparkles, Play, ArrowLeft, Eye, Check, X as XIcon, StopCircle,
} from 'lucide-react'
import { get, post } from '../api/client'
import type {
  InterviewQuestion, InterviewListResponse, InterviewStats,
  InterviewCategoryInfo,
} from '../types'
import type { InterviewGenState, InterviewGenActions } from '../hooks/useInterviewGenerate'

interface Props {
  onPreviewEntry?: (entryId: string) => void
  genState: InterviewGenState
  genActions: InterviewGenActions
}

const DIFFICULTY_COLORS: Record<string, string> = {
  easy: 'text-k-ok bg-k-ok/10 border-k-ok/20',
  medium: 'text-k-amber-ink bg-k-amber/10 border-k-amber/20',
  hard: 'text-k-hot bg-k-hot/10 border-k-hot/20',
}

const CATEGORY_ICONS: Record<string, string> = {
  fundamentals: '🔬',
  'tech-choices': '⚖️',
  'real-scenarios': '🛠️',
  'project-deep-dive': '🔍',
}

type ViewMode = 'browse' | 'practice'

interface PracticeCard {
  question: InterviewQuestion
  content: string
}

export default function InterviewView({ onPreviewEntry, genState, genActions }: Props) {
  const [mode, setMode] = useState<ViewMode>('browse')
  const [questions, setQuestions] = useState<InterviewQuestion[]>([])
  const [simulationSets, setSimulationSets] = useState<InterviewQuestion[]>([])
  const [simulationTotal, setSimulationTotal] = useState(0)
  const [stats, setStats] = useState<InterviewStats | null>(null)
  const [categories, setCategories] = useState<InterviewCategoryInfo[]>([])
  const [loading, setLoading] = useState(true)

  // Filters
  const [filterCategory, setFilterCategory] = useState('')
  const [filterProject, setFilterProject] = useState('')
  const [filterDifficulty, setFilterDifficulty] = useState('')
  const [filterTag, setFilterTag] = useState('')
  const [searchQuery, setSearchQuery] = useState('')
  const [page, setPage] = useState(1)
  const [totalPages, setTotalPages] = useState(1)
  const [total, setTotal] = useState(0)

  // Skill domains from config
  const [skillDomains, setSkillDomains] = useState<Array<{ key: string; label: string; icon: string; sub_domains: string[] }>>([])

  // Generate modal state (local — only controls the config dialog)
  const [showGenerate, setShowGenerate] = useState(false)
  const [genCategory, setGenCategory] = useState('')
  const [genProject, setGenProject] = useState('')
  const [genSkillDomain, setGenSkillDomain] = useState('')
  const [genFocusTopic, setGenFocusTopic] = useState('')
  const [genCount, setGenCount] = useState(5)

  // Practice state
  const [practiceCards, setPracticeCards] = useState<PracticeCard[]>([])
  const [practiceIndex, setPracticeIndex] = useState(0)
  const [showAnswer, setShowAnswer] = useState(false)
  const [practiceResults, setPracticeResults] = useState<Array<{ id: string; score: number }>>([])
  const [practiceSetup, setPracticeSetup] = useState(true)
  const [practiceCategory, setPracticeCategory] = useState('')
  const [practiceProject, setPracticeProject] = useState('')
  const [practiceCount, setPracticeCount] = useState(10)

  const fetchData = useCallback(async () => {
    setLoading(true)
    try {
      const params: Record<string, string> = {
        page: String(page),
        page_size: '15',
      }
      if (filterCategory) params.category = filterCategory
      if (filterProject) params.project = filterProject
      if (filterDifficulty) params.difficulty = filterDifficulty
      if (filterTag) params.tag = filterTag
      if (searchQuery) params.search = searchQuery

      const [questionsRes, categoriesRes, statsRes, domainsRes, simulationRes] = await Promise.all([
        get<InterviewListResponse>('/interview/questions', params),
        get<InterviewCategoryInfo[]>('/interview/categories'),
        get<InterviewStats>('/interview/stats'),
        get<Array<{ key: string; label: string; icon: string; sub_domains: string[] }>>('/interview/domains'),
        get<InterviewListResponse>('/interview/questions', {
          page: '1',
          page_size: '50',
          tag: 'mock-simulation',
        }),
      ])
      setQuestions(questionsRes.items)
      setTotalPages(questionsRes.total_pages)
      setTotal(questionsRes.total)
      setCategories(categoriesRes)
      setStats(statsRes)
      setSkillDomains(domainsRes)
      setSimulationSets(simulationRes.items)
      setSimulationTotal(simulationRes.total)
    } catch {
      // backend may not be up
    } finally {
      setLoading(false)
    }
  }, [page, filterCategory, filterProject, filterDifficulty, filterTag, searchQuery])

  useEffect(() => { fetchData() }, [fetchData])

  // Auto-refresh when background generation completes
  useEffect(() => {
    if (genState.phase === 'complete') {
      fetchData()
    }
  }, [genState.phase]) // eslint-disable-line react-hooks/exhaustive-deps

  // --- Trigger background generation ---
  const handleGenerate = () => {
    genActions.start({
      category: genCategory || null,
      project: genProject || null,
      skill_domain: genSkillDomain || null,
      focus_topic: genFocusTopic.trim() || null,
      count: genCount,
    })
    setShowGenerate(false) // close modal immediately — runs in background
  }

  // --- Practice mode ---
  const startPractice = async () => {
    const params: Record<string, string> = { page_size: String(practiceCount) }
    if (practiceCategory) params.category = practiceCategory
    if (practiceProject) params.project = practiceProject

    try {
      const res = await get<InterviewListResponse>('/interview/questions', params)
      if (res.items.length === 0) return

      const shuffled = [...res.items].sort(() => Math.random() - 0.5)
      const cards: PracticeCard[] = shuffled.map(q => ({ question: q, content: '' }))
      setPracticeCards(cards)
      setPracticeIndex(0)
      setShowAnswer(false)
      setPracticeResults([])
      setPracticeSetup(false)
    } catch { /* ignore */ }
  }

  const handleScore = async (score: number) => {
    const card = practiceCards[practiceIndex]
    if (!card) return

    const confidenceMap: Record<number, number> = { 1: 0.2, 2: 0.5, 3: 0.9 }
    const confidence = confidenceMap[score] || 0.5

    try {
      await post('/quiz/score', {
        file_path: card.question.file_path,
        score: confidence,
      })
    } catch { /* ignore */ }

    setPracticeResults(prev => [...prev, { id: card.question.id, score }])

    if (practiceIndex < practiceCards.length - 1) {
      setPracticeIndex(i => i + 1)
      setShowAnswer(false)
    }
  }

  const practiceFinished = practiceResults.length === practiceCards.length && practiceCards.length > 0
  const projectOptions = stats ? Object.keys(stats.project_distribution) : []
  const tagOptions = stats
    ? Object.entries(stats.tag_distribution)
        .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
        .map(([tag]) => tag)
    : []

  // ==================== Generation progress banner ====================
  const genBanner = genState.phase !== 'idle' && (
    <div className={`rounded-xl border p-4 ${
      genState.phase === 'generating'
        ? 'border-k-accent/30 bg-k-accent-soft'
        : genState.summary && genState.summary.total_failed > 0
        ? 'border-k-amber/30 bg-k-amber-soft'
        : 'border-k-ok/30 bg-k-ok-soft'
    }`}>
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2 text-sm">
          {genState.phase === 'generating' ? (
            <>
              <Loader2 className="w-4 h-4 animate-spin text-k-accent" />
              <span className="text-k-accent font-medium">
                生成中 {genState.results.length}/{genState.totalExpected}
              </span>
              {genState.model && (
                <span className="text-k-ink4 text-xs">({genState.model})</span>
              )}
            </>
          ) : (
            <>
              <Check className="w-4 h-4 text-k-ok" />
              <span className="text-k-ok font-medium">
                生成完成: {genState.summary?.total_created ?? genState.results.length} 题
                {(genState.summary?.total_failed ?? 0) > 0 && (
                  <span className="text-k-hot ml-1">({genState.summary?.total_failed} 失败)</span>
                )}
              </span>
            </>
          )}
        </div>
        <div className="flex items-center gap-2">
          {genState.phase === 'generating' && (
            <button onClick={genActions.stop}
              className="flex items-center gap-1 px-2.5 py-1 text-xs bg-k-sunk hover:bg-k-muted text-k-ink2 rounded-lg transition-colors">
              <StopCircle className="w-3.5 h-3.5" /> 停止
            </button>
          )}
          {genState.phase === 'complete' && (
            <button onClick={genActions.dismiss}
              className="text-k-ink4 hover:text-k-ink2 text-xs transition-colors">
              关闭
            </button>
          )}
        </div>
      </div>

      {/* Progress bar */}
      {genState.totalExpected > 0 && (
        <div className="w-full h-1.5 bg-k-sunk rounded-full mb-2">
          <div
            className={`h-1.5 rounded-full transition-all duration-300 ${
              genState.phase === 'generating' ? 'bg-k-accent' : 'bg-k-ok'
            }`}
            style={{ width: `${Math.round((genState.results.length / genState.totalExpected) * 100)}%` }}
          />
        </div>
      )}

      {/* Recent results (last 5) */}
      <div className="space-y-0.5 max-h-28 overflow-y-auto">
        {genState.results.slice(-5).map((r, i) => (
          <div key={i} className="text-xs text-k-ink3 flex items-center gap-1.5">
            <span className="text-k-ok">✓</span>
            <span className="truncate">{r.title}</span>
            <span className={`px-1 py-0.5 rounded text-[10px] ${DIFFICULTY_COLORS[r.difficulty] || ''}`}>
              {r.difficulty}
            </span>
          </div>
        ))}
        {genState.errors.slice(-3).map((e, i) => (
          <div key={`e${i}`} className="text-xs text-k-hot">✗ {e}</div>
        ))}
      </div>
    </div>
  )

  // === PRACTICE MODE ===
  if (mode === 'practice') {
    if (practiceSetup) {
      return (
        <div className="space-y-6">
          <div className="flex items-center gap-3">
            <button onClick={() => setMode('browse')} className="p-2 rounded-lg hover:bg-k-sunk text-k-ink3 transition-colors">
              <ArrowLeft className="w-5 h-5" />
            </button>
            <div className="p-2 bg-k-accent/10 text-k-accent rounded-lg">
              <Play className="w-5 h-5" />
            </div>
            <div>
              <h1 className="text-xl font-semibold text-k-ink">面试练习</h1>
              <p className="text-sm text-k-ink4">翻卡练习，评估置信度</p>
            </div>
          </div>

          {genBanner}

          <div className="max-w-md mx-auto space-y-4 mt-8">
            <div>
              <label className="block text-sm text-k-ink3 mb-1">分类</label>
              <select value={practiceCategory} onChange={e => setPracticeCategory(e.target.value)}
                className="w-full bg-k-bg border border-k-line rounded-lg px-3 py-2 text-sm text-k-ink2 outline-none focus:border-k-accent">
                <option value="">所有分类</option>
                {categories.map(c => <option key={c.key} value={c.key}>{c.label} ({c.question_count})</option>)}
              </select>
            </div>
            <div>
              <label className="block text-sm text-k-ink3 mb-1">项目</label>
              <select value={practiceProject} onChange={e => setPracticeProject(e.target.value)}
                className="w-full bg-k-bg border border-k-line rounded-lg px-3 py-2 text-sm text-k-ink2 outline-none focus:border-k-accent">
                <option value="">所有项目</option>
                {projectOptions.map(p => <option key={p} value={p}>{p}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-sm text-k-ink3 mb-1">题数</label>
              <input type="number" min={1} max={50} value={practiceCount} onChange={e => setPracticeCount(Number(e.target.value))}
                className="w-full bg-k-bg border border-k-line rounded-lg px-3 py-2 text-sm text-k-ink2 outline-none focus:border-k-accent" />
            </div>
            <button onClick={startPractice} disabled={!stats || stats.total_questions === 0}
              className="w-full mt-4 py-3 bg-k-accent hover:bg-k-accent disabled:opacity-50 text-k-accent-contrast rounded-lg text-sm font-medium transition-colors">
              开始练习
            </button>
          </div>
        </div>
      )
    }

    if (practiceFinished) {
      const remembered = practiceResults.filter(r => r.score === 3).length
      const fuzzy = practiceResults.filter(r => r.score === 2).length
      const forgot = practiceResults.filter(r => r.score === 1).length

      return (
        <div className="space-y-6">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-k-ok/10 text-k-ok rounded-lg">
              <Check className="w-5 h-5" />
            </div>
            <div>
              <h1 className="text-xl font-semibold text-k-ink">练习完成</h1>
              <p className="text-sm text-k-ink4">共 {practiceCards.length} 题</p>
            </div>
          </div>

          <div className="grid grid-cols-3 gap-4 max-w-lg mx-auto">
            <div className="p-4 rounded-xl border border-k-ok/40 bg-k-ok-soft text-center">
              <div className="text-2xl font-bold text-k-ok">{remembered}</div>
              <div className="text-xs text-k-ink3 mt-1">记得</div>
            </div>
            <div className="p-4 rounded-xl border border-k-amber/40 bg-k-amber-soft text-center">
              <div className="text-2xl font-bold text-k-amber-ink">{fuzzy}</div>
              <div className="text-xs text-k-ink3 mt-1">模糊</div>
            </div>
            <div className="p-4 rounded-xl border border-k-hot/40 bg-k-hot-soft text-center">
              <div className="text-2xl font-bold text-k-hot">{forgot}</div>
              <div className="text-xs text-k-ink3 mt-1">忘了</div>
            </div>
          </div>

          <div className="flex justify-center gap-3 mt-6">
            <button onClick={() => { setPracticeSetup(true); setPracticeResults([]) }}
              className="px-4 py-2 bg-k-sunk hover:bg-k-muted text-k-ink2 rounded-lg text-sm transition-colors">
              再来一轮
            </button>
            <button onClick={() => { setMode('browse'); setPracticeSetup(true); setPracticeResults([]) }}
              className="px-4 py-2 bg-k-accent hover:bg-k-accent text-k-accent-contrast rounded-lg text-sm transition-colors">
              返回题库
            </button>
          </div>
        </div>
      )
    }

    // Playing phase — flip card
    const card = practiceCards[practiceIndex]
    if (!card) return null

    return (
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <button onClick={() => { setMode('browse'); setPracticeSetup(true); setPracticeResults([]) }}
              className="p-2 rounded-lg hover:bg-k-sunk text-k-ink3 transition-colors">
              <ArrowLeft className="w-5 h-5" />
            </button>
            <span className="text-sm text-k-ink3">
              {practiceIndex + 1} / {practiceCards.length}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <span className={`px-2 py-0.5 text-xs rounded-md border ${DIFFICULTY_COLORS[card.question.difficulty] || ''}`}>
              {card.question.difficulty}
            </span>
            <span className="px-2 py-0.5 text-xs bg-k-sunk text-k-ink3 rounded-md">
              {card.question.category}
            </span>
          </div>
        </div>

        <div className="w-full h-1 bg-k-sunk rounded-full">
          <div className="h-1 bg-k-accent rounded-full transition-all"
            style={{ width: `${((practiceIndex + 1) / practiceCards.length) * 100}%` }} />
        </div>

        <div className="border border-k-line rounded-2xl overflow-hidden">
          <div className="p-6">
            <div className="text-lg font-medium text-k-ink mb-4">{card.question.title}</div>
            <div className="flex flex-wrap gap-1.5 mb-4">
              {(Array.isArray(card.question.domain) ? card.question.domain : [card.question.domain]).filter(Boolean).map(d => (
                <span key={d as string} className="px-2 py-0.5 text-xs bg-k-sunk text-k-ink3 rounded">{d as string}</span>
              ))}
              {card.question.tags.map(t => (
                <span key={t} className="px-2 py-0.5 text-xs bg-k-surface text-k-ink4 rounded">{t}</span>
              ))}
            </div>
            {card.question.project && (
              <div className="text-xs text-k-ink4">项目: {card.question.project}</div>
            )}
          </div>

          {!showAnswer && (
            <button onClick={() => setShowAnswer(true)}
              className="w-full py-4 border-t border-k-line bg-k-surface/40 hover:bg-k-sunk/60 text-k-ink2 text-sm font-medium flex items-center justify-center gap-2 transition-colors">
              <Eye className="w-4 h-4" /> 查看答案
            </button>
          )}

          {showAnswer && (
            <div className="border-t border-k-line p-6 bg-k-surface/20">
              <div className="text-xs text-k-ink4 mb-3 font-medium">
                框架: {card.question.answer_framework}
              </div>
              <div className="text-sm text-k-ink2 whitespace-pre-wrap">
                点击下方链接查看完整答案
              </div>

              <div className="flex gap-3 mt-6">
                <button onClick={() => handleScore(1)}
                  className="flex-1 py-3 rounded-xl border border-k-hot/40 bg-k-hot-soft text-k-hot text-sm font-medium hover:bg-k-hot-soft transition-colors">
                  忘了
                </button>
                <button onClick={() => handleScore(2)}
                  className="flex-1 py-3 rounded-xl border border-k-amber/40 bg-k-amber-soft text-k-amber-ink text-sm font-medium hover:bg-k-amber-soft transition-colors">
                  模糊
                </button>
                <button onClick={() => handleScore(3)}
                  className="flex-1 py-3 rounded-xl border border-k-ok/40 bg-k-ok-soft text-k-ok text-sm font-medium hover:bg-k-ok-soft transition-colors">
                  记得
                </button>
              </div>
            </div>
          )}
        </div>

        {showAnswer && (
          <button onClick={() => onPreviewEntry?.(card.question.id)}
            className="w-full text-center text-xs text-k-accent hover:text-k-accent transition-colors">
            点击查看完整条目详情
          </button>
        )}
      </div>
    )
  }

  // === BROWSE MODE ===

  if (loading && !stats) {
    return (
      <div className="h-[60vh] flex items-center justify-center">
        <Loader2 className="w-6 h-6 animate-spin text-k-ink4" />
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="p-2 bg-k-violet/10 text-k-violet rounded-lg">
            <Briefcase className="w-5 h-5" />
          </div>
          <div>
            <h1 className="text-xl font-semibold text-k-ink">面试题库</h1>
            <p className="text-sm text-k-ink4">基于简历的面试题目管理与练习</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowGenerate(true)}
            disabled={genState.phase === 'generating'}
            className="flex items-center gap-2 px-4 py-2.5 bg-k-sunk hover:bg-k-muted disabled:opacity-50 text-k-ink2 rounded-lg text-sm font-medium transition-colors"
          >
            {genState.phase === 'generating'
              ? <Loader2 className="w-4 h-4 animate-spin" />
              : <Sparkles className="w-4 h-4" />}
            {genState.phase === 'generating' ? '生成中...' : '生成题目'}
          </button>
          <button onClick={() => { setMode('practice'); setPracticeSetup(true) }}
            disabled={!stats || stats.total_questions === 0}
            className="flex items-center gap-2 px-4 py-2.5 bg-k-accent hover:bg-k-accent disabled:opacity-50 text-k-accent-contrast rounded-lg text-sm font-medium transition-colors">
            <Play className="w-4 h-4" /> 开始练习
          </button>
        </div>
      </div>

      {/* Generation progress banner — persistent across tab switches */}
      {genBanner}

      {/* Stats Cards */}
      {stats && (
        <div className="grid grid-cols-4 gap-4">
          <StatCard label="总题数" value={stats.total_questions} />
          <StatCard label="分类覆盖" value={`${Object.keys(stats.category_distribution).length}/${categories.length}`} />
          <StatCard
            label="难度分布"
            value={`E${stats.difficulty_distribution.easy || 0} M${stats.difficulty_distribution.medium || 0} H${stats.difficulty_distribution.hard || 0}`}
          />
          <StatCard label="待复习" value={stats.needs_review} accent={stats.needs_review > 0} />
        </div>
      )}

      {/* Category Cards */}
      <div>
        <h2 className="text-sm font-medium text-k-ink3 mb-3">题目分类</h2>
        <div className="grid grid-cols-4 gap-3">
          {categories.map((c) => (
            <div
              key={c.key}
              onClick={() => { setFilterCategory(filterCategory === c.key ? '' : c.key); setFilterTag(''); setPage(1) }}
              className={`p-3 rounded-xl border transition-colors cursor-pointer ${
                filterCategory === c.key
                  ? 'border-k-accent/50 bg-k-accent/5'
                  : 'border-k-line bg-k-surface/40 hover:border-k-line-strong'
              }`}
            >
              <div className="flex items-center justify-between mb-1">
                <span className="text-sm font-medium text-k-ink">
                  {CATEGORY_ICONS[c.key] || '📋'} {c.label}
                </span>
                <span className="text-xs text-k-ink4">{c.question_count} 题</span>
              </div>
              <div className="text-xs text-k-ink4 line-clamp-1">{c.description}</div>
            </div>
          ))}
          {simulationSets.length > 0 && (
            <div
              onClick={() => {
                if (filterTag === 'mock-simulation') {
                  setFilterTag('')
                } else {
                  setFilterTag('mock-simulation')
                  setFilterCategory('')
                }
                setPage(1)
              }}
              className={`p-3 rounded-xl border transition-colors cursor-pointer ${
                filterTag === 'mock-simulation'
                  ? 'border-k-accent/50 bg-k-accent/5'
                  : 'border-k-line bg-k-surface/40 hover:border-k-line-strong'
              }`}
            >
              <div className="flex items-center justify-between mb-1">
                <span className="text-sm font-medium text-k-ink">
                  🎯 面试模拟
                </span>
                <span className="text-xs text-k-ink4">{simulationTotal} 题</span>
              </div>
              <div className="text-xs text-k-ink4 line-clamp-1">高频大厂非算法拷打题</div>
            </div>
          )}
        </div>
      </div>

      {/* Special topic section — only shown when mock-simulation filter is active */}
      {simulationSets.length > 0 && filterTag === 'mock-simulation' && (
        <div>
          <div className="flex items-center justify-between mb-3">
            <div>
              <h2 className="text-sm font-medium text-k-ink2">专题区 · 面试模拟</h2>
              <p className="text-xs text-k-ink4 mt-1">
                基于当前简历与关联源码仓库整理的高频大厂非算法拷打题，答案内附源码依据。
              </p>
            </div>
            <button
              onClick={() => {
                setFilterTag(filterTag === 'mock-simulation' ? '' : 'mock-simulation')
                setPage(1)
              }}
              className={`px-3 py-1.5 text-xs rounded-lg border transition-colors ${
                filterTag === 'mock-simulation'
                  ? 'border-k-accent/40 bg-k-accent/10 text-k-accent'
                  : 'border-k-line bg-k-surface/40 text-k-ink3 hover:border-k-line-strong hover:text-k-ink2'
              }`}
            >
              {filterTag === 'mock-simulation' ? '取消只看专题' : '只看专题'}
            </button>
          </div>

          <div className="grid grid-cols-2 gap-3">
            {simulationSets.map((q) => (
              <button
                key={q.id}
                onClick={() => onPreviewEntry?.(q.id)}
                className="text-left p-4 rounded-xl border border-k-line bg-k-surface/40 hover:border-k-accent/40 hover:bg-k-surface/70 transition-colors"
              >
                <div className="flex items-start justify-between gap-3 mb-2">
                  <div className="text-sm font-medium text-k-ink leading-6">{q.title}</div>
                  <span className={`shrink-0 px-2 py-0.5 text-[11px] rounded-md border ${DIFFICULTY_COLORS[q.difficulty] || ''}`}>
                    {q.difficulty}
                  </span>
                </div>
                <div className="text-xs text-k-ink4 mb-3">
                  {categories.find(c => c.key === q.category)?.label || q.category}
                  {q.project ? ` · ${q.project}` : ''}
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {q.tags.filter(t => t !== 'mock-simulation').slice(0, 4).map(tag => (
                    <span key={tag} className="px-2 py-0.5 text-[11px] bg-k-sunk text-k-ink3 rounded">
                      {tag}
                    </span>
                  ))}
                </div>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Filters */}
      <div className="flex items-center gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-k-ink4" />
          <input
            type="text"
            placeholder="搜索题目..."
            value={searchQuery}
            onChange={(e) => { setSearchQuery(e.target.value); setPage(1) }}
            className="w-full pl-10 pr-4 py-2 bg-k-bg border border-k-line rounded-lg text-sm text-k-ink2 outline-none focus:border-k-accent placeholder:text-k-ink4"
          />
        </div>
        <div className="flex items-center gap-2">
          <Filter className="w-4 h-4 text-k-ink4" />
          <select value={filterCategory} onChange={(e) => { setFilterCategory(e.target.value); setPage(1) }}
            className="bg-k-bg border border-k-line rounded-lg px-3 py-2 text-sm text-k-ink2 outline-none focus:border-k-accent">
            <option value="">所有分类</option>
            {categories.map(c => <option key={c.key} value={c.key}>{c.label}</option>)}
          </select>
          <select value={filterProject} onChange={(e) => { setFilterProject(e.target.value); setPage(1) }}
            className="bg-k-bg border border-k-line rounded-lg px-3 py-2 text-sm text-k-ink2 outline-none focus:border-k-accent">
            <option value="">所有项目</option>
            {projectOptions.map(p => <option key={p} value={p}>{p}</option>)}
          </select>
          <select value={filterDifficulty} onChange={(e) => { setFilterDifficulty(e.target.value); setPage(1) }}
            className="bg-k-bg border border-k-line rounded-lg px-3 py-2 text-sm text-k-ink2 outline-none focus:border-k-accent">
            <option value="">所有难度</option>
            <option value="easy">Easy</option>
            <option value="medium">Medium</option>
            <option value="hard">Hard</option>
          </select>
          <select value={filterTag} onChange={(e) => { setFilterTag(e.target.value); setPage(1) }}
            className="bg-k-bg border border-k-line rounded-lg px-3 py-2 text-sm text-k-ink2 outline-none focus:border-k-accent">
            <option value="">所有标签</option>
            {tagOptions.map(tag => <option key={tag} value={tag}>{tag}</option>)}
          </select>
        </div>
      </div>

      {/* Question Table */}
      <div className="border border-k-line/80 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-k-line/80 bg-k-surface/40">
              <th className="text-left px-4 py-3 text-k-ink4 font-medium">标题</th>
              <th className="text-left px-4 py-3 text-k-ink4 font-medium w-24">分类</th>
              <th className="text-left px-4 py-3 text-k-ink4 font-medium w-32">项目</th>
              <th className="text-left px-4 py-3 text-k-ink4 font-medium w-20">难度</th>
              <th className="text-right px-4 py-3 text-k-ink4 font-medium w-20">置信度</th>
            </tr>
          </thead>
          <tbody>
            {questions.length === 0 ? (
              <tr>
                <td colSpan={5} className="text-center py-12 text-k-ink4">
                  {loading ? '加载中...' : '暂无题目，点击「生成题目」开始'}
                </td>
              </tr>
            ) : (
              questions.map((q) => (
                <tr
                  key={q.id}
                  className="border-b border-k-line/40 hover:bg-k-surface/40 cursor-pointer transition-colors"
                  onClick={() => onPreviewEntry?.(q.id)}
                >
                  <td className="px-4 py-3 text-k-ink">{q.title}</td>
                  <td className="px-4 py-3">
                    <span className="px-1.5 py-0.5 text-xs bg-k-sunk text-k-ink3 rounded">
                      {categories.find(c => c.key === q.category)?.label || q.category}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-xs text-k-ink4 truncate max-w-[120px]">
                    {q.project || '—'}
                  </td>
                  <td className="px-4 py-3">
                    <span className={`px-2 py-0.5 text-xs rounded-md border ${DIFFICULTY_COLORS[q.difficulty] || 'text-k-ink3'}`}>
                      {q.difficulty}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <ConfidenceBadge value={q.confidence} />
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between text-sm">
          <span className="text-k-ink4">共 {total} 题</span>
          <div className="flex items-center gap-2">
            <button disabled={page <= 1} onClick={() => setPage(p => p - 1)}
              className="p-1.5 rounded-lg bg-k-surface border border-k-line disabled:opacity-30 hover:bg-k-sunk transition-colors">
              <ChevronLeft className="w-4 h-4" />
            </button>
            <span className="text-k-ink3 px-2">{page} / {totalPages}</span>
            <button disabled={page >= totalPages} onClick={() => setPage(p => p + 1)}
              className="p-1.5 rounded-lg bg-k-surface border border-k-line disabled:opacity-30 hover:bg-k-sunk transition-colors">
              <ChevronRight className="w-4 h-4" />
            </button>
          </div>
        </div>
      )}

      {/* Generate Config Modal */}
      {showGenerate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-k-bg/60 backdrop-blur-sm">
          <div className="bg-k-surface border border-k-line rounded-2xl w-full max-w-md p-6 shadow-2xl">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-semibold text-k-ink">生成面试题</h3>
              <button onClick={() => setShowGenerate(false)} className="p-1 rounded-lg hover:bg-k-sunk text-k-ink3">
                <XIcon className="w-5 h-5" />
              </button>
            </div>

            <div className="space-y-4">
              <div>
                <label className="block text-sm text-k-ink3 mb-1">分类（留空=全部分类）</label>
                <select value={genCategory} onChange={e => setGenCategory(e.target.value)}
                  className="w-full bg-k-bg border border-k-line rounded-lg px-3 py-2 text-sm text-k-ink2 outline-none focus:border-k-accent">
                  <option value="">所有分类（每类各生成）</option>
                  {categories.map(c => <option key={c.key} value={c.key}>{c.label}</option>)}
                </select>
              </div>
              <div>
                <label className="block text-sm text-k-ink3 mb-1">聚焦项目（可选）</label>
                <select value={genProject} onChange={e => setGenProject(e.target.value)}
                  className="w-full bg-k-bg border border-k-line rounded-lg px-3 py-2 text-sm text-k-ink2 outline-none focus:border-k-accent">
                  <option value="">不限项目</option>
                  <option value="yongtu-intern">用途科技实习</option>
                  <option value="smart-portal">智慧服务门户系统</option>
                  <option value="cloud-native-infra">云原生基础设施平台演进</option>
                  <option value="1qfm">个人音乐电台 1QFM</option>
                  <option value="knowledge-graph">AI Agent 知识图谱系统</option>
                </select>
              </div>
              <div>
                <label className="block text-sm text-k-ink3 mb-1">聚焦技能域（可选）</label>
                <select value={genSkillDomain} onChange={e => setGenSkillDomain(e.target.value)}
                  className="w-full bg-k-bg border border-k-line rounded-lg px-3 py-2 text-sm text-k-ink2 outline-none focus:border-k-accent">
                  <option value="">不限技能域</option>
                  {skillDomains.map(d => (
                    <option key={d.key} value={d.key}>{d.icon} {d.label}</option>
                  ))}
                </select>
                {genSkillDomain && (
                  <div className="mt-1.5 flex flex-wrap gap-1">
                    {skillDomains.find(d => d.key === genSkillDomain)?.sub_domains.map(s => (
                      <span key={s} className="px-1.5 py-0.5 text-xs bg-k-sunk text-k-ink4 rounded">{s}</span>
                    ))}
                  </div>
                )}
              </div>
              <div>
                <label className="block text-sm text-k-ink3 mb-1">聚焦关键词/主题（可选）</label>
                <input
                  type="text"
                  value={genFocusTopic}
                  onChange={e => setGenFocusTopic(e.target.value)}
                  placeholder="例如：Redis 缓存一致性、Kubernetes 调度、限流降级"
                  className="w-full bg-k-bg border border-k-line rounded-lg px-3 py-2 text-sm text-k-ink2 outline-none focus:border-k-accent placeholder:text-k-ink4"
                />
                <p className="mt-1 text-xs text-k-ink4">支持短主题或多个关键词（逗号分隔），当前版本按单个字符串透传。</p>
              </div>
              <div>
                <label className="block text-sm text-k-ink3 mb-1">每类生成题数</label>
                <input type="number" min={1} max={10} value={genCount} onChange={e => setGenCount(Number(e.target.value))}
                  className="w-full bg-k-bg border border-k-line rounded-lg px-3 py-2 text-sm text-k-ink2 outline-none focus:border-k-accent" />
              </div>
            </div>

            <p className="mt-4 text-xs text-k-ink4">
              点击「开始生成」后弹窗将关闭，生成在后台进行，你可以继续浏览其他页面。
            </p>

            <div className="flex justify-end gap-2 mt-4">
              <button onClick={() => setShowGenerate(false)}
                className="px-4 py-2 bg-k-sunk hover:bg-k-muted text-k-ink2 rounded-lg text-sm transition-colors">
                取消
              </button>
              <button onClick={handleGenerate}
                className="px-4 py-2 bg-k-accent hover:bg-k-accent text-k-accent-contrast rounded-lg text-sm font-medium transition-colors flex items-center gap-2">
                <Sparkles className="w-4 h-4" /> 开始生成
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function StatCard({ label, value, accent }: { label: string; value: string | number; accent?: boolean }) {
  return (
    <div className="p-4 rounded-xl border border-k-line/80 bg-k-surface/40">
      <div className="text-xs text-k-ink4 mb-1">{label}</div>
      <div className={`text-lg font-semibold ${accent ? 'text-k-amber-ink' : 'text-k-ink'}`}>{value}</div>
    </div>
  )
}

function ConfidenceBadge({ value }: { value: number | null }) {
  if (value === null || value === undefined) return <span className="text-k-ink4">—</span>
  const pct = Math.round(value * 100)
  const color = pct >= 80 ? 'text-k-ok' : pct >= 50 ? 'text-k-amber-ink' : 'text-k-hot'
  return <span className={`text-xs font-mono ${color}`}>{pct}%</span>
}
