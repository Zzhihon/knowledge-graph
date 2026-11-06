import { useEffect, useState } from 'react'

export type ThemeChoice = 'system' | 'light' | 'dark'
const KEY = 'kg-theme'

function read(): ThemeChoice {
  try {
    const v = localStorage.getItem(KEY)
    return v === 'light' || v === 'dark' ? v : 'system'
  } catch {
    return 'system'
  }
}

/** The reader's theme choice; the resolved theme lives on <html data-theme>, which aihot.css keys on. */
export function useTheme(): [ThemeChoice, (t: ThemeChoice) => void] {
  const [choice, setChoice] = useState<ThemeChoice>(read)
  useEffect(() => {
    const media = matchMedia('(prefers-color-scheme: dark)')
    const apply = () => {
      document.documentElement.dataset.theme = choice === 'system' ? (media.matches ? 'dark' : 'light') : choice
    }
    apply()
    media.addEventListener('change', apply)
    try { localStorage.setItem(KEY, choice) } catch { /* private mode */ }
    return () => media.removeEventListener('change', apply)
  }, [choice])
  return [choice, setChoice]
}
