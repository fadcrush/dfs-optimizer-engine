/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: 'class',
  content: ['./src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        surface: {
          base:    '#0f172a',
          raised:  '#1e293b',
          overlay: '#1e2d40',
          border:  '#334155',
        },
        primary: {
          DEFAULT: '#2563eb',
          hover:   '#1d4ed8',
          muted:   '#1e3a5f',
        },
        success: { DEFAULT: '#22c55e', muted: '#14532d' },
        warning: { DEFAULT: '#f59e0b', muted: '#451a03' },
        danger:  { DEFAULT: '#ef4444', muted: '#450a0a' },
        text: {
          primary:   '#f8fafc',
          secondary: '#94a3b8',
          muted:     '#64748b',
        },
      },
      fontFamily: {
        sans: ['var(--font-inter)', 'system-ui', 'sans-serif'],
        mono: ['var(--font-geist-mono)', 'ui-monospace', 'monospace'],
      },
      borderRadius: {
        sm: '4px',
        DEFAULT: '6px',
        md: '8px',
        lg: '12px',
        xl: '16px',
      },
      boxShadow: {
        card:  '0 1px 3px rgba(0,0,0,0.4)',
        modal: '0 8px 32px rgba(0,0,0,0.6)',
      },
      keyframes: {
        shimmer: {
          '0%':   { backgroundPosition: '-200% 0' },
          '100%': { backgroundPosition: '200% 0' },
        },
        fadeIn: {
          from: { opacity: '0', transform: 'scale(0.97)' },
          to:   { opacity: '1', transform: 'scale(1)' },
        },
        slideUp: {
          from: { opacity: '0', transform: 'translateY(8px)' },
          to:   { opacity: '1', transform: 'translateY(0)' },
        },
        toastIn: {
          from: { opacity: '0', transform: 'translateX(100%)' },
          to:   { opacity: '1', transform: 'translateX(0)' },
        },
      },
      animation: {
        shimmer: 'shimmer 1.6s infinite linear',
        fadeIn:  'fadeIn 150ms ease-out',
        slideUp: 'slideUp 150ms ease-out',
        toastIn: 'toastIn 200ms ease-out',
      },
    },
  },
  plugins: [],
}

