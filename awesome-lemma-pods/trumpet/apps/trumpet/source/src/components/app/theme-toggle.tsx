import * as React from "react";

type ThemeMode = "light" | "dark";

function initialTheme(): ThemeMode {
  if (typeof window === "undefined") return "dark";
  const stored = window.localStorage.getItem("lemma-theme");
  if (stored === "light" || stored === "dark") return stored;
  return "dark"; // Trumpet defaults to dark
}

/** Apply theme immediately — called once on module load to avoid FOUC */
function applyTheme(theme: ThemeMode) {
  document.documentElement.classList.toggle("dark", theme === "dark");
  document.documentElement.style.colorScheme = theme;
}

export function ThemeToggle() {
  const [theme, setTheme] = React.useState<ThemeMode>(() => {
    const t = initialTheme();
    applyTheme(t);
    return t;
  });

  const toggle = () => {
    const next: ThemeMode = theme === "dark" ? "light" : "dark";
    applyTheme(next);
    window.localStorage.setItem("lemma-theme", next);
    setTheme(next);
  };

  const isDark = theme === "dark";

  // Use solid, theme-aware colours so the button is always visible.
  // Semi-transparent surface vars are invisible against the dark stage.
  const btnBg     = isDark ? '#2c2a27' : 'rgba(0,0,0,0.07)';
  const btnColor  = isDark ? 'rgba(255,255,255,0.55)' : 'rgba(0,0,0,0.45)';
  const btnBorder = isDark ? '1px solid rgba(255,255,255,0.14)' : '1px solid rgba(0,0,0,0.10)';
  const hoverBg   = isDark ? '#3a3832' : 'rgba(0,0,0,0.12)';
  const hoverColor = isDark ? 'rgba(255,255,255,0.82)' : 'rgba(0,0,0,0.70)';

  return (
    <button
      onClick={toggle}
      title={isDark ? "Switch to light mode" : "Switch to dark mode"}
      style={{
        display:        'flex',
        alignItems:     'center',
        justifyContent: 'center',
        width:           36,
        height:          36,
        borderRadius:   '50%',
        background:     btnBg,
        border:         btnBorder,
        cursor:         'pointer',
        color:          btnColor,
        flexShrink:     0,
        transition:     'background 0.15s, color 0.15s',
      }}
      onMouseEnter={e => {
        (e.currentTarget as HTMLButtonElement).style.background = hoverBg;
        (e.currentTarget as HTMLButtonElement).style.color = hoverColor;
      }}
      onMouseLeave={e => {
        (e.currentTarget as HTMLButtonElement).style.background = btnBg;
        (e.currentTarget as HTMLButtonElement).style.color = btnColor;
      }}
    >
      {isDark ? <SunIcon /> : <MoonIcon />}
    </button>
  );
}

function SunIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="4" />
      <line x1="12" y1="2" x2="12" y2="4" />
      <line x1="12" y1="20" x2="12" y2="22" />
      <line x1="4.22" y1="4.22" x2="5.64" y2="5.64" />
      <line x1="18.36" y1="18.36" x2="19.78" y2="19.78" />
      <line x1="2" y1="12" x2="4" y2="12" />
      <line x1="20" y1="12" x2="22" y2="12" />
      <line x1="4.22" y1="19.78" x2="5.64" y2="18.36" />
      <line x1="18.36" y1="5.64" x2="19.78" y2="4.22" />
    </svg>
  );
}

function MoonIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
    </svg>
  );
}
