import { useEffect, useState } from 'react';
import { Moon, Sun } from 'lucide-react';
import { Link, NavLink, useLocation } from 'react-router-dom';
import { useTheme } from '../context/ThemeContext';
import { useWorkspace } from '../context/WorkspaceContext';
import Icon from './Icon';

const LINKS = [
  { to: '/', label: 'Home', end: true },
  { to: '/enrich', label: 'Enrich SKU' },
  { to: '/batch', label: 'Batch Catalog' },
];

export default function TopNav() {
  const { exportableResults, exporting, runExport } = useWorkspace();
  const { theme, toggleTheme } = useTheme();
  const [menuOpen, setMenuOpen] = useState(false);
  const location = useLocation();

  // Close the mobile menu on navigation.
  useEffect(() => {
    setMenuOpen(false);
  }, [location.pathname]);

  return (
    <header className="sticky top-0 z-50 border-b border-line/15 bg-background/90 backdrop-blur">
      <div className="mx-auto flex h-20 w-full max-w-shell items-center justify-between px-6 md:px-container-padding">
        {/* Brand */}
        <Link to="/" className="flex items-center gap-3">
          <span className="flex h-9 w-9 items-center justify-center rounded border border-line/15 bg-surface-container">
            <Icon name="manufacturing" size={18} className="text-tertiary-fixed" />
          </span>
          <span className="font-display text-display-sm tracking-tighter text-primary">
            UniPulse AI
          </span>
        </Link>

        {/* Desktop nav */}
        <nav className="hidden items-center gap-10 md:flex" aria-label="Primary">
          {LINKS.map((link) => (
            <NavLink
              key={link.to}
              to={link.to}
              end={link.end}
              className={({ isActive }) =>
                `font-sans text-body-md transition-colors duration-300 ${
                  isActive
                    ? 'border-b border-primary pb-1 font-semibold text-primary'
                    : 'text-on-surface-variant hover:text-primary'
                }`
              }
            >
              {link.label}
            </NavLink>
          ))}
        </nav>

        {/* Trailing actions */}
        <div className="flex items-center gap-3">
          {/* Theme toggle (Lucide Sun/Moon) */}
          <button
            type="button"
            onClick={toggleTheme}
            className="flex h-10 w-10 items-center justify-center rounded border border-outline-variant text-on-surface-variant transition-colors duration-300 hover:border-outline hover:text-primary"
            aria-label={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}
            title={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}
          >
            <span key={theme} className="animate-fade-in flex items-center">
              {theme === 'dark' ? <Sun size={18} strokeWidth={1.75} /> : <Moon size={18} strokeWidth={1.75} />}
            </span>
          </button>

          <button
            type="button"
            onClick={runExport}
            disabled={exporting || exportableResults.length === 0}
            className="btn-ghost hidden !px-5 !py-2.5 md:inline-flex"
            title={
              exportableResults.length === 0
                ? 'Enrich a SKU or run a batch first'
                : `Export ${exportableResults.length} record(s) to Excel`
            }
          >
            <Icon name={exporting ? 'sync' : 'download'} size={18} className={exporting ? 'animate-spin' : ''} />
            {exporting ? 'Exporting…' : 'Export Data'}
          </button>

          <button
            type="button"
            className="text-primary md:hidden"
            onClick={() => setMenuOpen((v) => !v)}
            aria-label="Toggle navigation menu"
            aria-expanded={menuOpen}
          >
            <Icon name={menuOpen ? 'close' : 'menu'} size={26} />
          </button>
        </div>
      </div>

      {/* Mobile menu */}
      {menuOpen && (
        <nav
          className="animate-fade-in border-t border-line/15 bg-surface px-6 py-4 md:hidden"
          aria-label="Mobile"
        >
          <div className="flex flex-col gap-1">
            {LINKS.map((link) => (
              <NavLink
                key={link.to}
                to={link.to}
                end={link.end}
                className={({ isActive }) =>
                  `rounded px-3 py-3 font-sans text-body-md transition-colors ${
                    isActive
                      ? 'bg-surface-container-high font-semibold text-primary'
                      : 'text-on-surface-variant hover:bg-surface-container hover:text-primary'
                  }`
                }
              >
                {link.label}
              </NavLink>
            ))}
            <button
              type="button"
              onClick={runExport}
              disabled={exporting || exportableResults.length === 0}
              className="btn-ghost mt-3 justify-start"
            >
              <Icon name="download" size={18} />
              Export Data
            </button>
          </div>
        </nav>
      )}
    </header>
  );
}
