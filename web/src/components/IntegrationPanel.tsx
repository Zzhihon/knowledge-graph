import { X, Loader2, CheckCircle, GitMerge, Route, BookOpen, Link2, Sparkles, Play } from 'lucide-react'
import type { IntegrateState, IntegrateActions, IntegrateEvent } from '../hooks/useIntegrate'

interface Props {
  state: IntegrateState
  actions: IntegrateActions
  onClose: () => void
}

const phaseIcon = (phase: string) => {
  switch (phase) {
    case 'route': return <Route className="w-3 h-3 text-blue-400" />
    case 'interview': return <Sparkles className="w-3 h-3 text-amber-400" />
    case 'moc': return <BookOpen className="w-3 h-3 text-emerald-400" />
    case 'relations': return <Link2 className="w-3 h-3 text-indigo-400" />
    default: return <Loader2 className="w-3 h-3 text-zinc-400" />
  }
}

function EventItem({ evt }: { evt: IntegrateEvent }) {
  switch (evt.event) {
    case 'route_done':
      return (
        <div className="flex items-center gap-2 text-xs">
          <Route className="w-3 h-3 text-blue-400 shrink-0" />
          <span className="text-zinc-400 truncate">
            {evt.entry_id}: {evt.changed ? `${evt.original_domain} → ${evt.domain}` : '域未变'}
          </span>
        </div>
      )
    case 'interview_linked':
      return (
        <div className="flex items-center gap-2 text-xs">
          <GitMerge className="w-3 h-3 text-amber-400 shrink-0" />
          <span className="text-zinc-400 truncate">
            关联 {(evt.linked as Array<{title: string}>)?.length ?? 0} 个面试题
          </span>
        </div>
      )
    case 'interview_generated':
      return (
        <div className="flex items-center gap-2 text-xs">
          <Sparkles className="w-3 h-3 text-amber-400 shrink-0" />
          <span className="text-amber-300/80 truncate">
            生成: {evt.title as string}
          </span>
        </div>
      )
    case 'interview_skipped':
      return (
        <div className="flex items-center gap-2 text-xs">
          <Sparkles className="w-3 h-3 text-zinc-600 shrink-0" />
          <span className="text-zinc-600 truncate">无面试价值</span>
        </div>
      )
    case 'moc_updated':
      return (
        <div className="flex items-center gap-2 text-xs">
          <BookOpen className="w-3 h-3 text-emerald-400 shrink-0" />
          <span className="text-zinc-400 truncate">
            {evt.domain as string}: {evt.action as string}
          </span>
        </div>
      )
    case 'relations_done':
      return (
        <div className="flex items-center gap-2 text-xs">
          <Link2 className="w-3 h-3 text-indigo-400 shrink-0" />
          <span className="text-zinc-400 truncate">
            补全 {(evt.relations as unknown[])?.length ?? 0} 个关联
          </span>
        </div>
      )
    case 'entry_done':
      return (
        <div className="flex items-center gap-2 text-xs">
          <CheckCircle className="w-3 h-3 text-emerald-400 shrink-0" />
          <span className="text-emerald-400/80 font-medium">
            {evt.entry_id} 完成 ({evt.index as number}/{evt.total as number})
          </span>
        </div>
      )
    default:
      return null
  }
}

export default function IntegrationPanel({ state, actions, onClose }: Props) {
  const { phase, events, currentMessage, totalEntries, processedEntries, summary, errors } = state

  // Filter display events (skip phase events which are just status updates)
  const displayEvents = events.filter(e =>
    !['start', 'phase'].includes(e.event)
  )

  return (
    <div className="fixed right-0 top-0 h-full w-96 z-40 bg-zinc-950 border-l border-zinc-800 shadow-2xl flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between p-4 border-b border-zinc-800/80">
        <div className="flex items-center gap-2">
          {phase === 'running' && <Loader2 className="w-4 h-4 text-indigo-400 animate-spin" />}
          {phase === 'complete' && <CheckCircle className="w-4 h-4 text-emerald-400" />}
          <h3 className="text-sm font-medium text-zinc-200">智能整合网关</h3>
        </div>
        <button onClick={onClose} className="text-zinc-500 hover:text-zinc-300">
          <X className="w-4 h-4" />
        </button>
      </div>

      {/* Progress */}
      {phase === 'running' && (
        <div className="px-4 py-3 border-b border-zinc-800/50 space-y-2">
          <div className="flex items-center gap-2 text-xs text-zinc-400">
            {currentMessage && phaseIcon(currentMessage.includes('语义') ? 'route' :
              currentMessage.includes('面试') ? 'interview' :
              currentMessage.includes('主题') ? 'moc' :
              currentMessage.includes('关系') ? 'relations' : '')}
            <span>{currentMessage}</span>
          </div>
          {totalEntries > 0 && (
            <div className="space-y-1">
              <div className="flex justify-between text-[10px] text-zinc-600">
                <span>条目进度</span>
                <span>{processedEntries}/{totalEntries}</span>
              </div>
              <div className="h-1 bg-zinc-800 rounded-full overflow-hidden">
                <div
                  className="h-full bg-indigo-500/60 rounded-full transition-all duration-500"
                  style={{ width: `${totalEntries ? (processedEntries / totalEntries) * 100 : 0}%` }}
                />
              </div>
            </div>
          )}
        </div>
      )}

      {/* Summary */}
      {summary && (
        <div className="px-4 py-3 border-b border-zinc-800/50">
          <div className="grid grid-cols-2 gap-2 text-xs">
            <div className="flex items-center gap-1.5">
              <Route className="w-3 h-3 text-blue-400" />
              <span className="text-zinc-400">条目: <span className="text-zinc-200">{summary.total_entries}</span></span>
            </div>
            <div className="flex items-center gap-1.5">
              <Sparkles className="w-3 h-3 text-amber-400" />
              <span className="text-zinc-400">面试题: <span className="text-amber-300">+{summary.interview_generated}</span></span>
            </div>
            <div className="flex items-center gap-1.5">
              <BookOpen className="w-3 h-3 text-emerald-400" />
              <span className="text-zinc-400">MOC: <span className="text-emerald-300">{summary.moc_updated}</span></span>
            </div>
            <div className="flex items-center gap-1.5">
              <Link2 className="w-3 h-3 text-indigo-400" />
              <span className="text-zinc-400">关联: <span className="text-indigo-300">+{summary.relations_added}</span></span>
            </div>
          </div>
        </div>
      )}

      {/* Event log */}
      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-1.5">
        {displayEvents.map((evt, i) => (
          <EventItem key={i} evt={evt} />
        ))}
        {displayEvents.length === 0 && phase === 'running' && (
          <p className="text-xs text-zinc-600 text-center py-4">等待事件...</p>
        )}
      </div>

      {/* Errors */}
      {errors.length > 0 && (
        <div className="px-4 py-2 border-t border-rose-800/30 bg-rose-950/20">
          {errors.map((err, i) => (
            <p key={i} className="text-xs text-rose-400 truncate">{err}</p>
          ))}
        </div>
      )}

      {/* Footer */}
      <div className="px-4 py-3 border-t border-zinc-800/80 flex justify-between items-center">
        {phase === 'idle' && (
          <>
            <span className="text-[10px] text-zinc-600">对全部 ke-* 条目执行整合</span>
            <button
              onClick={() => actions.startAll({ skipInterview: false })}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-indigo-600/20 text-indigo-400 hover:bg-indigo-600/30 rounded-lg transition-colors"
            >
              <Play className="w-3 h-3" />
              全量整合
            </button>
          </>
        )}
        {phase === 'running' && (
          <>
            <span />
            <button
              onClick={actions.stop}
              className="px-3 py-1.5 text-xs text-rose-400 hover:text-rose-300 transition-colors"
            >
              取消
            </button>
          </>
        )}
        {phase === 'complete' && (
          <>
            <span />
            <button
              onClick={() => { actions.dismiss(); onClose() }}
              className="px-3 py-1.5 text-xs bg-zinc-800 text-zinc-300 hover:bg-zinc-700 rounded-lg transition-colors"
            >
              关闭
            </button>
          </>
        )}
      </div>
    </div>
  )
}
