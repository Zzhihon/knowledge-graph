import {
  LayoutDashboard, Search, Play, AlertTriangle, GitMerge, BrainCircuit,
  Plus, Trash2, MessageSquare, Library, Rss,
} from 'lucide-react'
import type { ConversationListItem } from '../types'

const navItems = [
  { id: 'dashboard', icon: LayoutDashboard, label: '概览仪表盘' },
  { id: 'ask', icon: Search, label: '探索与问答' },
  { id: 'quiz', icon: Play, label: '间隔测验' },
  { id: 'problems', icon: Library, label: '面试题库' },
  { id: 'health', icon: AlertTriangle, label: '健康巡检' },
  { id: 'graph', icon: GitMerge, label: '图谱演进' },
  { id: 'rss', icon: Rss, label: 'RSS 摄取' },
]

interface Props {
  activeTab: string
  setActiveTab: (tab: string) => void
  conversations: ConversationListItem[]
  currentConversationId: string | null
  onSelectConversation: (id: string) => void
  onNewConversation: () => void
  onDeleteConversation: (id: string) => void
}

export default function Sidebar({
  activeTab,
  setActiveTab,
  conversations,
  currentConversationId,
  onSelectConversation,
  onNewConversation,
  onDeleteConversation,
}: Props) {
  return (
    <aside className="w-64 bg-[#09090b] flex flex-col z-20 shrink-0 border-r border-zinc-800/50">
      <div className="h-14 flex items-center px-6 border-b border-zinc-800/50">
        <div className="flex items-center gap-2">
          <div className="w-6 h-6 rounded bg-zinc-100 flex items-center justify-center">
            <BrainCircuit className="w-4 h-4 text-zinc-900" />
          </div>
          <span className="font-semibold tracking-wide text-zinc-100">K.G. Vault</span>
        </div>
      </div>

      <div className="px-4 py-6 flex-1 overflow-y-auto">
        <div className="text-xs font-medium text-zinc-500 mb-3 px-2">主导航</div>
        <nav className="space-y-1">
          {navItems.map((item) => (
            <button
              key={item.id}
              onClick={() => {
                setActiveTab(item.id)
                if (item.id === 'ask') onNewConversation()
              }}
              className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-all duration-200 ${
                activeTab === item.id
                  ? 'bg-zinc-800/60 text-zinc-100 font-medium'
                  : 'text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200'
              }`}
            >
              <item.icon className="w-4 h-4" />
              {item.label}
            </button>
          ))}
        </nav>

        {activeTab === 'ask' && (
          <div className="mt-6">
            <div className="flex items-center justify-between px-2 mb-2">
              <span className="text-xs font-medium text-zinc-500">历史会话</span>
              <button
                onClick={onNewConversation}
                className="p-1 rounded hover:bg-zinc-800 text-zinc-500 hover:text-zinc-300 transition-colors"
                title="新对话"
              >
                <Plus className="w-3.5 h-3.5" />
              </button>
            </div>

            <div className="space-y-0.5 max-h-[calc(100vh-24rem)] overflow-y-auto">
              {conversations.length === 0 && (
                <p className="text-xs text-zinc-600 px-2 py-2">暂无历史会话</p>
              )}
              {conversations.map((c) => (
                <div
                  key={c.id}
                  className={`group flex items-center gap-2 px-2 py-1.5 rounded-lg cursor-pointer transition-colors ${
                    currentConversationId === c.id
                      ? 'bg-zinc-800/70 text-zinc-200'
                      : 'text-zinc-400 hover:bg-zinc-900 hover:text-zinc-300'
                  }`}
                  onClick={() => onSelectConversation(c.id)}
                >
                  <MessageSquare className="w-3.5 h-3.5 shrink-0" />
                  <span className="text-xs truncate flex-1">{c.title}</span>
                  <button
                    onClick={(e) => {
                      e.stopPropagation()
                      onDeleteConversation(c.id)
                    }}
                    className="p-0.5 rounded opacity-0 group-hover:opacity-100 hover:bg-zinc-700 text-zinc-500 hover:text-red-400 transition-all"
                    title="删除会话"
                  >
                    <Trash2 className="w-3 h-3" />
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      <div className="p-4 mt-auto border-t border-zinc-800/50 bg-zinc-900/20">
        <div className="text-xs font-medium text-zinc-500 mb-2">本地引擎状态</div>
        <div className="space-y-2">
          <div className="flex justify-between items-center text-xs">
            <span className="text-zinc-400">Obsidian 本地库</span>
            <span className="text-emerald-400 font-medium">连接正常</span>
          </div>
          <div className="flex justify-between items-center text-xs">
            <span className="text-zinc-400">Qdrant 向量空间</span>
            <span className="text-emerald-400 font-medium">已就绪</span>
          </div>
        </div>
      </div>
    </aside>
  )
}
