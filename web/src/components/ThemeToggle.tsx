import { cx } from '../lib/cx';
import type { ThemePreference } from '../hooks/useTheme';
import { MonitorIcon, MoonIcon, SunIcon } from './icons';

const OPTIONS: {
  value: ThemePreference;
  label: string;
  Icon: typeof SunIcon;
}[] = [
  { value: 'light', label: 'Light', Icon: SunIcon },
  { value: 'system', label: 'System', Icon: MonitorIcon },
  { value: 'dark', label: 'Dark', Icon: MoonIcon },
];

interface Props {
  preference: ThemePreference;
  onChange: (next: ThemePreference) => void;
}

export function ThemeToggle({ preference, onChange }: Props) {
  return (
    <div
      role="radiogroup"
      aria-label="Colour theme"
      className="flex items-center rounded-md border border-slate-300 p-0.5 dark:border-slate-700"
    >
      {OPTIONS.map(({ value, label, Icon }) => (
        <button
          key={value}
          type="button"
          role="radio"
          aria-checked={preference === value}
          aria-label={label}
          title={label}
          onClick={() => onChange(value)}
          className={cx(
            'rounded p-1.5 transition-colors',
            preference === value
              ? 'bg-slate-200 text-slate-900 dark:bg-slate-700 dark:text-slate-100'
              : 'text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200',
          )}
        >
          <Icon />
        </button>
      ))}
    </div>
  );
}
