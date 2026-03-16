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

  const handleSync = async () => {
    setIsSyncing(true)
    try {
      await post<SyncResult>('/sync', { full: false })
    } catch {
      // Error handling
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
          </div>
        </div>
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
