// Every page of kg-web in one list. The sidebar, the mobile drawer, the router in App.tsx and the
// page search all read it, so adding a page is one line here.
//
// A module that is not built yet stays here as `status: 'planned'` with its issue number: the
// sidebar shows it greyed out with "规划中 #n" and opening it explains what is coming. To ship one,
// set `status: 'live'` and give it a `view` that takes no props, or only `onNavigate`.
import type { ComponentType } from 'react'
import {
  Zap, List, LayoutGrid, FlaskConical, Search, Share2, Boxes, GitMerge, LayoutDashboard, AlertTriangle, Rss,
  Play, Library, Briefcase, GraduationCap, Inbox, Workflow, ClipboardCheck, Home, Server, Globe, Package,
  Siren, FolderKanban, FileText,
} from 'lucide-react'
import HubView from '../views/HubView'
import RulesRunsView from '../views/RulesRunsView'
import EvalLabelView from '../views/EvalLabelView'
import OverviewView from '../views/OverviewView'

export const GROUPS = ['总览', '知识', '自动化', '运维', '进行中', '工具', '练习'] as const
export type Group = (typeof GROUPS)[number]

/** What a registered view may receive. Keep it this small so modules plug in without App changes. */
export interface ModuleViewProps { onNavigate: (id: string) => void }

export interface Module {
  id: string
  group: Group
  label: string
  icon: ComponentType<{ className?: string }>
  status: 'live' | 'planned'
  /** The issue that builds or tracks it. */
  issue?: number
  /** Rendered as <view onNavigate={…} />. Pages that need App state are rendered in App.tsx instead. */
  view?: ComponentType<ModuleViewProps>
  /** 'reading' pages use the AIHOT layout; 'tool' pages keep the older shell with its header. */
  shell?: 'reading' | 'tool'
  /** One line for planned pages and the page search. */
  blurb?: string
  /** Reachable from another page but not listed in the sidebar. */
  hidden?: boolean
}

export const MODULES: Module[] = [
  { id: 'overview', group: '总览', label: '总览', icon: Home, status: 'live', issue: 11, view: OverviewView, shell: 'reading',
    blurb: '要你处理的、出问题的、在推进的' },

  { id: 'daily', group: '知识', label: '日报', icon: Zap, status: 'live', shell: 'reading' },
  { id: 'library', group: '知识', label: '知识库', icon: List, status: 'live', shell: 'reading' },
  { id: 'projects', group: '知识', label: '项目', icon: LayoutGrid, status: 'live', shell: 'reading' },
  { id: 'lab', group: '知识', label: '实验', icon: FlaskConical, status: 'live', shell: 'reading' },
  { id: 'eval', group: '知识', label: '评测', icon: ClipboardCheck, status: 'live', issue: 8, view: EvalLabelView, shell: 'reading',
    blurb: '审核检索评测集' },

  { id: 'hub', group: '自动化', label: '中台', icon: Inbox, status: 'live', issue: 8, view: HubView, shell: 'reading',
    blurb: '待处理卡片、花费和处理过的卡片' },
  { id: 'rules', group: '自动化', label: '规则与运行', icon: Workflow, status: 'live', issue: 8, view: RulesRunsView, shell: 'reading',
    blurb: '定时和事件规则，运行记录' },

  { id: 'services', group: '运维', label: '本地服务', icon: Server, status: 'planned', issue: 3, shell: 'reading',
    blurb: '本机的 launchd 任务和监听端口：哪些在跑、哪些挂了、24 小时可用性。' },
  { id: 'egress', group: '运维', label: '出口', icon: Globe, status: 'planned', issue: 4, shell: 'reading',
    blurb: '代理出口的探测记录：哪一段断了、出口身份对不对、休眠唤醒不误报。' },
  { id: 'artifacts', group: '运维', label: '工件', icon: Package, status: 'planned', issue: 6, shell: 'reading',
    blurb: '分享出去的页面和文件：在哪、谁能看、什么时候过期、有没有敏感内容。' },
  { id: 'incidents', group: '运维', label: '事故', icon: Siren, status: 'planned', issue: 9, shell: 'reading',
    blurb: '事故的开、关、证据和自愈记录，结案后沉淀成知识条目。右上角的"异常"徽标已经能列出未关闭的事故。' },

  { id: 'inprogress', group: '进行中', label: '项目与会话', icon: FolderKanban, status: 'planned', issue: 5, shell: 'reading',
    blurb: '最近活跃的项目和会话，哪些在等你回答。' },

  { id: 'ask', group: '工具', label: '探索与问答', icon: Search, status: 'live', shell: 'tool' },
  { id: 'network', group: '工具', label: '知识网络', icon: Share2, status: 'live', shell: 'tool' },
  { id: 'topics', group: '工具', label: '主题总览', icon: Boxes, status: 'live', shell: 'tool' },
  { id: 'graph', group: '工具', label: '图谱演进', icon: GitMerge, status: 'live', shell: 'tool' },
  { id: 'dashboard', group: '工具', label: '概览仪表盘', icon: LayoutDashboard, status: 'live', shell: 'tool' },
  { id: 'health', group: '工具', label: '健康巡检', icon: AlertTriangle, status: 'live', shell: 'tool' },
  { id: 'rss', group: '工具', label: 'RSS 摄取', icon: Rss, status: 'live', shell: 'tool' },

  { id: 'quiz', group: '练习', label: '间隔测验', icon: Play, status: 'live', shell: 'tool' },
  { id: 'problems', group: '练习', label: '算法题库', icon: Library, status: 'live', shell: 'tool' },
  { id: 'interview', group: '练习', label: '面试题库', icon: Briefcase, status: 'live', shell: 'tool' },
  { id: 'course', group: '练习', label: '课程知识', icon: GraduationCap, status: 'live', shell: 'tool' },
  { id: 'exam', group: '练习', label: '模拟面试', icon: FileText, status: 'live', shell: 'tool', hidden: true },
]

export const DEFAULT_MODULE = 'overview'
const BY_ID = new Map(MODULES.map((m) => [m.id, m]))
export const moduleOf = (id: string) => BY_ID.get(id)
export const isLive = (id: string) => BY_ID.get(id)?.status === 'live'
export { issueUrl } from './overview'

/** Sidebar groups in order, hidden pages left out. */
export const grouped = (): [Group, Module[]][] =>
  GROUPS.map((g) => [g, MODULES.filter((m) => m.group === g && !m.hidden)] as [Group, Module[]]).filter(([, ms]) => ms.length > 0)
