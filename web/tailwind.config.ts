import type { Config } from 'tailwindcss'

// Reading UI colours come from CSS variables in src/styles/aihot.css, so light and dark switch there.
const k = (name: string) => `var(--k-${name})`

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        k: {
          bg: k('bg'),
          sunk: k('sunk'),
          muted: k('muted'),
          surface: k('surface'),
          sidebar: k('sidebar'),
          ink: k('ink'),
          ink2: k('ink-2'),
          ink3: k('ink-3'),
          ink4: k('ink-4'),
          line: k('line'),
          'line-strong': k('line-strong'),
          'line-soft': k('line-soft'),
          accent: k('accent'),
          'accent-soft': k('accent-soft'),
          note: k('note'),
          hot: k('hot'),
          'hot-soft': k('hot-soft'),
          amber: k('amber'),
          'amber-ink': k('amber-ink'),
          'amber-soft': k('amber-soft'),
          ok: k('ok'),
          'ok-soft': k('ok-soft'),
        },
      },
      borderRadius: { mark: '5px', control: '8px', tile: '10px', card: '12px', panel: '14px', sheet: '16px' },
    },
  },
  plugins: [],
} satisfies Config
