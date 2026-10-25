import { useEffect, useState } from 'react';

const STAGES: [number, string][] = [
  [0, 'Looking up the evidence…'],
  [4, 'Checking the facts and the rules…'],
  [12, 'Writing your answer…'],
  [25, 'Still working. Fresh answers can take up to 40 seconds.'],
];

/** A message that follows a long wait, so a slow answer never looks like a frozen one. */
export function useProgress(busy: boolean): string {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    if (!busy) { setSeconds(0); return; }
    const started = Date.now();
    const timer = window.setInterval(() => setSeconds(Math.floor((Date.now() - started) / 1000)), 500);
    return () => window.clearInterval(timer);
  }, [busy]);
  return [...STAGES].reverse().find(([from]) => seconds >= from)![1];
}
