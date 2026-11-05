import { useState, useEffect, useCallback } from 'react'
import { Library, Loader2, ChevronLeft, ChevronRight, Search, Play, Sparkles, Filter } from 'lucide-react'
import { get, post } from '../api/client'
import type { ProblemListItem, ProblemListResponse, PatternInfo, ProblemStats, ExamPaper } from '../types'

interface Props {
  onPreviewEntry?: (entryId: string) => void
  onStartExam?: (exam: ExamPaper) => void
  setActiveTab?: (tab: string) => void
}

const DIFFICULTY_COLORS: Record<string, string> = {
  easy: 'text-k-ok bg-k-ok/10 border-k-ok/20',
  medium: 'text-k-amber-ink bg-k-amber/10 border-k-amber/20',
  hard: 'text-k-hot bg-k-hot/10 border-k-hot/20',
}

export default function ProblemBankView({ onPreviewEntry, onStartExam, setActiveTab }: Props) {
  const [problems, setProblems] = useState<ProblemListItem[]>([])
  const [patterns, setPatterns] = useState<PatternInfo[]>([])
  const [stats, setStats] = useState<ProblemStats | null>(null)
  const [loading, setLoading] = useState(true)
  const [generating, setGenerating] = useState<string | null>(null)
  const [examLoading, setExamLoading] = useState(false)

  // Filters
  const [filterPattern, setFilterPattern] = useState('')
  const [filterDifficulty, setFilterDifficulty] = useState('')
  const [searchQuery, setSearchQuery] = useState('')
  const [page, setPage] = useState(1)
  const [totalPages, setTotalPages] = useState(1)
  const [total, setTotal] = useState(0)

  const fetchData = useCallback(async () => {
    setLoading(true)
    try {
      const params: Record<string, string> = {
        page: String(page),
        page_size: '15',
      }
      if (filterPattern) params.pattern = filterPattern
      if (filterDifficulty) params.difficulty = filterDifficulty
      if (searchQuery) params.search = searchQuery

      const [problemsRes, patternsRes, statsRes] = await Promise.all([
        get<ProblemListResponse>('/problems', params),
        get<PatternInfo[]>('/problems/patterns'),
        get<ProblemStats>('/problems/stats'),
      ])
      setProblems(problemsRes.items)
      setTotalPages(problemsRes.total_pages)
      setTotal(problemsRes.total)
      setPatterns(patternsRes)
      setStats(statsRes)
    } catch {
      // backend may not be up yet
    } finally {
      setLoading(false)
    }
  }, [page, filterPattern, filterDifficulty, searchQuery])

  useEffect(() => { fetchData() }, [fetchData])

  const handleGeneratePattern = async (patternName: string, chineseName: string) => {
    setGenerating(patternName)
    try {
      const result = await post<{
        pattern_name: string
        pattern_file: string
        problems: Array<{ entry_id: string; title: string; leetcode_id: number | null; difficulty: string; file_path: string }>
        errors: string[]
      }>('/problems/generate-pattern', {
        pattern_name: patternName,
        chinese_name: chineseName,
        problem_count: 5,
      })

      // Show success notification
      const problemCount = result.problems.length
      const errorCount = result.errors.length
      const message = errorCount > 0
        ? `✅ 生成完成：${problemCount} 题成功，${errorCount} 题失败`
        : `✅ 成功生成 ${problemCount} 道题目`

      // Simple toast notification (you can replace with a proper toast library)
      const toast = document.createElement('div')
      toast.className = 'fixed top-4 right-4 px-4 py-3 bg-k-ok text-white rounded-lg shadow-lg z-50 animate-fade-in'
      toast.textContent = message
      document.body.appendChild(toast)
      setTimeout(() => toast.remove(), 3000)

      await fetchData()
    } catch (error) {
      // Show error notification
      const toast = document.createElement('div')
      toast.className = 'fixed top-4 right-4 px-4 py-3 bg-k-hot text-white rounded-lg shadow-lg z-50 animate-fade-in'
      toast.textContent = `❌ 生成失败：${error instanceof Error ? error.message : '未知错误'}`
      document.body.appendChild(toast)
      setTimeout(() => toast.remove(), 4000)
    } finally {
      setGenerating(null)
    }
  }

  const handleGenerateExam = async () => {
    setExamLoading(true)
    try {
      const exam = await post<ExamPaper>('/problems/generate-exam', {
        problem_count: 4,
        exclude_recently_reviewed: true,
      })
      if (onStartExam) {
        onStartExam(exam)
      }
      if (setActiveTab) {
        setActiveTab('exam')
      }
    } catch {
      // handle error
    } finally {
      setExamLoading(false)
    }
  }

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
            <Library className="w-5 h-5" />
          </div>
          <div>
            <h1 className="text-xl font-semibold text-k-ink">面试题库</h1>
            <p className="text-sm text-k-ink4">管理算法模式与面试题目</p>
          </div>
        </div>
        <button
          onClick={handleGenerateExam}
          disabled={examLoading || !stats || stats.total_problems === 0}
          className="flex items-center gap-2 px-4 py-2.5 bg-k-accent hover:bg-k-accent disabled:opacity-50 text-k-accent-contrast rounded-lg text-sm font-medium transition-colors"
        >
          {examLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
          模拟面试
        </button>
      </div>

      {/* Stats Cards */}
      {stats && (
        <div className="grid grid-cols-4 gap-4">
          <StatCard label="总题数" value={stats.total_problems} />
          <StatCard label="模式覆盖" value={stats.pattern_coverage} />
          <StatCard
            label="难度分布"
            value={`E${stats.difficulty_distribution.easy || 0} M${stats.difficulty_distribution.medium || 0} H${stats.difficulty_distribution.hard || 0}`}
          />
          <StatCard label="待复习" value={stats.needs_review} accent={stats.needs_review > 0} />
        </div>
      )}

      {/* Pattern Coverage Grid */}
      <div>
        <h2 className="text-sm font-medium text-k-ink3 mb-3">模式覆盖</h2>
        <div className="grid grid-cols-4 gap-3">
          {patterns.map((p) => (
            <div
              key={p.name}
              className={`p-3 rounded-xl border transition-colors ${
                p.status === 'active'
                  ? 'border-k-line bg-k-surface/40'
                  : generating === p.name
                  ? 'border-k-violet/50 bg-k-violet/5'
                  : 'border-dashed border-k-line-strong/50 bg-k-surface/20 hover:border-k-violet/30 cursor-pointer'
              }`}
              onClick={() => {
                if (p.status === 'pending' && !generating) {
                  handleGeneratePattern(p.name, p.chinese_name)
                }
              }}
            >
              <div className="flex items-center justify-between mb-1">
                <span className="text-sm font-medium text-k-ink">{p.chinese_name}</span>
                {p.status === 'active' ? (
                  <span className="text-xs text-k-ok">✓</span>
                ) : generating === p.name ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin text-k-violet" />
                ) : (
                  <Sparkles className="w-3.5 h-3.5 text-k-ink4" />
                )}
              </div>
              <div className="flex items-center justify-between">
                <span className="text-xs text-k-ink4">{p.name}</span>
                <span className="text-xs text-k-ink4">
                  {p.status === 'active'
                    ? `${p.problem_count} 题`
                    : generating === p.name
                    ? '生成中...'
                    : '待生成'}
                </span>
              </div>
              {generating === p.name && (
                <div className="mt-2 text-xs text-k-violet">
                  正在调用 Claude API 生成题目，预计 30-60 秒...
                </div>
              )}
            </div>
          ))}
        </div>
      </div>

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
          <select
            value={filterPattern}
            onChange={(e) => { setFilterPattern(e.target.value); setPage(1) }}
            className="bg-k-bg border border-k-line rounded-lg px-3 py-2 text-sm text-k-ink2 outline-none focus:border-k-accent"
          >
            <option value="">所有模式</option>
            {patterns.filter(p => p.status === 'active').map(p => (
              <option key={p.name} value={p.name}>{p.chinese_name}</option>
            ))}
          </select>
          <select
            value={filterDifficulty}
            onChange={(e) => { setFilterDifficulty(e.target.value); setPage(1) }}
            className="bg-k-bg border border-k-line rounded-lg px-3 py-2 text-sm text-k-ink2 outline-none focus:border-k-accent"
          >
            <option value="">所有难度</option>
            <option value="easy">Easy</option>
            <option value="medium">Medium</option>
            <option value="hard">Hard</option>
          </select>
        </div>
      </div>

      {/* Problem Table */}
      <div className="border border-k-line/80 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-k-line/80 bg-k-surface/40">
              <th className="text-left px-4 py-3 text-k-ink4 font-medium w-16">LC-ID</th>
              <th className="text-left px-4 py-3 text-k-ink4 font-medium">标题</th>
              <th className="text-left px-4 py-3 text-k-ink4 font-medium w-20">难度</th>
              <th className="text-left px-4 py-3 text-k-ink4 font-medium w-32">模式</th>
              <th className="text-left px-4 py-3 text-k-ink4 font-medium w-32">公司</th>
              <th className="text-right px-4 py-3 text-k-ink4 font-medium w-20">置信度</th>
            </tr>
          </thead>
          <tbody>
            {problems.length === 0 ? (
              <tr>
                <td colSpan={6} className="text-center py-12 text-k-ink4">
                  {loading ? '加载中...' : '暂无题目'}
                </td>
              </tr>
            ) : (
              problems.map((p) => (
                <tr
                  key={p.id}
                  className="border-b border-k-line/40 hover:bg-k-surface/40 cursor-pointer transition-colors"
                  onClick={() => onPreviewEntry?.(p.id)}
                >
                  <td className="px-4 py-3 font-mono text-k-ink3">
                    {p.leetcode_id ?? '—'}
                  </td>
                  <td className="px-4 py-3 text-k-ink">{p.title}</td>
                  <td className="px-4 py-3">
                    <span className={`px-2 py-0.5 text-xs rounded-md border ${DIFFICULTY_COLORS[p.difficulty] || 'text-k-ink3'}`}>
                      {p.difficulty}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap gap-1">
                      {(p.pattern || []).map((pat) => (
                        <span key={pat} className="px-1.5 py-0.5 text-xs bg-k-sunk text-k-ink3 rounded">
                          {pat}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-xs text-k-ink4 truncate max-w-[120px]">
                    {(p.companies || []).slice(0, 2).join(', ')}
                    {(p.companies || []).length > 2 && '...'}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <ConfidenceBadge value={p.confidence} />
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
            <button
              disabled={page <= 1}
              onClick={() => setPage(p => p - 1)}
              className="p-1.5 rounded-lg bg-k-surface border border-k-line disabled:opacity-30 hover:bg-k-sunk transition-colors"
            >
              <ChevronLeft className="w-4 h-4" />
            </button>
            <span className="text-k-ink3 px-2">{page} / {totalPages}</span>
            <button
              disabled={page >= totalPages}
              onClick={() => setPage(p => p + 1)}
              className="p-1.5 rounded-lg bg-k-surface border border-k-line disabled:opacity-30 hover:bg-k-sunk transition-colors"
            >
              <ChevronRight className="w-4 h-4" />
            </button>
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
      <div className={`text-lg font-semibold ${accent ? 'text-k-amber-ink' : 'text-k-ink'}`}>
        {value}
      </div>
    </div>
  )
}

function ConfidenceBadge({ value }: { value: number | null }) {
  if (value === null || value === undefined) return <span className="text-k-ink4">—</span>
  const pct = Math.round(value * 100)
  const color = pct >= 80 ? 'text-k-ok' : pct >= 50 ? 'text-k-amber-ink' : 'text-k-hot'
  return <span className={`text-xs font-mono ${color}`}>{pct}%</span>
}
