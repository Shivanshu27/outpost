import type { ThemePreference } from '../hooks/useTheme';
import type { MetaOut } from '../types';
import { ThemeToggle } from './ThemeToggle';

interface Props {
  meta: MetaOut | undefined;
  themePreference: ThemePreference;
  onThemeChange: (next: ThemePreference) => void;
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <span className="whitespace-nowrap">
      <span className="text-slate-500 dark:text-slate-400">{label} </span>
      <span className="font-medium text-slate-800 dark:text-slate-200">
        {value}
      </span>
    </span>
  );
}

export function Header({ meta, themePreference, onThemeChange }: Props) {
  const noProvider = meta?.llm_provider === 'none';

  return (
    <header className="border-b border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-950">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 px-4 py-2.5">
        <h1 className="text-base font-semibold tracking-tight">
          Outpost
          {meta && (
            <span className="ml-1.5 font-mono text-xs font-normal text-slate-400 dark:text-slate-500">
              {meta.version}
            </span>
          )}
        </h1>

        {meta && (
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
            <Fact label="Country" value={meta.country} />
            <Fact label="Timezone" value={meta.timezone} />
            <Fact
              label="Sources"
              value={String(meta.sources.length)}
            />
            <Fact label="Rules" value={String(meta.rule_count)} />
            <Fact
              label="Scoring"
              value={noProvider ? 'not configured' : meta.llm_provider}
            />
          </div>
        )}

        <div className="ml-auto">
          <ThemeToggle preference={themePreference} onChange={onThemeChange} />
        </div>
      </div>

      {/*
        Deliberately a quiet line of prose, not a banner with an icon and a
        dismiss button. No provider is a supported configuration — the pipeline
        still runs through stage 7 and most of the value is there. Nagging
        about it would be selling an upgrade the user did not ask for.
      */}
      {noProvider && (
        <p className="border-t border-slate-100 px-4 py-1.5 text-xs text-slate-500 dark:border-slate-800/80 dark:text-slate-400">
          No scoring provider is configured, so the match-score column is empty.
          Everything else — eligibility, verification, ranking by pre-score — is
          unaffected.
        </p>
      )}
    </header>
  );
}
