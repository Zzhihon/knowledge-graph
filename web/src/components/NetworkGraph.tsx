import { useRef, useEffect, useCallback } from 'react'
import * as d3 from 'd3'
import type { NetworkNode, NetworkEdge } from '../types'

// Domain → color mapping (consistent hues for the dark theme)
const DOMAIN_COLORS: Record<string, string> = {
  golang: '#00ADD8',
  algorithm: '#F59E0B',
  'system-design': '#8B5CF6',
  architecture: '#8B5CF6',
  infrastructure: '#06B6D4',
  kubernetes: '#326CE5',
  database: '#EF4444',
  networking: '#10B981',
  security: '#F97316',
  frontend: '#EC4899',
  backend: '#6366F1',
  devops: '#14B8A6',
  cloud: '#3B82F6',
  'machine-learning': '#A855F7',
  career: '#F472B6',
}
const FALLBACK_COLORS = [
  '#64748B', '#78716C', '#71717A', '#737373',
  '#6B7280', '#9CA3AF', '#A1A1AA', '#A3A3A3',
]

const EDGE_COLORS: Record<string, string> = {
  references: '#3B82F6',
  prerequisites: '#F59E0B',
  supersedes: '#EF4444',
}

function getDomainColor(domains: string[]): string {
  for (const d of domains) {
    const key = d.toLowerCase()
    if (DOMAIN_COLORS[key]) return DOMAIN_COLORS[key]
  }
  if (domains.length > 0) {
    const hash = domains[0].split('').reduce((a, c) => a + c.charCodeAt(0), 0)
    return FALLBACK_COLORS[hash % FALLBACK_COLORS.length]
  }
  return '#64748B'
}

interface SimNode extends d3.SimulationNodeDatum {
  id: string
  title: string
  domain: string[]
  type: string
  depth: string
  status: string
  confidence: number | null
  tags: string[]
  linkCount: number
}

interface SimLink extends d3.SimulationLinkDatum<SimNode> {
  type: string
}

interface Props {
  nodes: NetworkNode[]
  edges: NetworkEdge[]
  filteredDomains: Set<string>
  filteredEdgeTypes: Set<string>
  onNodeClick?: (nodeId: string) => void
}

export default function NetworkGraph({
  nodes,
  edges,
  filteredDomains,
  filteredEdgeTypes,
  onNodeClick,
}: Props) {
  const svgRef = useRef<SVGSVGElement>(null)
  const simRef = useRef<d3.Simulation<SimNode, SimLink> | null>(null)
  const containerRef = useRef<HTMLDivElement>(null)

  // Tooltip state rendered as DOM overlay
  const tooltipRef = useRef<HTMLDivElement>(null)

  const showTooltip = useCallback((node: SimNode, x: number, y: number) => {
    const tip = tooltipRef.current
    if (!tip) return
    tip.innerHTML = `
      <div class="font-medium text-zinc-100 text-sm mb-1 truncate max-w-[240px]">${node.title}</div>
      <div class="text-xs text-zinc-400 space-y-0.5">
        <div>领域: ${node.domain.join(', ') || '—'}</div>
        <div>连接: ${node.linkCount}</div>
        ${node.confidence != null ? `<div>置信度: ${(node.confidence * 100).toFixed(0)}%</div>` : ''}
      </div>
    `
    tip.style.left = `${x + 14}px`
    tip.style.top = `${y - 14}px`
    tip.style.opacity = '1'
    tip.style.pointerEvents = 'none'
  }, [])

  const hideTooltip = useCallback(() => {
    const tip = tooltipRef.current
    if (!tip) return
    tip.style.opacity = '0'
  }, [])

  // Build simulation once when data changes
  useEffect(() => {
    const svg = svgRef.current
    if (!svg || nodes.length === 0) return

    const width = svg.clientWidth
    const height = svg.clientHeight

    // Count links per node
    const linkCounts = new Map<string, number>()
    edges.forEach((e) => {
      linkCounts.set(e.source, (linkCounts.get(e.source) || 0) + 1)
      linkCounts.set(e.target, (linkCounts.get(e.target) || 0) + 1)
    })

    const simNodes: SimNode[] = nodes.map((n) => ({
      ...n,
      linkCount: linkCounts.get(n.id) || 0,
    }))

    const nodeById = new Map(simNodes.map((n) => [n.id, n]))
    const simLinks: SimLink[] = edges
      .filter((e) => nodeById.has(e.source) && nodeById.has(e.target))
      .map((e) => ({ source: e.source, target: e.target, type: e.type }))

    // Clear previous
    const root = d3.select(svg)
    root.selectAll('*').remove()

    const g = root.append('g')

    // Zoom
    const zoom = d3.zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.1, 6])
      .on('zoom', (event) => {
        g.attr('transform', event.transform)
      })
    root.call(zoom)

    // Center initially
    const initialTransform = d3.zoomIdentity
      .translate(width / 2, height / 2)
      .scale(0.6)
    root.call(zoom.transform, initialTransform)

    // Links
    const linkG = g.append('g').attr('class', 'links')
    const linkSel = linkG
      .selectAll<SVGLineElement, SimLink>('line')
      .data(simLinks)
      .join('line')
      .attr('stroke', (d) => EDGE_COLORS[d.type] || '#334155')
      .attr('stroke-width', 0.5)
      .attr('stroke-opacity', 0.3)

    // Nodes
    const nodeG = g.append('g').attr('class', 'nodes')
    const nodeSel = nodeG
      .selectAll<SVGCircleElement, SimNode>('circle')
      .data(simNodes, (d) => d.id)
      .join('circle')
      .attr('r', (d) => Math.max(3, Math.min(12, 3 + d.linkCount * 0.5)))
      .attr('fill', (d) => getDomainColor(d.domain))
      .attr('stroke', '#09090b')
      .attr('stroke-width', 0.5)
      .attr('cursor', 'pointer')

    // Drag
    const drag = d3.drag<SVGCircleElement, SimNode>()
      .on('start', (event, d) => {
        if (!event.active) simulation.alphaTarget(0.3).restart()
        d.fx = d.x
        d.fy = d.y
      })
      .on('drag', (event, d) => {
        d.fx = event.x
        d.fy = event.y
      })
      .on('end', (event, d) => {
        if (!event.active) simulation.alphaTarget(0)
        d.fx = null
        d.fy = null
      })
    nodeSel.call(drag)

    // Hover highlight
    const neighborMap = new Map<string, Set<string>>()
    simLinks.forEach((l) => {
      const src = typeof l.source === 'string' ? l.source : (l.source as SimNode).id
      const tgt = typeof l.target === 'string' ? l.target : (l.target as SimNode).id
      if (!neighborMap.has(src)) neighborMap.set(src, new Set())
      if (!neighborMap.has(tgt)) neighborMap.set(tgt, new Set())
      neighborMap.get(src)!.add(tgt)
      neighborMap.get(tgt)!.add(src)
    })

    nodeSel
      .on('mouseover', function (event, d) {
        const neighbors = neighborMap.get(d.id) || new Set()
        nodeSel
          .attr('opacity', (n) => (n.id === d.id || neighbors.has(n.id)) ? 1 : 0.08)
        linkSel
          .attr('stroke-opacity', (l) => {
            const src = typeof l.source === 'string' ? l.source : (l.source as SimNode).id
            const tgt = typeof l.target === 'string' ? l.target : (l.target as SimNode).id
            return src === d.id || tgt === d.id ? 0.8 : 0.03
          })
          .attr('stroke-width', (l) => {
            const src = typeof l.source === 'string' ? l.source : (l.source as SimNode).id
            const tgt = typeof l.target === 'string' ? l.target : (l.target as SimNode).id
            return src === d.id || tgt === d.id ? 1.5 : 0.5
          })

        // Tooltip
        const [mx, my] = d3.pointer(event, containerRef.current)
        showTooltip(d, mx, my)
      })
      .on('mousemove', function (event, d) {
        const [mx, my] = d3.pointer(event, containerRef.current)
        showTooltip(d, mx, my)
      })
      .on('mouseout', function () {
        nodeSel.attr('opacity', 1)
        linkSel.attr('stroke-opacity', 0.3).attr('stroke-width', 0.5)
        hideTooltip()
      })
      .on('click', (_event, d) => {
        onNodeClick?.(d.id)
      })

    // Simulation
    const simulation = d3.forceSimulation<SimNode>(simNodes)
      .force('link', d3.forceLink<SimNode, SimLink>(simLinks)
        .id((d) => d.id)
        .distance(60)
        .strength(0.3))
      .force('charge', d3.forceManyBody().strength(-80).distanceMax(300))
      .force('center', d3.forceCenter(0, 0))
      .force('collision', d3.forceCollide<SimNode>().radius((d) => Math.max(3, 3 + d.linkCount * 0.5) + 2))
      .on('tick', () => {
        linkSel
          .attr('x1', (d) => (d.source as SimNode).x!)
          .attr('y1', (d) => (d.source as SimNode).y!)
          .attr('x2', (d) => (d.target as SimNode).x!)
          .attr('y2', (d) => (d.target as SimNode).y!)
        nodeSel
          .attr('cx', (d) => d.x!)
          .attr('cy', (d) => d.y!)
      })

    simRef.current = simulation

    return () => {
      simulation.stop()
      simRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes, edges, onNodeClick, showTooltip, hideTooltip])

  // Apply filters via display toggling (no simulation rebuild)
  useEffect(() => {
    const svg = svgRef.current
    if (!svg) return

    const root = d3.select(svg)

    // Build set of visible node IDs
    const visibleNodes = new Set<string>()
    root.selectAll<SVGCircleElement, SimNode>('.nodes circle').each(function (d) {
      const domainMatch = d.domain.length === 0 || d.domain.some((dm) => filteredDomains.has(dm))
      const visible = domainMatch
      d3.select(this).style('display', visible ? '' : 'none')
      if (visible) visibleNodes.add(d.id)
    })

    root.selectAll<SVGLineElement, SimLink>('.links line').each(function (d) {
      const src = typeof d.source === 'string' ? d.source : (d.source as SimNode).id
      const tgt = typeof d.target === 'string' ? d.target : (d.target as SimNode).id
      const edgeVisible = filteredEdgeTypes.has(d.type) && visibleNodes.has(src) && visibleNodes.has(tgt)
      d3.select(this).style('display', edgeVisible ? '' : 'none')
    })
  }, [filteredDomains, filteredEdgeTypes])

  // Resize handling
  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    const observer = new ResizeObserver(() => {
      const svg = svgRef.current
      if (!svg) return
      svg.setAttribute('width', `${container.clientWidth}`)
      svg.setAttribute('height', `${container.clientHeight}`)
    })
    observer.observe(container)
    return () => observer.disconnect()
  }, [])

  return (
    <div ref={containerRef} className="relative w-full h-full overflow-hidden bg-[#09090b]">
      <svg
        ref={svgRef}
        className="w-full h-full"
        style={{ cursor: 'grab' }}
      />
      <div
        ref={tooltipRef}
        className="absolute z-50 px-3 py-2 bg-zinc-900 border border-zinc-700/50 rounded-lg shadow-xl opacity-0 transition-opacity duration-150 pointer-events-none"
        style={{ maxWidth: 280 }}
      />
    </div>
  )
}

export { DOMAIN_COLORS, EDGE_COLORS, getDomainColor }
