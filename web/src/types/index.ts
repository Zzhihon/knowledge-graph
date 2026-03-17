export interface Stats {
  total_items: number
  needs_review: number
  avg_confidence: number
  domains: string[]
  type_counts: Record<string, number>
  layer_counts: Record<string, number>
}

export interface RadarData {
  [domain: string]: {
    coverage: number
    depth_score: number
    freshness: number
    avg_confidence: number
    total_entries: number
  }
}

export interface SearchResult {
  id: string
  score: number
  title: string
  domain: string
  type: string
  depth: string
  snippet: string
  file_path: string
  metadata: Record<string, unknown>
}

export interface Source {
  id: string
  title: string
  domain: string
  score: number
}

export interface QuizEntry {
  id: string
  title: string
  question: string
  context: string
  answer: string
  tags: string[]
  layer: string
  confidence: number | null
  file_path: string
}

export interface ScoreResult {
  ok: boolean
  new_confidence: number | null
  next_review: string
}

export interface HealthItem {
  id: string
  title: string
  last_updated: string
  confidence: number | null
  status: string
  domain: string
  file_path: string
}

export interface HealthReview {
  outdated: HealthItem[]
  low_confidence: HealthItem[]
  drafts: HealthItem[]
}

export interface GapAnalysis {
  [domain: string]: {
    label: string
    icon: string
    entry_count: number
    defined_sub_domains: string[]
    covered_sub_domains: string[]
    missing_sub_domains: string[]
    coverage_percent: number
  }
}

export interface LinkSuggestion {
  source_title: string
  target_title: string
  source_id: string
  target_id: string
  similarity: number
  source: string
}

export interface DiffRecord {
  change_type: string
  timestamp: string
  diff_text: string
  entry_id?: string
  stats: {
    additions: number
    deletions: number
  }
}

export interface HistoryEntry {
  id: string
  title: string
  created: string
  confidence: number | null
  status: string
  is_current: boolean
}

export interface ExportResult {
  content: string
  file_path: string
}

export interface QualityInfo {
  action: 'create' | 'merge' | 'skip'
  novelty_score: number | null
  quality_score: number | null
  reason: string
}

export interface IngestEntry {
  id: string
  title: string
  directory: string
  action: string
  novelty_score: number | null
  quality_score: number | null
  reason: string
}

export interface IngestResult {
  created: number
  merged: number
  skipped: number
  entries: IngestEntry[]
}

export interface BatchFileResult {
  file_path: string
  status: 'success' | 'error' | 'unsupported'
  entries_created: number
  entries_merged: number
  entries_skipped: number
  entries: IngestEntry[]
  error: string | null
}

export interface BatchIngestResult {
  total_files: number
  processed: number
  entries_created: number
  entries_merged: number
  entries_skipped: number
  errors: { file: string; error: string }[]
  file_results: BatchFileResult[]
}

export interface CrossDomainInsight {
  domain_a: string
  domain_b: string
  entry_a_id: string
  entry_a_title: string
  entry_b_id: string
  entry_b_title: string
  similarity: number
  description: string
}

export interface Backlink {
  source_id: string
  source_title: string
  source_domain: string
  link_type: 'graph_relation' | 'wiki_link'
  rel_type: string
}

export interface ConversationListItem {
  id: string
  title: string
  mode: string
  created_at: string
  updated_at: string
}

export interface ChatMessage {
  id: number
  role: 'user' | 'assistant'
  content: string
  sources: Source[] | null
  created_at: string
}

export interface Conversation extends ConversationListItem {
  messages: ChatMessage[]
}

export interface EntryDetail {
  id: string
  title: string
  domain: string | string[]
  type: string
  depth: string
  status: string
  confidence: number | null
  tags: string[]
  created: string
  updated: string
  review_date: string
  related: string[]
  difficulty: string | null
  content: string
  file_path: string
  relative_path: string
  vault_name: string
}

export interface DistillGroup {
  group_id: number
  entry_ids: string[]
  titles: string[]
  domains: string[]
  avg_similarity: number
}

export interface DistillResult {
  new_entry_id: string
  new_entry_title: string
  new_entry_path: string
  superseded_ids: string[]
  deleted_count: number
}

export interface SyncResult {
  new: number
  changed: number
  deleted: number
  unchanged: number
  qdrant_upserted: number
  graph_upserted: number
  edges_created: number
}

// --- Network Graph types ---

export interface NetworkNode {
  id: string
  title: string
  domain: string[]
  type: string
  depth: string
  status: string
  confidence: number | null
  tags: string[]
}

export interface NetworkEdge {
  source: string
  target: string
  type: 'references' | 'prerequisites' | 'supersedes'
}

export interface NetworkData {
  nodes: NetworkNode[]
  edges: NetworkEdge[]
  meta: {
    domains: string[]
    edge_types: string[]
    node_count: number
    edge_count: number
  }
}

// --- Problem Bank types ---

export interface ProblemListItem {
  id: string
  title: string
  leetcode_id: number | null
  difficulty: string
  pattern: string[]
  companies: string[]
  confidence: number | null
  tags: string[]
  review_date: string
  file_path: string
}

export interface ProblemListResponse {
  items: ProblemListItem[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

export interface PatternInfo {
  name: string
  chinese_name: string
  status: 'active' | 'pending'
  problem_count: number
  anchors: number[]
}

export interface ProblemStats {
  total_problems: number
  difficulty_distribution: Record<string, number>
  pattern_coverage: string
  active_patterns: number
  total_patterns: number
  covered_patterns: string[]
  needs_review: number
}

export interface ExamProblem {
  id: string
  title: string
  leetcode_id: number | null
  difficulty: string
  pattern: string[]
  companies: string[]
  time_estimate: number
  content: string
  file_path: string
}

export interface ExamPaper {
  problems: ExamProblem[]
  total_time: number
  difficulty_distribution: Record<string, number>
  pattern_coverage: string[]
}

// --- Domain Overview types ---

export interface DomainMetrics {
  coverage: number
  depth_score: number
  freshness: number
  avg_confidence: number
  total_entries: number
}

export interface DomainEntry {
  id: string
  title: string
  type: string
  depth: string
  confidence: number
  status: string
  domain: string[]
}

export interface DomainOverview {
  key: string
  label: string
  icon: string
  sub_domains: string[]
  metrics: DomainMetrics
  entries: DomainEntry[]
}

export interface DomainsOverviewResponse {
  domains: DomainOverview[]
}

