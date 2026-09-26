/** Minimal class joiner. A dependency for this would be silly. */
export function cx(
  ...parts: (string | false | null | undefined)[]
): string {
  return parts.filter(Boolean).join(' ');
}
