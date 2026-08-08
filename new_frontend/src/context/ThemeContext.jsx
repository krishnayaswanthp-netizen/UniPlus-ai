import { createContext, useCallback, useContext, useEffect, useState } from 'react';

const STORAGE_KEY = 'unipulse-theme';
const ThemeContext = createContext(null);

function getInitialTheme() {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === 'light' || saved === 'dark') return saved;
  } catch {
    /* storage unavailable — fall through to default */
  }
  return 'dark';
}

/**
 * ThemeProvider flips the `dark` class on <html> (Tailwind `darkMode: class`
 * + CSS variables defined in src/index.css). The choice persists to
 * localStorage; a tiny inline script in index.html applies it before first
 * paint so there is no flash of the wrong theme.
 *
 * While switching, the `theme-anim` class is applied for ~350 ms so every
 * color transition animates smoothly instead of snapping.
 */
export function ThemeProvider({ children }) {
  const [theme, setTheme] = useState(getInitialTheme);

  useEffect(() => {
    const root = document.documentElement;
    root.classList.add('theme-anim');
    root.classList.toggle('dark', theme === 'dark');
    try {
      localStorage.setItem(STORAGE_KEY, theme);
    } catch {
      /* private mode etc. — theme still applies for this session */
    }
    const timer = window.setTimeout(() => root.classList.remove('theme-anim'), 350);
    return () => window.clearTimeout(timer);
  }, [theme]);

  const toggleTheme = useCallback(() => {
    setTheme((prev) => (prev === 'dark' ? 'light' : 'dark'));
  }, []);

  return (
    <ThemeContext.Provider value={{ theme, toggleTheme }}>{children}</ThemeContext.Provider>
  );
}

export function useTheme() {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error('useTheme must be used within <ThemeProvider>');
  return ctx;
}
