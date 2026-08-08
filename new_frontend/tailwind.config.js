/** @type {import('tailwindcss').Config} */
export default {
  // `dark` class strategy: the <html> element carries `.dark` for the
  // obsidian theme and no class for the light B2B industrial theme. Every
  // color token below resolves to a CSS variable (see src/index.css), so
  // component classes flip theme automatically — no `dark:` variants needed.
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        // --- Canvas & surfaces -----------------------------------------
        background: 'rgb(var(--canvas) / <alpha-value>)',
        canvas: 'rgb(var(--canvas) / <alpha-value>)',
        surface: 'rgb(var(--surface) / <alpha-value>)',
        'surface-dim': 'rgb(var(--surface-dim) / <alpha-value>)',
        'surface-bright': 'rgb(var(--surface-bright) / <alpha-value>)',
        'surface-container-lowest': 'rgb(var(--surface-container-lowest) / <alpha-value>)',
        'surface-container-low': 'rgb(var(--surface-container-low) / <alpha-value>)',
        'surface-container': 'rgb(var(--surface-container) / <alpha-value>)',
        'surface-container-high': 'rgb(var(--surface-container-high) / <alpha-value>)',
        'surface-container-highest': 'rgb(var(--surface-container-highest) / <alpha-value>)',
        'surface-variant': 'rgb(var(--surface-variant) / <alpha-value>)',
        // --- Ink (text hierarchy) ---------------------------------------
        'on-surface': 'rgb(var(--ink) / <alpha-value>)',
        'on-background': 'rgb(var(--ink) / <alpha-value>)',
        'on-surface-variant': 'rgb(var(--ink-2) / <alpha-value>)',
        outline: 'rgb(var(--ink-3) / <alpha-value>)',
        'outline-variant': 'rgb(var(--outline-variant) / <alpha-value>)',
        // Hairline token: white in dark, slate in light — pair with alpha
        // modifiers (e.g. `border-line/15`) for subtle dividers.
        line: 'rgb(var(--line) / <alpha-value>)',
        // --- Primary / buttons ------------------------------------------
        primary: 'rgb(var(--ink) / <alpha-value>)',
        'on-primary': 'rgb(var(--on-primary) / <alpha-value>)',
        'primary-container': 'rgb(var(--primary-container) / <alpha-value>)',
        'on-primary-container': 'rgb(var(--on-primary-container) / <alpha-value>)',
        'primary-fixed': 'rgb(var(--primary-fixed) / <alpha-value>)',
        'primary-fixed-dim': 'rgb(var(--primary-fixed-dim) / <alpha-value>)',
        'on-primary-fixed': 'rgb(var(--on-primary-fixed) / <alpha-value>)',
        secondary: 'rgb(var(--secondary) / <alpha-value>)',
        'on-secondary': 'rgb(var(--on-secondary) / <alpha-value>)',
        'secondary-container': 'rgb(var(--secondary-container) / <alpha-value>)',
        'on-secondary-container': 'rgb(var(--on-secondary-container) / <alpha-value>)',
        tertiary: 'rgb(var(--ink) / <alpha-value>)',
        'on-tertiary': 'rgb(var(--on-primary-fixed) / <alpha-value>)',
        'tertiary-container': 'rgb(var(--tertiary-container) / <alpha-value>)',
        'on-tertiary-container': 'rgb(var(--on-tertiary-container) / <alpha-value>)',
        'tertiary-fixed': 'rgb(var(--accent) / <alpha-value>)',
        'tertiary-fixed-dim': 'rgb(var(--accent-dim) / <alpha-value>)',
        'on-tertiary-fixed': 'rgb(var(--on-tertiary-fixed) / <alpha-value>)',
        // --- Accents & status -------------------------------------------
        'muted-blue': 'rgb(var(--steel) / <alpha-value>)',
        error: 'rgb(var(--error) / <alpha-value>)',
        'on-error': 'rgb(var(--on-error) / <alpha-value>)',
        'error-container': 'rgb(var(--error-container) / <alpha-value>)',
        'on-error-container': 'rgb(var(--on-error-container) / <alpha-value>)',
      },
      fontFamily: {
        display: ['"EB Garamond"', 'Georgia', 'serif'],
        sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
      },
      fontSize: {
        'display-lg': ['84px', { lineHeight: '92px', letterSpacing: '-0.02em', fontWeight: '400' }],
        'display-sm': ['56px', { lineHeight: '64px', letterSpacing: '-0.01em', fontWeight: '400' }],
        'headline-lg': ['40px', { lineHeight: '48px', fontWeight: '400' }],
        'headline-md': ['28px', { lineHeight: '38px', fontWeight: '400' }],
        'headline-sm': ['20px', { lineHeight: '28px', fontWeight: '500' }],
        'body-lg': ['18px', { lineHeight: '32px', fontWeight: '400' }],
        'body-md': ['15px', { lineHeight: '24px', fontWeight: '400' }],
        'label-caps': ['12px', { lineHeight: '16px', letterSpacing: '0.08em', fontWeight: '600' }],
        'label-sm': ['11px', { lineHeight: '14px', letterSpacing: '0.08em', fontWeight: '600' }],
        'metric-xl': ['112px', { lineHeight: '112px', fontWeight: '400' }],
      },
      borderRadius: {
        DEFAULT: '0.125rem',
        lg: '0.25rem',
        xl: '0.5rem',
      },
      spacing: {
        'section-gap': '120px',
        'element-gap': '48px',
        'container-padding': '64px',
        'margin-edge': '64px',
        sidebar: '280px',
      },
      maxWidth: {
        shell: '1440px',
      },
      boxShadow: {
        tactile: 'var(--shadow-tactile)',
        'tactile-lg': 'var(--shadow-tactile-lg)',
      },
      keyframes: {
        'fade-in': {
          '0%': { opacity: '0', transform: 'translateY(10px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        'pulse-soft': {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.45' },
        },
      },
      animation: {
        'fade-in': 'fade-in 0.45s ease-in-out both',
        'pulse-soft': 'pulse-soft 2s ease-in-out infinite',
      },
    },
  },
  plugins: [],
};
