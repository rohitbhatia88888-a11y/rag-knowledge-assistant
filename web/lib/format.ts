const UPPER = new Set(["sop", "it", "hr", "pdf"]);

/** "postmortem-2026-03-fleet-console" -> "Postmortem 2026-03 fleet console" */
export function documentName(stem: string): string {
  const words: string[] = [];
  for (const part of stem.split(/[-_]/)) {
    const prev = words[words.length - 1];
    if (/^\d+$/.test(part) && prev && /^\d+(-\d+)*$/.test(prev)) words[words.length - 1] = `${prev}-${part}`;
    else words.push(UPPER.has(part.toLowerCase()) ? part.toUpperCase() : part.toLowerCase());
  }
  const text = words.join(" ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** Two-letter monogram: first letters of two words, or the first two letters of one ("Engineer" -> "EN"). */
export function initials(name: string): string {
  const words = name.trim().split(/[\s@._-]+/).filter(Boolean);
  const mono = words.length > 1 ? words[0][0] + words[1][0] : (words[0] ?? "?").slice(0, 2);
  return mono.toUpperCase();
}
