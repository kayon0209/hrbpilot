import type { Config } from 'tailwindcss';

export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        // Hallmark anchor scale — mapped to Tailwind 50-900 for component compatibility
        // All neutrals tinted to H=50 (warm stone)
        neutral: {
          50:  'var(--color-paper)',
          100: 'var(--color-paper-2)',
          200: 'var(--color-paper-3)',
          300: 'var(--color-rule)',
          400: 'var(--color-rule-2)',
          500: 'var(--color-muted)',
          600: 'var(--color-neutral)',
          700: 'var(--color-ink-2)',
          800: 'var(--color-ink-2)',
          900: 'var(--color-ink)',
        },
        // Primary = the single accent
        primary: {
          50:  'var(--color-accent-soft)',
          100: 'var(--color-accent-soft)',
          200: 'var(--color-accent-soft)',
          400: 'var(--color-accent)',
          500: 'var(--color-accent)',
          600: 'var(--color-accent-hover)',
          700: 'var(--color-accent-hover)',
          800: 'var(--color-accent-ink)',
          900: 'var(--color-accent-ink)',
        },
        // Accent kept as alias
        accent: {
          50:  'var(--color-accent-soft)',
          400: 'var(--color-accent)',
          500: 'var(--color-accent)',
          600: 'var(--color-accent-hover)',
        },
        // Semantic colors
        success: {
          50:  'var(--color-success-soft)',
          400: 'var(--color-success)',
          500: 'var(--color-success)',
          600: 'var(--color-success)',
        },
        warning: {
          50:  'var(--color-warning-soft)',
          200: 'var(--color-warning-soft)',
          400: 'var(--color-warning)',
          500: 'var(--color-warning)',
          600: 'var(--color-warning)',
          700: 'var(--color-warning)',
        },
        danger: {
          50:  'var(--color-danger-soft)',
          200: 'var(--color-danger-soft)',
          400: 'var(--color-danger)',
          500: 'var(--color-danger)',
          600: 'var(--color-danger)',
          700: 'var(--color-danger)',
        },
        // Scene accent hints
        emerald: {
          50:  'var(--scene-voice-insight-soft)',
          400: 'var(--scene-voice-insight)',
          500: 'var(--scene-voice-insight)',
          600: 'var(--scene-voice-insight)',
        },
        indigo: {
          50:  'var(--scene-policy-qa-soft)',
          500: 'var(--scene-policy-qa)',
          600: 'var(--scene-policy-qa)',
        },
        sky: {
          50:  'var(--scene-interview-soft)',
          600: 'var(--scene-interview)',
        },
        amber: {
          50:  'var(--scene-weekly-report-soft)',
          600: 'var(--scene-weekly-report)',
        },
        rose: {
          50:  'var(--scene-culture-soft)',
          500: 'var(--scene-culture)',
          600: 'var(--scene-culture)',
        },
      },
      fontFamily: {
        display: ['var(--font-display)', 'ui-serif', 'Georgia', 'serif'],
        body: ['var(--font-body)', 'ui-sans-serif', 'sans-serif'],
        mono: ['var(--font-mono)', 'ui-monospace', 'monospace'],
      },
      fontSize: {
        'xs':      ['var(--text-xs)',  { lineHeight: 'var(--lh-normal)' }],
        'sm':      ['var(--text-sm)',  { lineHeight: 'var(--lh-normal)' }],
        'base':    ['var(--text-base)',{ lineHeight: 'var(--lh-normal)' }],
        'md':      ['var(--text-md)',  { lineHeight: 'var(--lh-snug)' }],
        'lg':      ['var(--text-lg)',  { lineHeight: 'var(--lh-snug)' }],
        'xl':      ['var(--text-xl)',  { lineHeight: 'var(--lh-snug)' }],
        '2xl':     ['var(--text-2xl)', { lineHeight: 'var(--lh-tight)' }],
        'display': ['var(--text-display)', { lineHeight: 'var(--lh-tight)' }],
        'caption': ['var(--text-xs)',  { lineHeight: 'var(--lh-normal)' }],
        'card-title': ['var(--text-md)', { lineHeight: 'var(--lh-snug)', fontWeight: '500' }],
        'section-title': ['var(--text-lg)', { lineHeight: 'var(--lh-snug)' }],
        'page-title': ['var(--text-xl)', { lineHeight: 'var(--lh-snug)' }],
        'metric': ['var(--text-lg)', { lineHeight: '1.1', fontFamily: 'var(--font-mono)' }],
      },
      spacing: {
        '0.5': '2px',
        '3xs': 'var(--space-3xs)',
        '2xs': 'var(--space-2xs)',
        'xs': 'var(--space-xs)',
        'sm': 'var(--space-sm)',
        'md': 'var(--space-md)',
        'lg': 'var(--space-lg)',
        'xl': 'var(--space-xl)',
        '2xl': 'var(--space-2xl)',
        '3xl': 'var(--space-3xl)',
      },
      borderRadius: {
        sm: 'var(--radius-sm)',
        md: 'var(--radius-md)',
        lg: 'var(--radius-lg)',
        xl: 'var(--radius-lg)',
        '2xl': 'var(--radius-lg)',
        full: 'var(--radius-full)',
      },
      boxShadow: {
        xs: 'var(--shadow-xs)',
        sm: 'var(--shadow-sm)',
        md: 'var(--shadow-md)',
        lg: 'var(--shadow-md)',
      },
      transitionDuration: {
        fast: '150ms',
        normal: '220ms',
        slow: '350ms',
      },
      maxWidth: {
        measure: 'var(--measure)',
        page: 'var(--page-max)',
      },
    },
  },
  plugins: [],
} satisfies Config;
