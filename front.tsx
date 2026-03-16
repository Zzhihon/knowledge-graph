import React, { useState, useEffect, useRef } from 'react';
import { 
  LayoutDashboard, MessageSquare, Brain, Activity, 
  Network, RefreshCw, Download, Upload, Search, 
  ChevronRight, CheckCircle2, AlertTriangle, Play,
  BookOpen, Code2, Database, GitCommit, GitMerge,
  Clock, FileText, Settings, Sparkles, X, Plus,
  BrainCircuit
} from 'lucide-react';

// --- MOCK DATA ---
const mockStats = {
  totalItems: 342,
  needsReview: 15,
  avgConfidence: 0.78,
  domains: ['算法', 'Golang', '云原生', '分布式系统']
};

const mockHealthGaps = [
  { id: 1, type: '过时', title: 'Go GMP 调度模型', lastUpdated: '2023-05-12', confidence: 0.8 },
  { id: 2, type: '薄弱', title: 'KMP 字符串匹配原理', lastUpdated: '2024-01-20', confidence: 0.4 },
  { id: 3, type: '草稿', title: 'Raft 协议选主逻辑', lastUpdated: '2024-02-05', confidence: null },
  { id: 4, type: '空白', title: '动态规划模式模板', lastUpdated: null, confidence: null },
];

const mockQuizCards = [
  {
    id: 1,
    layer: '01-原理层',
    title: '滑动窗口的时间复杂度',
    question: '为什么滑动窗口是 O(n) 而不是 O(n²)？',
    answer: '虽然有内外两层循环（或 while），但左右指针 left 和 right 各自最多遍历数组一次。也就是每个元素最多进窗口一次、出窗口一次。2n 次操作，忽略常数项即为 O(n)。',
    tags: ['算法', '滑动窗口']
  },
  {
    id: 2,
    layer: '02-模式层',
    title: 'Go Goroutine 栈增长',
    question: 'Go 语言的 Goroutine 栈是如何扩容的？',
    answer: 'Go 1.3 之前使用分段栈（Segmented Stacks），但存在 "Hot split" 性能问题。Go 1.4 之后改为连续栈（Continuous Stacks）。当栈空间不足时，会分配一块原来两倍大的新内存，并将旧栈数据拷贝过去，然后更新所有指针。',
    tags: ['Golang', '底层机制']
  }
];

export default function App() {
  const [activeTab, setActiveTab] = useState('dashboard'); // dashboard, ask, quiz, health, graph
  const [isExportOpen, setIsExportOpen] = useState(false);
  const [isIngestOpen, setIsIngestOpen] = useState(false);
  const [isSyncing, setIsSyncing] = useState(false);

  const handleSync = () => {
    setIsSyncing(true);
    setTimeout(() => setIsSyncing(false), 1500);
  };

  return (
    <div className="flex h-screen bg-[#09090b] text-zinc-300 font-sans selection:bg-indigo-500/30">
      
      {/* 侧边导航栏 */}
      <Sidebar activeTab={activeTab} setActiveTab={setActiveTab} />

      {/* 主内容区 */}
      <main className="flex-1 flex flex-col relative border-l border-zinc-800/50 bg-zinc-950/50">
        
        {/* 顶部全局操作栏 */}
        <header className="h-14 border-b border-zinc-800/50 flex items-center justify-between px-6 bg-[#09090b]/80 backdrop-blur-md z-10">
          <div className="flex items-center gap-2 text-sm text-zinc-400">
            <Database className="w-4 h-4 text-zinc-500" />
            <span>Vault</span>
            <ChevronRight className="w-4 h-4 text-zinc-600" />
            <span className="text-zinc-100 font-medium">
              {activeTab === 'dashboard' && '概览仪表盘'}
              {activeTab === 'ask' && '智能问答与检索'}
              {activeTab === 'quiz' && '间隔重复测验'}
              {activeTab === 'health' && '知识健康巡检'}
              {activeTab === 'graph' && '图谱与演进链路'}
            </span>
          </div>
          
          <div className="flex items-center gap-3">
            {/* 全局快捷操作，替代 CLI 强记忆 */}
            <ActionButton 
              icon={<RefreshCw className={isSyncing ? "animate-spin" : ""} />} 
              label="同步索引" 
              cmd="kg sync" 
              onClick={handleSync} 
            />
            <ActionButton 
              icon={<Upload />} 
              label="导入知识" 
              cmd="kg ingest" 
              onClick={() => setIsIngestOpen(true)} 
            />
            <ActionButton 
              icon={<Download />} 
              label="导出文档" 
              cmd="kg export" 
              onClick={() => setIsExportOpen(true)} 
              primary 
            />
          </div>
        </header>

        {/* 动态视图渲染 */}
        <div className="flex-1 overflow-y-auto p-8 scroll-smooth">
          <div className="max-w-5xl mx-auto h-full">
            {activeTab === 'dashboard' && <DashboardView setActiveTab={setActiveTab} />}
            {activeTab === 'ask' && <AskView />}
            {activeTab === 'quiz' && <QuizView />}
            {activeTab === 'health' && <HealthView />}
            {activeTab === 'graph' && <GraphView />}
          </div>
        </div>
      </main>

      {/* 弹窗组件 */}
      {isExportOpen && <ExportModal onClose={() => setIsExportOpen(false)} />}
      {isIngestOpen && <IngestModal onClose={() => setIsIngestOpen(false)} />}
    </div>
  );
}

// --- 视图组件 ---

function DashboardView({ setActiveTab }) {
  return (
    <div className="space-y-10 animate-in fade-in duration-500 pb-10">
      <div>
        <h1 className="text-2xl font-semibold text-zinc-100 mb-2">欢迎回来，开始构建体系</h1>
        <p className="text-zinc-500 text-sm">Markdown 为源，AI 为引擎，间隔重复为节奏。</p>
      </div>

      {/* 数据统计网格 */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <StatCard title="活跃知识节点" value={mockStats.totalItems} subValue="基于 Obsidian" icon={<FileText />} />
        <StatCard title="待复习队列" value={mockStats.needsReview} subValue="今日需处理" icon={<Clock />} alert />
        <StatCard title="全局置信度" value={`${(mockStats.avgConfidence * 100).toFixed(0)}%`} subValue="SM-2 算法评估" icon={<Activity />} />
        <StatCard title="覆盖知识域" value={mockStats.domains.length} subValue="算法、Go等" icon={<LayoutDashboard />} />
      </div>

      {/* 三层架构卡片 */}
      <div className="space-y-4">
        <h2 className="text-sm font-medium text-zinc-400">三层知识架构概览</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <ArchitectureCard 
            title="01 原理层 (Principles)" 
            desc="回答「为什么」。核心概念、底层机制与推导过程。" 
            count={124} 
            icon={<BookOpen className="text-indigo-400" />} 
          />
          <ArchitectureCard 
            title="02 模式层 (Patterns)" 
            desc="回答「怎么做」。最佳实践、代码模板与通用范式。" 
            count={42} 
            icon={<Brain className="text-emerald-400" />} 
          />
          <ArchitectureCard 
            title="08 实战层 (Problems)" 
            desc="回答「用起来」。LeetCode 题解、Bug 复盘与真实案例。" 
            count={176} 
            icon={<Code2 className="text-amber-400" />} 
          />
        </div>
      </div>

      {/* 快捷入口区块 */}
      <div className="space-y-4">
        <h2 className="text-sm font-medium text-zinc-400">快速开始</h2>
        <div className="grid grid-cols-2 gap-4">
          <div 
            onClick={() => setActiveTab('ask')}
            className="p-5 border border-zinc-800/80 bg-zinc-900/30 rounded-xl hover:border-indigo-500/50 hover:bg-indigo-500/5 cursor-pointer transition-all flex items-start gap-4"
          >
            <div className="p-2.5 bg-zinc-800/50 rounded-lg text-indigo-400"><Search className="w-5 h-5" /></div>
            <div>
              <h3 className="text-zinc-200 font-medium mb-1">查阅与问答</h3>
              <p className="text-zinc-500 text-sm mb-2">基于 Qdrant + 图谱的 RAG 增强检索</p>
              <span className="text-xs text-zinc-600 font-mono bg-zinc-900 px-1.5 py-0.5 rounded">kg ask / query</span>
            </div>
          </div>

          <div 
            onClick={() => setActiveTab('quiz')}
            className="p-5 border border-zinc-800/80 bg-zinc-900/30 rounded-xl hover:border-emerald-500/50 hover:bg-emerald-500/5 cursor-pointer transition-all flex items-start gap-4"
          >
            <div className="p-2.5 bg-zinc-800/50 rounded-lg text-emerald-400"><Play className="w-5 h-5" /></div>
            <div>
              <h3 className="text-zinc-200 font-medium mb-1">开始今日测验</h3>
              <p className="text-zinc-500 text-sm mb-2">清空复习队列，对抗遗忘曲线</p>
              <span className="text-xs text-zinc-600 font-mono bg-zinc-900 px-1.5 py-0.5 rounded">kg quiz -i</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function AskView() {
  const [messages, setMessages] = useState([
    { role: 'assistant', content: '你好，我是你的 Vault 知识助理。你可以直接向我提问（如："滑动窗口和双指针的区别"），我会调用图谱进行 RAG 回答；或者输入搜索词进行语义检索。' }
  ]);
  const [input, setInput] = useState('');
  const [mode, setMode] = useState('ask'); // ask, query
  
  const handleSend = (e) => {
    e.preventDefault();
    if (!input.trim()) return;
    setMessages(prev => [...prev, { role: 'user', content: input }]);
    setInput('');
    
    // Mock response
    setTimeout(() => {
      setMessages(prev => [...prev, { 
        role: 'assistant', 
        content: `这是基于本地知识库的检索结果：\n\n滑动窗口是双指针的一种特例。双指针通常包括「对撞指针」和「快慢指针」，而滑动窗口本质上是维护一个满足特定条件的「同向双指针」区间。\n\n**参考节点：**\n- [02-Patterns/滑动窗口模板]\n- [01-Principles/双指针核心思想]` 
      }]);
    }, 1000);
  };

  return (
    <div className="flex flex-col h-[calc(100vh-8rem)] bg-zinc-900/40 border border-zinc-800/80 rounded-2xl overflow-hidden">
      {/* 模式切换头 */}
      <div className="flex p-2 bg-zinc-900/80 border-b border-zinc-800/80">
        <button 
          onClick={() => setMode('ask')}
          className={`flex-1 py-1.5 text-sm font-medium rounded-lg transition-colors flex items-center justify-center gap-2 ${mode === 'ask' ? 'bg-zinc-800 text-zinc-100' : 'text-zinc-500 hover:text-zinc-300'}`}
        >
          <Sparkles className="w-4 h-4" /> 综合问答 (Ask)
        </button>
        <button 
          onClick={() => setMode('query')}
          className={`flex-1 py-1.5 text-sm font-medium rounded-lg transition-colors flex items-center justify-center gap-2 ${mode === 'query' ? 'bg-zinc-800 text-zinc-100' : 'text-zinc-500 hover:text-zinc-300'}`}
        >
          <Search className="w-4 h-4" /> 语义检索 (Query)
        </button>
      </div>

      {/* 聊天内容区 */}
      <div className="flex-1 overflow-y-auto p-6 space-y-6">
        {messages.map((msg, i) => (
          <div key={i} className={`flex gap-4 ${msg.role === 'user' ? 'flex-row-reverse' : ''}`}>
            <div className={`w-8 h-8 rounded-full flex items-center justify-center shrink-0 ${msg.role === 'user' ? 'bg-indigo-500/20 text-indigo-400' : 'bg-zinc-800 text-zinc-300'}`}>
              {msg.role === 'user' ? <Search className="w-4 h-4" /> : <BrainCircuit className="w-4 h-4" />}
            </div>
            <div className={`max-w-[80%] rounded-2xl px-5 py-3 text-sm leading-relaxed ${msg.role === 'user' ? 'bg-indigo-600/10 text-indigo-100 border border-indigo-500/20' : 'bg-zinc-800/40 border border-zinc-700/50 text-zinc-300'}`}>
              <div className="whitespace-pre-wrap">{msg.content}</div>
            </div>
          </div>
        ))}
      </div>

      {/* 输入框 */}
      <div className="p-4 border-t border-zinc-800/80 bg-zinc-900/50">
        <form onSubmit={handleSend} className="relative flex items-center">
          <input 
            type="text" 
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder={mode === 'ask' ? "询问知识库中的问题..." : "输入关键词进行混合检索..."}
            className="w-full bg-zinc-950 border border-zinc-700/80 text-zinc-200 rounded-xl pl-4 pr-24 py-3.5 focus:outline-none focus:border-indigo-500/50 focus:ring-1 focus:ring-indigo-500/50 transition-all text-sm"
          />
          <div className="absolute right-2 flex items-center gap-2">
            <span className="text-[10px] text-zinc-600 font-mono hidden sm:block">
              {mode === 'ask' ? 'kg ask' : 'kg query'}
            </span>
            <button 
              type="submit"
              disabled={!input.trim()}
              className="px-4 py-1.5 bg-zinc-100 hover:bg-white text-zinc-900 disabled:opacity-50 disabled:cursor-not-allowed rounded-lg text-sm font-medium transition-colors"
            >
              发送
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

function QuizView() {
  const [quizState, setQuizState] = useState('setup'); // setup, playing, finished
  const [currentIndex, setCurrentIndex] = useState(0);
  const [isFlipped, setIsFlipped] = useState(false);
  const [domain, setDomain] = useState('algorithm');
  const [count, setCount] = useState(5);

  const startQuiz = () => setQuizState('playing');
  
  const handleScore = () => {
    setIsFlipped(false);
    if (currentIndex < mockQuizCards.length - 1) {
      setCurrentIndex(prev => prev + 1);
    } else {
      setQuizState('finished');
    }
  };

  if (quizState === 'setup') {
    return (
      <div className="max-w-md mx-auto mt-10 p-8 border border-zinc-800/80 bg-zinc-900/40 rounded-2xl animate-in fade-in">
        <div className="flex items-center gap-3 mb-6">
          <div className="p-2 bg-indigo-500/10 text-indigo-400 rounded-lg"><Play className="w-5 h-5" /></div>
          <h2 className="text-xl font-semibold text-zinc-100">配置测验任务</h2>
        </div>
        <div className="space-y-5">
          <div>
            <label className="block text-sm text-zinc-400 mb-2">选择知识域</label>
            <select value={domain} onChange={e => setDomain(e.target.value)} className="w-full bg-zinc-950 border border-zinc-800 rounded-lg p-2.5 text-zinc-300 outline-none focus:border-indigo-500 text-sm">
              <option value="all">全量域 (All)</option>
              <option value="algorithm">算法 (Algorithm)</option>
              <option value="golang">Golang</option>
            </select>
          </div>
          <div>
            <label className="block text-sm text-zinc-400 mb-2">抽取题目数量</label>
            <input type="number" value={count} onChange={e => setCount(e.target.value)} className="w-full bg-zinc-950 border border-zinc-800 rounded-lg p-2.5 text-zinc-300 outline-none focus:border-indigo-500 text-sm" />
          </div>
          <div className="pt-4 border-t border-zinc-800 flex justify-between items-center">
            <span className="text-xs font-mono text-zinc-500">kg quiz --domain {domain} --count {count}</span>
            <button onClick={startQuiz} className="px-5 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-sm font-medium transition-colors">
              开始测验
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (quizState === 'finished') {
    return (
      <div className="h-[60vh] flex flex-col items-center justify-center animate-in zoom-in duration-500">
        <div className="w-16 h-16 rounded-full bg-emerald-500/10 flex items-center justify-center mb-6 border border-emerald-500/20">
          <CheckCircle2 className="w-8 h-8 text-emerald-500" />
        </div>
        <h2 className="text-2xl font-semibold text-zinc-100 mb-2">测验完成</h2>
        <p className="text-zinc-500 text-sm mb-8">复习间隔已根据 SM-2 算法自动更新至 Markdown 元数据中。</p>
        <button onClick={() => { setQuizState('setup'); setCurrentIndex(0); }} className="px-5 py-2.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-200 rounded-lg text-sm font-medium transition-colors">
          返回配置页
        </button>
      </div>
    );
  }

  const currentCard = mockQuizCards[currentIndex];

  return (
    <div className="flex flex-col items-center justify-center py-4 animate-in fade-in h-[calc(100vh-12rem)]">
      <div className="w-full max-w-2xl h-full flex flex-col">
        {/* 顶部进度条 */}
        <div className="flex items-center justify-between mb-6">
          <span className="text-xs font-mono text-zinc-500 bg-zinc-900 px-2 py-1 rounded">kg quiz -i</span>
          <span className="text-sm font-medium text-zinc-400">
            进度 {currentIndex + 1} / {mockQuizCards.length}
          </span>
        </div>

        {/* 沉浸式闪卡区域 */}
        <div className="relative perspective-1000 flex-1">
          <div className={`w-full h-full absolute transition-all duration-500 transform-style-3d ${isFlipped ? 'rotate-y-180' : ''}`}>
            
            {/* 卡片正面（问题） */}
            <div className={`absolute inset-0 backface-hidden flex flex-col bg-zinc-900/50 border border-zinc-800 rounded-2xl p-8 shadow-xl ${isFlipped ? 'invisible' : 'visible'}`}>
              <div className="flex gap-2 mb-6">
                <span className="px-2 py-1 bg-zinc-800 text-zinc-400 text-xs rounded-md">{currentCard.layer}</span>
                {currentCard.tags.map(t => (
                  <span key={t} className="px-2 py-1 bg-indigo-500/10 text-indigo-400 text-xs rounded-md">{t}</span>
                ))}
              </div>
              <div className="flex-1 flex items-center justify-center">
                <h3 className="text-2xl text-zinc-100 font-medium leading-relaxed text-center">{currentCard.question}</h3>
              </div>
              <button onClick={() => setIsFlipped(true)} className="w-full py-3.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-200 rounded-xl text-sm font-medium transition-colors mt-6">
                显示答案
              </button>
            </div>

            {/* 卡片背面（答案与评分） */}
            <div className={`absolute inset-0 backface-hidden rotate-y-180 flex flex-col bg-zinc-900/80 border border-zinc-700 rounded-2xl p-8 shadow-xl ${!isFlipped ? 'invisible' : 'visible'}`}>
              <div className="mb-4 text-sm font-medium text-zinc-500">{currentCard.question}</div>
              <div className="w-full h-px bg-zinc-800 mb-6"></div>
              <div className="flex-1 overflow-y-auto">
                <p className="text-zinc-200 leading-relaxed">
                  {currentCard.answer}
                </p>
              </div>
              
              <div className="grid grid-cols-3 gap-4 mt-6">
                <button onClick={handleScore} className="py-3 flex flex-col items-center gap-1 bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 rounded-xl transition-colors border border-rose-500/10">
                  <span className="text-sm font-medium">忘了 (Forgot)</span>
                  <span className="text-[10px] opacity-70">明天再复习</span>
                </button>
                <button onClick={handleScore} className="py-3 flex flex-col items-center gap-1 bg-amber-500/10 hover:bg-amber-500/20 text-amber-400 rounded-xl transition-colors border border-amber-500/10">
                  <span className="text-sm font-medium">模糊 (Partial)</span>
                  <span className="text-[10px] opacity-70">7天后复习</span>
                </button>
                <button onClick={handleScore} className="py-3 flex flex-col items-center gap-1 bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-400 rounded-xl transition-colors border border-emerald-500/10">
                  <span className="text-sm font-medium">记得 (Confident)</span>
                  <span className="text-[10px] opacity-70">30天后复习</span>
                </button>
              </div>
            </div>

          </div>
        </div>
      </div>
    </div>
  );
}

function HealthView() {
  return (
    <div className="animate-in fade-in duration-500">
      <div className="flex items-end justify-between mb-6">
        <div>
          <h1 className="text-2xl font-semibold text-zinc-100 mb-1">系统诊断与巡检</h1>
          <p className="text-zinc-500 text-sm">自动定位过时、薄弱节点及架构空白，维护图谱健康。</p>
        </div>
        <div className="flex gap-3">
          <button className="flex items-center gap-2 px-4 py-2 bg-zinc-900 border border-zinc-800 hover:bg-zinc-800 text-zinc-300 rounded-lg text-sm transition-colors">
            <FileText className="w-4 h-4" /> 生成报告 (Report)
          </button>
        </div>
      </div>

      <div className="bg-zinc-900/40 border border-zinc-800/80 rounded-xl overflow-hidden">
        <div className="px-4 py-3 border-b border-zinc-800/80 bg-zinc-900 flex justify-between items-center">
          <span className="text-xs font-medium text-zinc-400">异常节点列表</span>
          <span className="text-[10px] font-mono text-zinc-600 bg-zinc-950 px-2 py-0.5 rounded">kg review --gaps</span>
        </div>
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-zinc-800/50 text-zinc-500">
              <th className="px-5 py-3 font-medium">缺陷类型</th>
              <th className="px-5 py-3 font-medium">目标节点</th>
              <th className="px-5 py-3 font-medium">置信度</th>
              <th className="px-5 py-3 font-medium">时间戳</th>
              <th className="px-5 py-3 font-medium text-right">操作</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800/50">
            {mockHealthGaps.map((item) => (
              <tr key={item.id} className="hover:bg-zinc-800/20 transition-colors group">
                <td className="px-5 py-3">
                  <span className={`inline-flex px-2 py-1 rounded text-[11px] font-medium border
                    ${item.type === '过时' ? 'bg-amber-500/10 text-amber-500 border-amber-500/20' : ''}
                    ${item.type === '薄弱' ? 'bg-rose-500/10 text-rose-400 border-rose-500/20' : ''}
                    ${item.type === '草稿' ? 'bg-zinc-500/10 text-zinc-400 border-zinc-500/20' : ''}
                    ${item.type === '空白' ? 'bg-indigo-500/10 text-indigo-400 border-indigo-500/20' : ''}
                  `}>
                    {item.type}
                  </span>
                </td>
                <td className="px-5 py-3 font-medium text-zinc-300">{item.title}</td>
                <td className="px-5 py-3">
                  {item.confidence ? (
                    <div className="flex items-center gap-2 w-24">
                      <div className="flex-1 h-1.5 bg-zinc-800 rounded-full overflow-hidden">
                        <div className={`h-full ${item.confidence > 0.6 ? 'bg-emerald-500/80' : 'bg-rose-500/80'}`} style={{ width: `${item.confidence * 100}%` }} />
                      </div>
                      <span className="text-xs text-zinc-500">{item.confidence.toFixed(2)}</span>
                    </div>
                  ) : <span className="text-zinc-600">-</span>}
                </td>
                <td className="px-5 py-3 text-zinc-500">{item.lastUpdated || '无记录'}</td>
                <td className="px-5 py-3 text-right">
                  <button className="text-xs text-indigo-400 hover:text-indigo-300 font-medium opacity-0 group-hover:opacity-100 transition-opacity">
                    {item.type === '空白' ? 'AI 补全' : '前往修复'}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function GraphView() {
  return (
    <div className="animate-in fade-in duration-500">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold text-zinc-100 mb-1">图谱网络与演进</h1>
        <p className="text-zinc-500 text-sm">洞察知识点间的潜在关联，追踪思维演进链路。</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* 潜在关联发现 */}
        <div className="bg-zinc-900/40 border border-zinc-800/80 rounded-xl p-5">
          <div className="flex justify-between items-center mb-4">
            <h3 className="text-sm font-medium text-zinc-300 flex items-center gap-2"><Network className="w-4 h-4 text-indigo-400"/> 潜在关联发现</h3>
            <span className="text-[10px] font-mono text-zinc-600 bg-zinc-950 px-1.5 py-0.5 rounded">kg link</span>
          </div>
          <div className="space-y-3">
            <div className="flex items-center gap-3 p-3 bg-zinc-950/50 rounded-lg border border-zinc-800/50">
              <span className="text-sm text-zinc-300">滑动窗口模板</span>
              <Activity className="w-4 h-4 text-zinc-600 shrink-0" />
              <span className="text-sm text-zinc-300">双指针核心思想</span>
              <span className="ml-auto text-xs text-emerald-400 font-mono">0.92</span>
            </div>
            <div className="flex items-center gap-3 p-3 bg-zinc-950/50 rounded-lg border border-zinc-800/50">
              <span className="text-sm text-zinc-300">Raft 选主逻辑</span>
              <Activity className="w-4 h-4 text-zinc-600 shrink-0" />
              <span className="text-sm text-zinc-300">分布式一致性</span>
              <span className="ml-auto text-xs text-emerald-400 font-mono">0.88</span>
            </div>
            <button className="w-full py-2 text-xs font-medium text-zinc-500 hover:text-zinc-300 transition-colors">查看全部关联探讨</button>
          </div>
        </div>

        {/* 演进历史记录 */}
        <div className="bg-zinc-900/40 border border-zinc-800/80 rounded-xl p-5">
          <div className="flex justify-between items-center mb-4">
            <h3 className="text-sm font-medium text-zinc-300 flex items-center gap-2"><GitCommit className="w-4 h-4 text-amber-400"/> 知识节点演进 (Diff)</h3>
            <div className="flex gap-2">
              <span className="text-[10px] font-mono text-zinc-600 bg-zinc-950 px-1.5 py-0.5 rounded">kg history</span>
              <span className="text-[10px] font-mono text-zinc-600 bg-zinc-950 px-1.5 py-0.5 rounded">kg diff</span>
            </div>
          </div>
          <div className="relative border-l border-zinc-800 ml-3 pl-5 space-y-6 mt-2">
            <div className="relative">
              <div className="absolute -left-[25px] top-1 w-2.5 h-2.5 bg-amber-500 rounded-full ring-4 ring-zinc-950"></div>
              <p className="text-xs text-zinc-500 mb-1">今日 14:30</p>
              <p className="text-sm text-zinc-300">重构了 <strong>滑动窗口模板</strong>，增加了 Go 语言实现版本，置信度重置。</p>
            </div>
            <div className="relative">
              <div className="absolute -left-[25px] top-1 w-2.5 h-2.5 bg-zinc-700 rounded-full ring-4 ring-zinc-950"></div>
              <p className="text-xs text-zinc-500 mb-1">3 天前</p>
              <p className="text-sm text-zinc-300">新建实战节点 <strong>LC-76 最小覆盖子串</strong>。</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// --- 弹窗与小组件 ---

function ExportModal({ onClose }) {
  const [format, setFormat] = useState('study-guide');
  
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm animate-in fade-in">
      <div className="bg-zinc-950 border border-zinc-800 rounded-2xl w-full max-w-lg shadow-2xl">
        <div className="flex items-center justify-between p-5 border-b border-zinc-800/80">
          <h2 className="text-lg font-medium text-zinc-100 flex items-center gap-2">
            <Download className="w-5 h-5 text-indigo-400" /> 多格式数据导出
          </h2>
          <button onClick={onClose} className="text-zinc-500 hover:text-zinc-300"><X className="w-5 h-5" /></button>
        </div>
        <div className="p-6 space-y-6">
          <div className="space-y-3">
            <FormatOption id="study-guide" title="个人学习指南 (Study Guide)" desc="按前置知识排序，包含练习题和你的薄弱点提示" selected={format === 'study-guide'} onClick={() => setFormat('study-guide')} />
            <FormatOption id="blog" title="技术博客 (Blog Post)" desc="叙事流排版：提出问题 -> 原理分析 -> 关键洞察" selected={format === 'blog'} onClick={() => setFormat('blog')} />
            <FormatOption id="onboarding" title="新人入职文档 (Onboarding Doc)" desc="输出团队上下文、核心规范与常见避坑 Checklist" selected={format === 'onboarding'} onClick={() => setFormat('onboarding')} />
          </div>
        </div>
        <div className="p-5 border-t border-zinc-800/80 flex justify-between items-center bg-zinc-900/30 rounded-b-2xl">
          <span className="text-xs font-mono text-zinc-500">kg export --format {format}</span>
          <div className="flex gap-3">
            <button onClick={onClose} className="px-4 py-2 text-sm text-zinc-400 hover:text-zinc-200">取消</button>
            <button className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-sm font-medium transition-colors">
              执行导出
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function IngestModal({ onClose }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm animate-in fade-in">
      <div className="bg-zinc-950 border border-zinc-800 rounded-2xl w-full max-w-lg shadow-2xl">
        <div className="flex items-center justify-between p-5 border-b border-zinc-800/80">
          <h2 className="text-lg font-medium text-zinc-100 flex items-center gap-2">
            <Upload className="w-5 h-5 text-emerald-400" /> 知识摄取与提取
          </h2>
          <button onClick={onClose} className="text-zinc-500 hover:text-zinc-300"><X className="w-5 h-5" /></button>
        </div>
        <div className="p-8 flex flex-col items-center justify-center border-2 border-dashed border-zinc-800 rounded-xl m-6 bg-zinc-900/30 hover:bg-zinc-900/50 hover:border-zinc-700 transition-colors cursor-pointer">
          <FileText className="w-10 h-10 text-zinc-600 mb-3" />
          <p className="text-sm font-medium text-zinc-300">拖拽文件到此处，或点击上传</p>
          <p className="text-xs text-zinc-500 mt-1">支持从对话记录、技术文章中自动提取结构化节点</p>
        </div>
        <div className="p-5 border-t border-zinc-800/80 flex justify-between items-center bg-zinc-900/30 rounded-b-2xl">
          <span className="text-xs font-mono text-zinc-500">kg ingest [filename]</span>
          <button className="px-4 py-2 bg-emerald-600/20 text-emerald-400 hover:bg-emerald-600/30 rounded-lg text-sm font-medium transition-colors">
            开始解析
          </button>
        </div>
      </div>
    </div>
  );
}

// --- 基础 UI 部件 ---

function Sidebar({ activeTab, setActiveTab }) {
  const navItems = [
    { id: 'dashboard', icon: LayoutDashboard, label: '概览仪表盘' },
    { id: 'ask', icon: Search, label: '探索与问答' },
    { id: 'quiz', icon: Play, label: '间隔测验' },
    { id: 'health', icon: AlertTriangle, label: '健康巡检' },
    { id: 'graph', icon: GitMerge, label: '图谱演进' },
  ];

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

      <div className="px-4 py-6">
        <div className="text-xs font-medium text-zinc-500 mb-3 px-2">主导航</div>
        <nav className="space-y-1">
          {navItems.map((item) => (
            <button
              key={item.id}
              onClick={() => setActiveTab(item.id)}
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
  );
}

function StatCard({ title, value, subValue, icon, alert }) {
  return (
    <div className="p-5 border border-zinc-800/80 bg-zinc-900/30 rounded-xl">
      <div className="flex justify-between items-start mb-2">
        <div className="text-sm font-medium text-zinc-400">{title}</div>
        <div className="text-zinc-500">{icon}</div>
      </div>
      <div className={`text-3xl font-semibold mb-1 ${alert ? 'text-amber-400' : 'text-zinc-100'}`}>{value}</div>
      <div className="text-xs text-zinc-500">{subValue}</div>
    </div>
  );
}

function ArchitectureCard({ title, desc, count, icon }) {
  return (
    <div className="p-5 border border-zinc-800/80 bg-zinc-900/30 rounded-xl">
      <div className="mb-3">{icon}</div>
      <h3 className="text-zinc-100 font-medium mb-1">{title}</h3>
      <p className="text-xs text-zinc-500 mb-4 h-8">{desc}</p>
      <div className="flex items-center justify-between text-xs pt-3 border-t border-zinc-800">
        <span className="text-zinc-400">收录节点</span>
        <span className="font-mono text-zinc-300">{count}</span>
      </div>
    </div>
  );
}

function ActionButton({ icon, label, cmd, onClick, primary }) {
  return (
    <button 
      onClick={onClick}
      className={`group flex items-center gap-2 px-3 py-1.5 text-sm rounded-lg transition-all ${
        primary 
          ? 'bg-zinc-100 text-zinc-900 hover:bg-white' 
          : 'border border-zinc-700 hover:border-zinc-500 text-zinc-300 hover:bg-zinc-800'
      }`}
    >
      <div className="w-4 h-4">{icon}</div>
      <span className="font-medium">{label}</span>
      <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded ml-1 opacity-0 group-hover:opacity-100 transition-opacity hidden sm:block ${
        primary ? 'bg-zinc-200 text-zinc-600' : 'bg-zinc-800 text-zinc-400'
      }`}>
        {cmd}
      </span>
    </button>
  );
}

function FormatOption({ title, desc, selected, onClick }) {
  return (
    <div 
      onClick={onClick}
      className={`cursor-pointer border p-4 rounded-xl flex gap-3 transition-all ${
        selected ? 'bg-indigo-500/10 border-indigo-500/50' : 'bg-zinc-900 border-zinc-800 hover:border-zinc-700'
      }`}
    >
      <div className={`mt-0.5 w-4 h-4 rounded-full border flex items-center justify-center shrink-0 ${
        selected ? 'border-indigo-400' : 'border-zinc-600'
      }`}>
        {selected && <div className="w-2 h-2 bg-indigo-400 rounded-full"></div>}
      </div>
      <div>
        <div className={`font-medium text-sm mb-1 ${selected ? 'text-indigo-300' : 'text-zinc-300'}`}>{title}</div>
        <div className="text-xs text-zinc-500">{desc}</div>
      </div>
    </div>
  );
}
