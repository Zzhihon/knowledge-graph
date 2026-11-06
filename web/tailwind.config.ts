import type { Config } from 'tailwindcss'

// UI colours come from CSS variables in src/styles/aihot.css, so light and dark switch there.
// Opacity modifiers (bg-k-accent/10) mix the variable with transparent.
const k = (name: string) => ({ opacityValue }: { opacityValue?: string }) =>
  opacityValue === undefined || opacityValue === '1'
    ? `var(--k-${name})`
    : `color-mix(in srgb, var(--k-${name}) calc(${opacityValue} * 100%), transparent)`

const NAMES = [
  'bg', 'sunk', 'muted', 'surface', 'sidebar', 'ink', 'line', 'line-strong', 'line-soft',
  'accent', 'accent-soft', 'accent-contrast', 'note', 'hot', 'hot-soft', 'amber', 'amber-ink', 'amber-soft',
  'ok', 'ok-soft', 'violet', 'violet-soft',
]

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        k: {
          ...Object.fromEntries(NAMES.map((n) => [n, k(n)])),
          ink2: k('ink-2'),
          ink3: k('ink-3'),
          ink4: k('ink-4'),
        },
      },
      borderRadius: { mark: '5px', control: '8px', tile: '10px', card: '12px', panel: '14px', sheet: '16px' },
    },
  },
  plugins: [],
} satisfies Config
