import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  applyLemmaHostTheme,
  getLemmaHostTheme,
  subscribeLemmaHostTheme,
  type LemmaHostThemeMessage,
} from '../browser-theme.js';

const message: LemmaHostThemeMessage = {
  type: 'lemma-app-theme',
  theme: 'dark',
  density: 'compact',
  tokens: {
    '--lemma-app-bg': '#1a1b18',
    '--lemma-app-accent': '#8588e8',
    '--not-public': 'ignored',
  },
};

describe('embedded app theme contract', () => {
  afterEach(() => {
    document.documentElement.removeAttribute('data-lemma-theme');
    document.documentElement.removeAttribute('data-lemma-density');
    document.documentElement.classList.remove('dark');
    document.documentElement.removeAttribute('style');
    vi.restoreAllMocks();
  });

  it('applies only public Lemma variables and emits the stable theme event', () => {
    const listener = vi.fn();
    const unsubscribe = subscribeLemmaHostTheme(listener);

    applyLemmaHostTheme(message);

    expect(document.documentElement.dataset.lemmaTheme).toBe('dark');
    expect(document.documentElement.dataset.lemmaDensity).toBe('compact');
    expect(document.documentElement.classList.contains('dark')).toBe(true);
    expect(document.documentElement.style.getPropertyValue('--lemma-app-bg')).toBe('#1a1b18');
    expect(document.documentElement.style.getPropertyValue('--not-public')).toBe('');
    expect(getLemmaHostTheme()).toEqual(message);
    expect(listener).toHaveBeenCalledWith(message);

    unsubscribe();
  });
});
