/**
 * Inline icons.
 *
 * Hand-rolled rather than pulled from a pack: there are six of them, and the
 * shapes matter — each eligibility state needs a glyph distinguishable without
 * colour, so they are deliberately different silhouettes (tick / circle / bar).
 */

interface IconProps {
  className?: string;
}

const base = 'h-3.5 w-3.5 shrink-0';

export function CheckIcon({ className }: IconProps) {
  return (
    <svg
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className={className ?? base}
    >
      <path d="M3 8.5 6.5 12 13 4" />
    </svg>
  );
}

/** Informational, not a warning triangle — see `lib/eligibility.ts`. */
export function QuestionIcon({ className }: IconProps) {
  return (
    <svg
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className={className ?? base}
    >
      <circle cx="8" cy="8" r="6.25" />
      <path d="M6.3 6.2a1.75 1.75 0 1 1 2.2 1.9v1.1" />
      <path d="M8.5 11.6h.01" />
    </svg>
  );
}

export function BlockIcon({ className }: IconProps) {
  return (
    <svg
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      aria-hidden="true"
      className={className ?? base}
    >
      <circle cx="8" cy="8" r="6.25" />
      <path d="M4.8 8h6.4" />
    </svg>
  );
}

export function ExternalLinkIcon({ className }: IconProps) {
  return (
    <svg
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className={className ?? base}
    >
      <path d="M9.5 2.5H13.5V6.5" />
      <path d="M13.5 2.5 7.5 8.5" />
      <path d="M12 9.8v3.2a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1h3.2" />
    </svg>
  );
}

export function CloseIcon({ className }: IconProps) {
  return (
    <svg
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      aria-hidden="true"
      className={className ?? base}
    >
      <path d="M4 4l8 8M12 4l-8 8" />
    </svg>
  );
}

export function SunIcon({ className }: IconProps) {
  return (
    <svg
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      aria-hidden="true"
      className={className ?? base}
    >
      <circle cx="8" cy="8" r="3" />
      <path d="M8 1v1.5M8 13.5V15M1 8h1.5M13.5 8H15M3.1 3.1l1 1M11.9 11.9l1 1M12.9 3.1l-1 1M4.1 11.9l-1 1" />
    </svg>
  );
}

export function MoonIcon({ className }: IconProps) {
  return (
    <svg
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className={className ?? base}
    >
      <path d="M13.5 9.6A5.8 5.8 0 0 1 6.4 2.5a5.8 5.8 0 1 0 7.1 7.1Z" />
    </svg>
  );
}

export function MonitorIcon({ className }: IconProps) {
  return (
    <svg
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className={className ?? base}
    >
      <rect x="1.75" y="2.75" width="12.5" height="8.5" rx="1" />
      <path d="M5.5 14h5" />
    </svg>
  );
}
