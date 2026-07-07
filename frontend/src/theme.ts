// Central design tokens for the TastyHub test frontend.
//
// The old UI was built on a warm red (#c0392b) + cream (#fffdf8) palette with
// per-component hardcoded hex values. This file replaces that with a single,
// cool blue-based system so the whole app reads as one modern surface. Import
// `t` (tokens) into a component's `styles` object instead of hardcoding colors.

export const t = {
  color: {
    // Brand — indigo/blue ramp
    primary: '#2563eb',        // primary actions, active nav, accents
    primaryHover: '#1d4ed8',
    primarySoft: '#eff6ff',    // tinted backgrounds (selected cards, hints)
    primarySofter: '#dbeafe',
    primaryBorder: '#bfdbfe',
    primaryText: '#1e40af',

    // Accent — used sparingly for a second brand note (calendar "today", chips)
    accent: '#0ea5e9',

    // Semantic
    success: '#16a34a',
    successSoft: '#f0fdf4',
    successBorder: '#bbf7d0',
    warning: '#d97706',
    danger: '#dc2626',
    dangerSoft: '#fef2f2',

    // Neutrals — cool grey (slate) ramp
    text: '#0f172a',           // near-black headings/body
    textMuted: '#64748b',      // secondary text, meta
    textFaint: '#94a3b8',      // placeholders, hints
    border: '#e2e8f0',         // default borders / dividers
    borderStrong: '#cbd5e1',
    divider: '#f1f5f9',

    // Surfaces
    bg: '#f8fafc',             // app canvas
    surface: '#ffffff',        // cards, panels
    surfaceMuted: '#f1f5f9',   // inset areas, status bars, bot bubbles
  },

  radius: { sm: 6, md: 8, lg: 12, xl: 16, pill: 999 },

  shadow: {
    sm: '0 1px 2px rgba(15, 23, 42, 0.06)',
    md: '0 4px 12px rgba(15, 23, 42, 0.08)',
    lg: '0 12px 32px rgba(15, 23, 42, 0.16)',
    focus: '0 0 0 3px rgba(37, 99, 235, 0.18)',
  },

  font: {
    sans: `'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif`,
  },
} as const
