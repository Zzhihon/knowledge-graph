import { useState, useEffect, useCallback } from 'react'
import NavSidebar from './components/k/NavSidebar'
import ReaderSheet from './components/k/ReaderSheet'
import DailyView from './views/DailyView'
import LibraryView from './views/LibraryView'
import PlannedView from './views/PlannedView'
import { IncidentBadge, IncidentDrawer } from './components/k/IncidentBadge'
import { DEFAULT_MODULE, MODULES, isLive, moduleOf } from './lib/modules'
import { fetchOpenIncidents, usePoll } from './lib/overview'
import { ReaderContext, fetchOverview, type LibraryOverview } from './lib/library'
import { useTheme } from './lib/theme'
import Header from './components/Header'
import ExportModal from './components/ExportModal'
import IngestModal from './components/IngestModal'
import SearchOverlay from './components/SearchOverlay'
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
import InterviewView from './views/InterviewView'
import CourseKnowledgeView from './views/CourseKnowledgeView'
import EntryPreview from './components/EntryPreview'
import IntegrationPanel from './components/IntegrationPanel'
import { useRSSPull } from './hooks/useRSSPull'
import { useInterviewGenerate } from './hooks/useInterviewGenerate'
import { useCourseProcess } from './hooks/useCourseProcess'
import { useIntegrate } from './hooks/useIntegrate'
import { get, post, del } from './api/client'
import { Menu, Search } from 'lucide-react'
import type { SyncResult, ConversationListItem, ExamPaper } from './types'

// Pages, their groups and whether they are built yet live in lib/modules.ts. Reading pages use the
// AIHOT-derived design (styles/aihot.css); the older tool pages keep their shell with its header.
const tabFromHash = () => {
  const tab = location.hash.slice(1).split('&')[0].replace(/^read=.*/, '')
  return moduleOf(tab) ? tab : DEFAULT_MODULE
}

export default function App() {
  // #library, #projects … opens that page; #read=<path> also opens an entry, so views can be bookmarked.
  const [activeTab, setActiveTab] = useState(tabFromHash)
  const reading = moduleOf(activeTab)?.shell === 'reading'
  const [theme, setTheme] = useTheme()
  const [overview, setOverview] = useState<LibraryOverview>({ counts: {}, index: {}, dailies: [] })
  const [readerPath, setReaderPath] = useState<string | null>(() => decodeURIComponent(location.hash.match(/read=([^&]+)/)?.[1] ?? '') || null)

  useEffect(() => {
    const hash = [activeTab, readerPath && `read=${encodeURIComponent(readerPath)}`].filter(Boolean).join('&')
    history.replaceState(null, '', `#${hash}`)
  }, [activeTab, readerPath])
  const [isExportOpen, setIsExportOpen] = useState(false)
  const [isIngestOpen, setIsIngestOpen] = useState(false)
  const [isSearchOpen, setIsSearchOpen] = useState(false)
  const [isSyncing, setIsSyncing] = useState(false)
  const [isMenuOpen, setIsMenuOpen] = useState(false)
  const [isIncidentsOpen, setIsIncidentsOpen] = useState(false)
  const incidents = usePoll(fetchOpenIncidents)

  // Every in-app link goes through here. The incident list is a drawer until the #9 page ships.
  const navigate = useCallback((id: string) => {
    setIsMenuOpen(false)
    if (id === 'incidents' && !isLive('incidents')) { setIsIncidentsOpen(true); return }
    setActiveTab(moduleOf(id) ? id : DEFAULT_MODULE)
  }, [])

  const [conversations, setConversations] = useState<ConversationListItem[]>([])
  const [currentConversationId, setCurrentConversationId] = useState<string | null>(null)
  const [previewEntryId, setPreviewEntryId] = useState<string | null>(null)
  const [examData, setExamData] = useState<ExamPaper | null>(null)

  // RSS pull state — lives at App level so it persists across tab switches
  const [rssPullState, rssPullActions] = useRSSPull()

  // Interview generate state — lives at App level so it persists across tab switches
  const [interviewGenState, interviewGenActions] = useInterviewGenerate()

  // Course process state — lives at App level so it persists across tab switches
  const [courseProcessState, courseProcessActions] = useCourseProcess()

  // Integration gateway state — lives at App level, triggered after ingest
  const [integrateState, integrateActions] = useIntegrate()
  const [isIntegratePanelOpen, setIsIntegratePanelOpen] = useState(false)

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

  useEffect(() => {
    if (reading) fetchOverview().then(setOverview).catch(() => {})
  }, [activeTab, reading])

  // Global Cmd+K / Ctrl+K shortcut to toggle search overlay
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault()
        setIsSearchOpen(prev => !prev)
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [])

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

  const reader = { index: overview.index, open: setReaderPath }
  const current = moduleOf(activeTab)
  const openIncidents = () => setIsIncidentsOpen(true)
  const badge = (compact?: boolean) => (
    <IncidentBadge count={incidents.data?.count ?? null} error={incidents.error} onOpen={openIncidents} compact={compact} />
  )
  const nav = {
    activeTab,
    setActiveTab: navigate,
    theme,
    setTheme,
    onOpenSearch: () => { setIsMenuOpen(false); setIsSearchOpen(true) },
    conversations,
    currentConversationId,
    onSelectConversation: (id: string) => { setIsMenuOpen(false); handleSelectConversation(id) },
    onNewConversation: handleNewConversation,
    onDeleteConversation: handleDeleteConversation,
  }

  const readingPage = () => {
    if (!current) return null
    if (current.status === 'planned') return <PlannedView module={current} onNavigate={navigate} />
    if (current.view) { const View = current.view; return <View onNavigate={navigate} /> }
    switch (activeTab) {
      case 'daily': return <DailyView dailies={overview.dailies} />
      case 'library': return <LibraryView title="知识库" counts={overview.counts} />
      case 'projects': return <LibraryView title="项目" board="07-Projects" counts={overview.counts} />
      case 'lab': return <LibraryView title="实验" board="11-Experiments" counts={overview.counts} />
      default: return null
    }
  }

  return (
    <ReaderContext.Provider value={reader}>
    <div className="k-app flex h-[100dvh] flex-col md:flex-row">
      <NavSidebar {...nav} />

      {/* Narrow screens: a top bar with the menu, the page name, search and the 异常 badge. */}
      <div className="flex h-14 shrink-0 items-center gap-2 border-b border-k-line bg-k-sidebar px-2 md:hidden">
        <button onClick={() => setIsMenuOpen(true)} className="rounded-full p-2 text-k-ink2 hover:bg-k-sunk" aria-label="打开菜单" aria-expanded={isMenuOpen}>
          <Menu className="h-5 w-5" />
        </button>
        <span className="min-w-0 flex-1 truncate text-[16px] font-[650] text-k-ink">{current?.label ?? ''}</span>
        <button onClick={() => setIsSearchOpen(true)} className="rounded-full p-2 text-k-ink3 hover:bg-k-sunk" aria-label="搜索">
          <Search className="h-5 w-5" />
        </button>
        <span className="pr-1">{badge(true)}</span>
      </div>
      {isMenuOpen && (
        <div className="fixed inset-0 z-50 flex md:hidden" role="dialog" aria-modal="true" aria-label="导航">
          <NavSidebar {...nav} variant="drawer" onClose={() => setIsMenuOpen(false)} />
          <div className="flex-1 bg-[color-mix(in_srgb,var(--k-ink)_28%,transparent)]" onClick={() => setIsMenuOpen(false)} />
        </div>
      )}

      {reading ? (
        <main className="relative flex min-h-0 min-w-0 flex-1 flex-col">
          <div className="absolute right-6 top-5 z-20 hidden md:block">{badge()}</div>
          <div className="min-h-0 flex-1 overflow-y-auto">
            <div className="px-4 pb-10 pt-5 sm:px-10 sm:pt-8">{readingPage()}</div>
          </div>
        </main>
      ) : (
      <main className="flex-1 flex flex-col relative min-h-0 min-w-0 bg-k-bg text-k-ink2 font-sans selection:bg-k-accent/30">
        <Header
          extra={<span className="hidden md:inline-flex">{badge()}</span>}
          activeTab={activeTab}
          isSyncing={isSyncing}
          onSync={handleSync}
          onOpenIngest={() => setIsIngestOpen(true)}
          onOpenExport={() => setIsExportOpen(true)}
          onOpenIntegrate={() => setIsIntegratePanelOpen(true)}
          onOpenSearch={() => setIsSearchOpen(true)}
        />

        {/* Sync result notification bar */}
        {syncResult && (
          <div className="px-6 py-2.5 bg-k-ok-soft border-b border-k-ok/30 flex items-center justify-between animate-fade-in">
            <div className="flex items-center gap-4 text-sm">
              <span className="text-k-ok font-medium">✓ 同步完成</span>
              <span className="text-k-ink3">
                新增 <span className="text-k-ok">{syncResult.new}</span>
                {' · '}更新 <span className="text-k-amber-ink">{syncResult.changed}</span>
                {' · '}删除 <span className="text-k-hot">{syncResult.deleted}</span>
                {' · '}未变 <span className="text-k-ink4">{syncResult.unchanged}</span>
                {' · '}向量 <span className="text-k-accent">{syncResult.vector_upserted}</span>
              </span>
            </div>
            <button
              onClick={() => setSyncResult(null)}
              className="text-k-ink4 hover:text-k-ink2 text-xs"
            >
              关闭
            </button>
          </div>
        )}
        {syncError && (
          <div className="px-6 py-2.5 bg-k-hot-soft border-b border-k-hot/30 flex items-center justify-between animate-fade-in">
            <span className="text-sm text-k-hot">✗ 同步失败：{syncError}</span>
            <button
              onClick={() => setSyncError(null)}
              className="text-k-ink4 hover:text-k-ink2 text-xs"
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
              {activeTab === 'dashboard' && <DashboardView setActiveTab={navigate} onPreviewEntry={setPreviewEntryId} />}
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
                  onStartExam={(exam) => { setExamData(exam); navigate('exam') }}
                  setActiveTab={navigate}
                />
              )}
              {activeTab === 'exam' && (
                <ExamView
                  examData={examData}
                  onPreviewEntry={setPreviewEntryId}
                  setActiveTab={navigate}
                />
              )}
              {activeTab === 'health' && <HealthView onPreviewEntry={setPreviewEntryId} />}
              {activeTab === 'graph' && <GraphView onPreviewEntry={setPreviewEntryId} />}
              {activeTab === 'interview' && (
                <InterviewView
                  onPreviewEntry={setPreviewEntryId}
                  genState={interviewGenState}
                  genActions={interviewGenActions}
                />
              )}
              {activeTab === 'course' && (
                <CourseKnowledgeView
                  onPreviewEntry={setPreviewEntryId}
                  processState={courseProcessState}
                  processActions={courseProcessActions}
                />
              )}
              {activeTab === 'topics' && <TopicExplorerView />}
              {activeTab === 'rss' && <RSSView pullState={rssPullState} pullActions={rssPullActions} />}
            </div>
          </div>
        )}
      </main>
      )}

      {isExportOpen && <ExportModal onClose={() => setIsExportOpen(false)} />}
      {isIngestOpen && (
        <IngestModal
          onClose={() => setIsIngestOpen(false)}
          onIngestComplete={(entryIds) => {
            if (entryIds.length > 0) {
              integrateActions.start(entryIds)
              setIsIntegratePanelOpen(true)
            }
          }}
        />
      )}
      <SearchOverlay
        open={isSearchOpen}
        onClose={() => setIsSearchOpen(false)}
        onSelectEntry={(id) => setPreviewEntryId(id)}
        modules={MODULES}
        onNavigate={navigate}
      />
      {isIncidentsOpen && (
        <IncidentDrawer items={incidents.data?.items ?? []} error={incidents.error}
          onClose={() => setIsIncidentsOpen(false)} onNavigate={navigate} />
      )}
      {previewEntryId && (
        <EntryPreview
          entryId={previewEntryId}
          onClose={() => setPreviewEntryId(null)}
          onNavigate={(id) => setPreviewEntryId(id)}
        />
      )}
      {(isIntegratePanelOpen || integrateState.phase !== 'idle') && (
        <IntegrationPanel
          state={integrateState}
          actions={integrateActions}
          onClose={() => setIsIntegratePanelOpen(false)}
        />
      )}
      {readerPath && <ReaderSheet path={readerPath} onClose={() => setReaderPath(null)} />}
    </div>
    </ReaderContext.Provider>
  )
}
