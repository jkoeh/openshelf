/** Locate timed units in original text, retaining punctuation and unaligned gaps. */
export function alignedTextParts(text: string, words: { word: string; index: number }[]): { text: string; index?: number }[] {
  const parts: { text: string; index?: number }[] = [];
  let cursor = 0;
  for (const word of words) {
    const value = word.word.trim();
    if (!value) continue;
    const start = text.indexOf(value, cursor);
    if (start < 0) continue;
    if (start > cursor) parts.push({ text: text.slice(cursor, start) });
    const end = start + value.length;
    parts.push({ text: text.slice(start, end), index: word.index });
    cursor = end;
  }
  if (cursor < text.length) parts.push({ text: text.slice(cursor) });
  return parts;
}
