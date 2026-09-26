import { useCallback, useEffect, useState } from 'react';

export type ThemePreference = 'light' | 'dark' | 'system';

const STORAGE_KEY = 'outpost.theme';

function readStored(): ThemePreference {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    if (value === 'light' || value === 'dark' || value === 'system') {
      return value;
    }
  } catch {
    // Storage can be unavailable (private mode, blocked site data). Falling
    // back to the system preference is a complete answer, so this is not worth
    // surfacing.
  }
  return 'system';
}

function prefersDark(): boolean {
  return window.matchMedia('(prefers-color-scheme: dark)').matches;
}

/**
 * Theme preference, persisted, with `system` tracking `prefers-color-scheme`
 * live rather than only at boot. The inline script in `index.html` applies the
 * same resolution before first paint to avoid a flash.
 */
export function useTheme(): {
  preference: ThemePreference;
  resolved: 'light' | 'dark';
  setPreference: (next: ThemePreference) => void;
} {
  const [preference, setPreferenceState] =
    useState<ThemePreference>(readStored);
  const [systemDark, setSystemDark] = useState(prefersDark);

  useEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: dark)');
    const onChange = (event: MediaQueryListEvent) => setSystemDark(event.matches);
    media.addEventListener('change', onChange);
    return () => media.removeEventListener('change', onChange);
  }, []);

  const resolved: 'light' | 'dark' =
    preference === 'system' ? (systemDark ? 'dark' : 'light') : preference;

  useEffect(() => {
    document.documentElement.classList.toggle('dark', resolved === 'dark');
    document.documentElement.style.colorScheme = resolved;
  }, [resolved]);

  const setPreference = useCallback((next: ThemePreference) => {
    setPreferenceState(next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Preference still applies for this session.
    }
  }, []);

  return { preference, resolved, setPreference };
}
