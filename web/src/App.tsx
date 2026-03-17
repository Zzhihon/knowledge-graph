import { useState, useEffect, useCallback } from 'react'
import Sidebar from './components/Sidebar'
import Header from './components/Header'
import ExportModal from './components/ExportModal'
import IngestModal from './components/IngestModal'
import DashboardView from './views/DashboardView'
import AskView from './views/AskView'
import QuizView from './views/QuizView'
import HealthView from './views/HealthView'
import GraphView from './views/GraphView'
import ProblemBankView from './views/ProblemBankView'
import ExamView from './views/ExamView'
import RSSView from './views/RSSView'
import NetworkGraphView from './views/NetworkGraphView'
import TopicExplorerView from './views/TopicExplorerView'
import EntryPreview from './components/EntryPreview'
import { get, post, del } from './api/client'
import type { SyncResult, ConversationListItem, ExamPaper } from './types'

export default function App() {
  const [activeTab, setActiveTab] = useState('dashboard')
  const [isExportOpen, setIsExportOpen] = useState(false)
  const [isIngestOpen, setIsIngestOpen] = useState(false)
  const [isSyncing, setIsSyncing] = useState(false)

  const [conversations, setConversations] = useState<ConversationListItem[]>([])
  const [currentConversationId, setCurrentConversationId] = useState<string | null>(null)
  const [previewEntryId, setPreviewEntryId] = useState<string | null>(null)
  const [examData, setExamData] = useState<ExamPaper | null>(null)

  const refreshConversations = useCallback(async () => {
    try {
      const list = await get<ConversationListItem[]>('/conversations')
      setConversations(list)
    } catch {
      // backend may not be up yet
    }
  }, [])

  useEffect(() => {
    refreshConversations()
  }, [refreshConversations])

  const handleSelectConversation = (id: string) => {
    setCurrentConversationId(id)
    setActiveTab('ask')
  }

  const handleNewConversation = () => {
    setCurrentConversationId(null)
    setActiveTab('ask')
  }

  const handleDeleteConversation = async (id: string) => {
    try {
      await del<{ ok: boolean }>(`/conversations/${id}`)
      if (currentConversationId === id) setCurrentConversationId(null)
      await refreshConversations()
    } catch {
      // ignore
    }
  }

  const handleConversationCreated = (id: string) => {
    setCurrentConversationId(id)
    refreshConversations()
  }

  const [syncResult, setSyncResult] = useState<SyncResult | null>(null)
  const [syncError, setSyncError] = useState<string | null>(null)

  const handleSync = async () => {
    setIsSyncing(true)
    setSyncResult(null)
    setSyncError(null)
    try {
      const result = await post<SyncResult>('/sync', { full: false })
      setSyncResult(result)
      // Auto-dismiss after 5 seconds
      setTimeout(() => setSyncResult(null), 5000)
    } catch (err) {
      setSyncError(err instanceof Error ? err.message : '同步失败')
      setTimeout(() => setSyncError(null), 4000)
    } finally {
      setIsSyncing(false)
    }
  }

  return (
    <div className="flex h-screen bg-[#09090b] text-zinc-300 font-sans selection:bg-indigo-500/30">
      <Sidebar
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        conversations={conversations}
        currentConversationId={currentConversationId}
        onSelectConversation={handleSelectConversation}
        onNewConversation={handleNewConversation}
        onDeleteConversation={handleDeleteConversation}
      />

      <main className="flex-1 flex flex-col relative border-l border-zinc-800/50 bg-zinc-950/50">
        <Header
          activeTab={activeTab}
          isSyncing={isSyncing}
          onSync={handleSync}
          onOpenIngest={() => setIsIngestOpen(true)}
          onOpenExport={() => setIsExportOpen(true)}
        />

        {/* Sync result notification bar */}
        {syncResult && (
          <div className="px-6 py-2.5 bg-emerald-950/40 border-b border-emerald-800/30 flex items-center justify-between animate-fade-in">
            <div className="flex items-center gap-4 text-sm">
              <span className="text-emerald-400 font-medium">✓ 同步完成</span>
              <span className="text-zinc-400">
                新增 <span className="text-emerald-400">{syncResult.new}</span>
                {' · '}更新 <span className="text-amber-400">{syncResult.changed}</span>
                {' · '}删除 <span className="text-rose-400">{syncResult.deleted}</span>
                {' · '}未变 <span className="text-zinc-500">{syncResult.unchanged}</span>
                {' · '}向量 <span className="text-indigo-400">{syncResult.qdrant_upserted}</span>
              </span>
            </div>
            <button
              onClick={() => setSyncResult(null)}
              className="text-zinc-500 hover:text-zinc-300 text-xs"
            >
              关闭
            </button>
          </div>
        )}
        {syncError && (
          <div className="px-6 py-2.5 bg-rose-950/40 border-b border-rose-800/30 flex items-center justify-between animate-fade-in">
            <span className="text-sm text-rose-400">✗ 同步失败：{syncError}</span>
            <button
              onClick={() => setSyncError(null)}
              className="text-zinc-500 hover:text-zinc-300 text-xs"
            >
              关闭
            </button>
          </div>
        )}

        {activeTab === 'network' ? (
          <div className="flex-1 overflow-hidden">
            <NetworkGraphView onPreviewEntry={setPreviewEntryId} />
          </div>
        ) : (
          <div className="flex-1 overflow-y-auto p-8 scroll-smooth">
            <div className="max-w-5xl mx-auto h-full">
              {activeTab === 'dashboard' && <DashboardView setActiveTab={setActiveTab} />}
              {activeTab === 'ask' && (
                <AskView
                  conversationId={currentConversationId}
                  onConversationCreated={handleConversationCreated}
                  onPreviewEntry={setPreviewEntryId}
                />
              )}
              {activeTab === 'quiz' && <QuizView onPreviewEntry={setPreviewEntryId} />}
              {activeTab === 'problems' && (
                <ProblemBankView
                  onPreviewEntry={setPreviewEntryId}
                  onStartExam={(exam) => { setExamData(exam); setActiveTab('exam') }}
                  setActiveTab={setActiveTab}
                />
              )}
              {activeTab === 'exam' && (
                <ExamView
                  examData={examData}
                  onPreviewEntry={setPreviewEntryId}
                  setActiveTab={setActiveTab}
                />
              )}
              {activeTab === 'health' && <HealthView onPreviewEntry={setPreviewEntryId} />}
              {activeTab === 'graph' && <GraphView onPreviewEntry={setPreviewEntryId} />}
              {activeTab === 'topics' && <TopicExplorerView />}
              {activeTab === 'rss' && <RSSView />}
            </div>
          </div>
        )}
      </main>

      {isExportOpen && <ExportModal onClose={() => setIsExportOpen(false)} />}
      {isIngestOpen && <IngestModal onClose={() => setIsIngestOpen(false)} />}
      {previewEntryId && (
        <EntryPreview
          entryId={previewEntryId}
          onClose={() => setPreviewEntryId(null)}
          onNavigate={(id) => setPreviewEntryId(id)}
        />
      )}
    </div>
  )
}
