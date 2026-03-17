/**
 * Merges class names, filtering out falsy values.
 * Mirrors the behaviour of `clsx` for the common cases used in this project.
 * Accepts any mix of strings, undefined, null, false, and conditional objects
 * ({ 'my-class': booleanCondition }).
 */
export function cn(
  ...inputs: (string | undefined | null | false | Record<string, boolean>)[]
): string {
  const classes: string[] = [];
  for (const input of inputs) {
    if (!input) continue;
    if (typeof input === 'string') {
      classes.push(input);
    } else if (typeof input === 'object') {
      for (const [key, value] of Object.entries(input)) {
        if (value) classes.push(key);
      }
    }
  }
  return classes.join(' ');
}

export function formatSalary(value: number): string {
  if (!Number.isFinite(value)) return '$0'
  return `$${Math.round(value).toLocaleString()}`
}

export function formatProjection(value: number): string {
  if (!Number.isFinite(value)) return '0.0'
  return value.toFixed(2)
}
