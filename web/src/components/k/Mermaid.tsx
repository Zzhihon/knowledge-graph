import { useEffect, useRef, useState } from 'react'

let seq = 0

/** A mermaid code block drawn as SVG; falls back to the source when the syntax is wrong. */
export default function Mermaid({ code }: { code: string }) {
  const ref = useRef<HTMLDivElement>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      const { default: mermaid } = await import('mermaid')
      const dark = document.documentElement.dataset.theme === 'dark'
      const css = getComputedStyle(document.documentElement)
      const v = (n: string) => css.getPropertyValue(`--k-${n}`).trim()
      mermaid.initialize({
        startOnLoad: false,
        securityLevel: 'strict',
        theme: 'base',
        fontFamily: css.getPropertyValue('--k-sans'),
        themeVariables: {
          darkMode: dark,
          fontSize: '15px',
          background: v('surface'),
          primaryColor: v('sunk'),
          primaryBorderColor: v('line-strong'),
          primaryTextColor: v('ink'),
          secondaryColor: v('accent-soft'),
          tertiaryColor: v('surface'),
          lineColor: v('ink-4'),
          textColor: v('ink-2'),
          clusterBkg: v('sunk'),
          edgeLabelBackground: v('surface'),
        },
        flowchart: { curve: 'basis', padding: 14, useMaxWidth: false },
        sequence: { useMaxWidth: false },
      })
      try {
        const { svg } = await mermaid.render(`kg-mmd-${++seq}`, code)
        if (!cancelled && ref.current) ref.current.innerHTML = svg
      } catch {
        if (!cancelled) setFailed(true)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [code])

  if (failed) {
    return (
      <figure className="k-figure" style={{ textAlign: 'left' }}>
        <pre className="k-mono text-[12.5px] whitespace-pre-wrap">{code}</pre>
        <figcaption>图解语法有误，显示原文</figcaption>
      </figure>
    )
  }
  return <figure className="k-figure"><div ref={ref} aria-label="图解" /></figure>
}
