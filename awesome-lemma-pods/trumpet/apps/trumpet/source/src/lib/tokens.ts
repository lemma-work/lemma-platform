// Trumpet design tokens — bg/fg/muted use CSS variables so they flip with the theme
export const tokens = {
  bg:         'var(--trumpet-bg)',
  bg2:        'var(--trumpet-bg2)',
  fg:         'var(--trumpet-fg)',
  muted:      'var(--trumpet-muted)',
  cream:      '#efe9db',
  creamEdge:  '#e7e0cf',
  lilac:      '#e6e0f1',
  lilacEdge:  '#ddd5ec',
  ink:        '#1a1815',
  inkSoft:    '#5c574f',
  accent:     '#f5402c',
  green:      '#57b86a',
  amber:      '#f3b223',
  red:        '#f5402c',
  font:       "'Hanken Grotesk', system-ui, sans-serif",
} as const;

export const CANVAS_W = 1512;
export const CANVAS_H = 1008;
