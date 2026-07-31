export const LEMMA_APP_THEME_MESSAGE_TYPE = 'lemma-app-theme';
export const LEMMA_THEME_EVENT = 'lemma:theme';

export type LemmaHostTheme = 'light' | 'dark';

export interface LemmaHostThemeMessage {
  type: typeof LEMMA_APP_THEME_MESSAGE_TYPE;
  theme: LemmaHostTheme;
  density?: 'compact';
  tokens: Record<string, string>;
}

let currentHostTheme: LemmaHostThemeMessage | null = null;

function isHostThemeMessage(value: unknown): value is LemmaHostThemeMessage {
  if (!value || typeof value !== 'object') return false;
  const candidate = value as Partial<LemmaHostThemeMessage>;
  return candidate.type === LEMMA_APP_THEME_MESSAGE_TYPE
    && (candidate.theme === 'light' || candidate.theme === 'dark')
    && Boolean(candidate.tokens && typeof candidate.tokens === 'object');
}

export function applyLemmaHostTheme(message: LemmaHostThemeMessage): void {
  if (typeof document === 'undefined') return;
  const root = document.documentElement;
  Object.entries(message.tokens).forEach(([name, value]) => {
    if (!name.startsWith('--lemma-app-') || typeof value !== 'string' || value.length > 512) return;
    root.style.setProperty(name, value);
  });
  root.dataset.lemmaTheme = message.theme;
  root.dataset.lemmaDensity = message.density || 'compact';
  root.classList.toggle('dark', message.theme === 'dark');
  root.style.colorScheme = message.theme;
  currentHostTheme = message;

  if (typeof window !== 'undefined' && typeof CustomEvent !== 'undefined') {
    window.dispatchEvent(new CustomEvent(LEMMA_THEME_EVENT, { detail: message }));
  }
}

export function getLemmaHostTheme(): LemmaHostThemeMessage | null {
  return currentHostTheme;
}

export function subscribeLemmaHostTheme(listener: (message: LemmaHostThemeMessage) => void): () => void {
  if (typeof window === 'undefined') return () => undefined;
  const handleTheme = (event: Event) => {
    listener((event as CustomEvent<LemmaHostThemeMessage>).detail);
  };
  window.addEventListener(LEMMA_THEME_EVENT, handleTheme);
  if (currentHostTheme) listener(currentHostTheme);
  return () => window.removeEventListener(LEMMA_THEME_EVENT, handleTheme);
}

if (typeof window !== 'undefined' && window.parent !== window) {
  window.addEventListener('message', (event: MessageEvent) => {
    if (event.source !== window.parent || !isHostThemeMessage(event.data)) return;
    applyLemmaHostTheme(event.data);
  });
}
