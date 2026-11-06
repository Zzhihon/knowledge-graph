import { Search, Moon, Monitor, Sun, Plus, Trash2, MessageSquare, X } from 'lucide-react'
import type { ConversationListItem } from '../../types'
import type { ThemeChoice } from '../../lib/theme'
import { grouped } from '../../lib/modules'

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
  /** 'drawer' is the narrow-screen menu: always shown, with a close button. */
  variant?: 'side' | 'drawer'
  onClose?: () => void
}

export default function NavSidebar(p: Props) {
  const drawer = p.variant === 'drawer'
  return (
    <aside className={drawer
      ? 'flex h-full w-[280px] max-w-[86vw] flex-col bg-k-sidebar'
      : 'hidden w-[236px] shrink-0 flex-col border-r border-k-line bg-k-sidebar md:flex'}>
      <div className="flex items-start px-6 pb-5 pt-7">
        <div>
          <div className="text-[22px] font-light tracking-[0.34em] text-k-ink">KG<span className="text-k-accent">·</span>VAULT</div>
          <div className="mt-1 text-[12px] tracking-[0.2em] text-k-ink4">个人知识库</div>
        </div>
        {drawer && (
          <button onClick={p.onClose} className="-mr-2 ml-auto rounded-full p-1.5 text-k-ink3 hover:bg-k-sunk" aria-label="关闭菜单">
            <X className="h-5 w-5" />
          </button>
        )}
      </div>

      <nav className="flex-1 overflow-y-auto px-4 pb-4">
        {grouped().map(([group, items]) => (
          <div key={group}>
            <div className="px-3 pb-1 pt-4 text-[12px] text-k-ink4">{group}</div>
            {items.map(({ id, label, icon: Icon, status, issue }) => (
              <div key={id}>
                <button
                  onClick={() => { p.setActiveTab(id); if (id === 'ask') p.onNewConversation() }}
                  aria-current={p.activeTab === id ? 'page' : undefined}
                  title={status === 'planned' ? `规划中 #${issue}：还没上线，点开看说明` : undefined}
                  className={`flex w-full items-center gap-3 rounded-tile px-3.5 py-2 text-left text-[14.5px] transition-colors ${
                    p.activeTab === id ? 'bg-k-sunk font-semibold text-k-ink'
                      : status === 'planned' ? 'text-k-ink4 hover:bg-k-sunk' : 'text-k-ink2 hover:bg-k-sunk'}`}
                >
                  <Icon className={`h-[17px] w-[17px] ${status === 'planned' ? 'opacity-50' : 'opacity-80'}`} />
                  <span className="min-w-0 flex-1 truncate">{label}</span>
                  {status === 'planned' && <span className="shrink-0 whitespace-nowrap text-[11.5px] font-normal text-k-ink4">规划中 #{issue}</span>}
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
          <Search className="h-4 w-4" /><span className="flex-1 text-left">全局搜索</span><kbd className="k-kbd">⌘K</kbd>
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
