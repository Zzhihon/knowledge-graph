import { X, Loader2, CheckCircle, GitMerge, Route, BookOpen, Link2, Sparkles, Play } from 'lucide-react'
import type { IntegrateState, IntegrateActions, IntegrateEvent } from '../hooks/useIntegrate'

interface Props {
  state: IntegrateState
  actions: IntegrateActions
  onClose: () => void
}

const phaseIcon = (phase: string) => {
  switch (phase) {
    case 'route': return <Route className="w-3 h-3 text-k-accent" />
    case 'interview': return <Sparkles className="w-3 h-3 text-k-amber-ink" />
    case 'moc': return <BookOpen className="w-3 h-3 text-k-ok" />
    case 'relations': return <Link2 className="w-3 h-3 text-k-accent" />
    default: return <Loader2 className="w-3 h-3 text-k-ink3" />
  }
}

function EventItem({ evt }: { evt: IntegrateEvent }) {
  switch (evt.event) {
    case 'route_done':
      return (
        <div className="flex items-center gap-2 text-xs">
          <Route className="w-3 h-3 text-k-accent shrink-0" />
          <span className="text-k-ink3 truncate">
            {evt.entry_id}: {evt.changed ? `${evt.original_domain} → ${evt.domain}` : '域未变'}
          </span>
        </div>
      )
    case 'interview_linked':
      return (
        <div className="flex items-center gap-2 text-xs">
          <GitMerge className="w-3 h-3 text-k-amber-ink shrink-0" />
          <span className="text-k-ink3 truncate">
            关联 {(evt.linked as Array<{title: string}>)?.length ?? 0} 个面试题
          </span>
        </div>
      )
    case 'interview_generated':
      return (
        <div className="flex items-center gap-2 text-xs">
          <Sparkles className="w-3 h-3 text-k-amber-ink shrink-0" />
          <span className="text-k-amber-ink/80 truncate">
            生成: {evt.title as string}
          </span>
        </div>
      )
    case 'interview_skipped':
      return (
        <div className="flex items-center gap-2 text-xs">
          <Sparkles className="w-3 h-3 text-k-ink4 shrink-0" />
          <span className="text-k-ink4 truncate">无面试价值</span>
        </div>
      )
    case 'moc_updated':
      return (
        <div className="flex items-center gap-2 text-xs">
          <BookOpen className="w-3 h-3 text-k-ok shrink-0" />
          <span className="text-k-ink3 truncate">
            {evt.domain as string}: {evt.action as string}
          </span>
        </div>
      )
    case 'relations_done':
      return (
        <div className="flex items-center gap-2 text-xs">
          <Link2 className="w-3 h-3 text-k-accent shrink-0" />
          <span className="text-k-ink3 truncate">
            补全 {(evt.relations as unknown[])?.length ?? 0} 个关联
          </span>
        </div>
      )
    case 'entry_done':
      return (
        <div className="flex items-center gap-2 text-xs">
          <CheckCircle className="w-3 h-3 text-k-ok shrink-0" />
          <span className="text-k-ok/80 font-medium">
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
    <div className="fixed right-0 top-0 h-full w-96 z-40 bg-k-bg border-l border-k-line shadow-2xl flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between p-4 border-b border-k-line/80">
        <div className="flex items-center gap-2">
          {phase === 'running' && <Loader2 className="w-4 h-4 text-k-accent animate-spin" />}
          {phase === 'complete' && <CheckCircle className="w-4 h-4 text-k-ok" />}
          <h3 className="text-sm font-medium text-k-ink">智能整合网关</h3>
        </div>
        <button onClick={onClose} className="text-k-ink4 hover:text-k-ink2">
          <X className="w-4 h-4" />
        </button>
      </div>

      {/* Progress */}
      {phase === 'running' && (
        <div className="px-4 py-3 border-b border-k-line/50 space-y-2">
          <div className="flex items-center gap-2 text-xs text-k-ink3">
            {currentMessage && phaseIcon(currentMessage.includes('语义') ? 'route' :
              currentMessage.includes('面试') ? 'interview' :
              currentMessage.includes('主题') ? 'moc' :
              currentMessage.includes('关系') ? 'relations' : '')}
            <span>{currentMessage}</span>
          </div>
          {totalEntries > 0 && (
            <div className="space-y-1">
              <div className="flex justify-between text-[10px] text-k-ink4">
                <span>条目进度</span>
                <span>{processedEntries}/{totalEntries}</span>
              </div>
              <div className="h-1 bg-k-sunk rounded-full overflow-hidden">
                <div
                  className="h-full bg-k-accent/60 rounded-full transition-all duration-500"
                  style={{ width: `${totalEntries ? (processedEntries / totalEntries) * 100 : 0}%` }}
                />
              </div>
            </div>
          )}
        </div>
      )}

      {/* Summary */}
      {summary && (
        <div className="px-4 py-3 border-b border-k-line/50">
          <div className="grid grid-cols-2 gap-2 text-xs">
            <div className="flex items-center gap-1.5">
              <Route className="w-3 h-3 text-k-accent" />
              <span className="text-k-ink3">条目: <span className="text-k-ink">{summary.total_entries}</span></span>
            </div>
            <div className="flex items-center gap-1.5">
              <Sparkles className="w-3 h-3 text-k-amber-ink" />
              <span className="text-k-ink3">面试题: <span className="text-k-amber-ink">+{summary.interview_generated}</span></span>
            </div>
            <div className="flex items-center gap-1.5">
              <BookOpen className="w-3 h-3 text-k-ok" />
              <span className="text-k-ink3">MOC: <span className="text-k-ok">{summary.moc_updated}</span></span>
            </div>
            <div className="flex items-center gap-1.5">
              <Link2 className="w-3 h-3 text-k-accent" />
              <span className="text-k-ink3">关联: <span className="text-k-accent">+{summary.relations_added}</span></span>
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
          <p className="text-xs text-k-ink4 text-center py-4">等待事件...</p>
        )}
      </div>

      {/* Errors */}
      {errors.length > 0 && (
        <div className="px-4 py-2 border-t border-k-hot/30 bg-k-hot-soft">
          {errors.map((err, i) => (
            <p key={i} className="text-xs text-k-hot truncate">{err}</p>
          ))}
        </div>
      )}

      {/* Footer */}
      <div className="px-4 py-3 border-t border-k-line/80 flex justify-between items-center">
        {phase === 'idle' && (
          <>
            <span className="text-[10px] text-k-ink4">对全部 ke-* 条目执行整合</span>
            <button
              onClick={() => actions.startAll({ skipInterview: false })}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-k-accent/20 text-k-accent hover:bg-k-accent/30 rounded-lg transition-colors"
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
              className="px-3 py-1.5 text-xs text-k-hot hover:text-k-hot transition-colors"
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
              className="px-3 py-1.5 text-xs bg-k-sunk text-k-ink2 hover:bg-k-muted rounded-lg transition-colors"
            >
              关闭
            </button>
          </>
        )}
      </div>
    </div>
  )
}
