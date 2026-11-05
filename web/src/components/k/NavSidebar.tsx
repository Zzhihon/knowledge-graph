import type { ComponentType } from 'react'
import {
  Zap, List, LayoutGrid, FlaskConical, Search, Share2, Boxes, GitMerge, LayoutDashboard, AlertTriangle, Rss,
  Play, Library, Briefcase, GraduationCap, Moon, Monitor, Sun, Plus, Trash2, MessageSquare,
  Inbox, Workflow, ClipboardCheck,
} from 'lucide-react'
import type { ConversationListItem } from '../../types'
import type { ThemeChoice } from '../../lib/theme'

type Item = { id: string; label: string; icon: ComponentType<{ className?: string }> }
const GROUPS: [string, Item[]][] = [
  ['内容', [
    { id: 'daily', label: '日报', icon: Zap },
    { id: 'library', label: '知识库', icon: List },
    { id: 'projects', label: '项目', icon: LayoutGrid },
    { id: 'lab', label: '实验', icon: FlaskConical },
    { id: 'eval', label: '评测', icon: ClipboardCheck },
  ]],
  ['自动化', [
    { id: 'hub', label: '中台', icon: Inbox },
    { id: 'rules', label: '规则与运行', icon: Workflow },
  ]],
  ['工具', [
    { id: 'ask', label: '探索与问答', icon: Search },
    { id: 'network', label: '知识网络', icon: Share2 },
    { id: 'topics', label: '主题总览', icon: Boxes },
    { id: 'graph', label: '图谱演进', icon: GitMerge },
    { id: 'dashboard', label: '概览仪表盘', icon: LayoutDashboard },
    { id: 'health', label: '健康巡检', icon: AlertTriangle },
    { id: 'rss', label: 'RSS 摄取', icon: Rss },
  ]],
  ['练习', [
    { id: 'quiz', label: '间隔测验', icon: Play },
    { id: 'problems', label: '算法题库', icon: Library },
    { id: 'interview', label: '面试题库', icon: Briefcase },
    { id: 'course', label: '课程知识', icon: GraduationCap },
  ]],
]

interface Props {
  activeTab: string
  setActiveTab: (tab: string) => void
  theme: ThemeChoice
  setTheme: (t: ThemeChoice) => void
  onOpenSearch: () => void
  conversations: ConversationListItem[]
  currentConversationId: string | null
  onSelectConversation: (id: string) => void
  onNewConversation: () => void
  onDeleteConversation: (id: string) => void
}

export default function NavSidebar(p: Props) {
  return (
    <aside className="flex w-[236px] shrink-0 flex-col border-r border-k-line bg-k-sidebar">
      <div className="px-6 pb-5 pt-7">
        <div className="text-[22px] font-light tracking-[0.34em] text-k-ink">KG<span className="text-k-accent">·</span>VAULT</div>
        <div className="mt-1 text-[12px] tracking-[0.2em] text-k-ink4">个人知识库</div>
      </div>

      <nav className="flex-1 overflow-y-auto px-4 pb-4">
        {GROUPS.map(([group, items]) => (
          <div key={group}>
            <div className="px-3 pb-1 pt-4 text-[12px] text-k-ink4">{group}</div>
            {items.map(({ id, label, icon: Icon }) => (
              <div key={id}>
                <button
                  onClick={() => { p.setActiveTab(id); if (id === 'ask') p.onNewConversation() }}
                  aria-current={p.activeTab === id ? 'page' : undefined}
                  className={`flex w-full items-center gap-3 rounded-tile px-3.5 py-2 text-left text-[14.5px] transition-colors ${
                    p.activeTab === id ? 'bg-k-sunk font-semibold text-k-ink' : 'text-k-ink2 hover:bg-k-sunk'}`}
                >
                  <Icon className="h-[17px] w-[17px] opacity-80" />{label}
                </button>
                {id === 'ask' && p.activeTab === 'ask' && (
                  <div className="mb-2 ml-4 mt-1 border-l border-k-line pl-2">
                    <button onClick={p.onNewConversation} className="flex w-full items-center gap-2 rounded-control px-2 py-1 text-[12.5px] text-k-ink3 hover:bg-k-sunk">
                      <Plus className="h-3.5 w-3.5" />新对话
                    </button>
                    {p.conversations.slice(0, 12).map((c) => (
                      <div key={c.id} onClick={() => p.onSelectConversation(c.id)}
                        className={`group flex cursor-pointer items-center gap-2 rounded-control px-2 py-1 text-[12.5px] ${
                          p.currentConversationId === c.id ? 'bg-k-sunk text-k-ink' : 'text-k-ink3 hover:bg-k-sunk'}`}>
                        <MessageSquare className="h-3.5 w-3.5 shrink-0" />
                        <span className="flex-1 truncate">{c.title}</span>
                        <button onClick={(e) => { e.stopPropagation(); p.onDeleteConversation(c.id) }}
                          className="opacity-0 hover:text-k-hot group-hover:opacity-100" aria-label="删除会话"><Trash2 className="h-3 w-3" /></button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        ))}
      </nav>

      <div className="space-y-3 border-t border-k-line-soft px-5 py-4">
        <button onClick={p.onOpenSearch} className="k-search h-9 w-full text-[13.5px]">
          <Search className="h-4 w-4" /><span className="flex-1 text-left">搜索条目</span><kbd className="k-kbd">⌘K</kbd>
        </button>
        <div className="flex justify-center gap-1 rounded-full bg-k-sunk p-1" role="group" aria-label="主题">
          {([['dark', Moon, '深色'], ['system', Monitor, '跟随系统'], ['light', Sun, '浅色']] as const).map(([v, Icon, label]) => (
            <button key={v} onClick={() => p.setTheme(v)} aria-pressed={p.theme === v} title={label}
              className={`flex flex-1 justify-center rounded-full py-1.5 ${p.theme === v ? 'bg-k-surface text-k-ink shadow-sm' : 'text-k-ink4 hover:text-k-ink'}`}>
              <Icon className="h-4 w-4" />
            </button>
          ))}
        </div>
      </div>
    </aside>
  )
}
