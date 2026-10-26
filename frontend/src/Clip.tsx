export type Clip = { clipId: string; kind: string; fromMs: number; toMs: number; focusEventIds: string[] };
/** A small original icon for each kind of moment, drawn with currentColor so it follows the theme. */
export function EventIcon({ type }: { type: string }) {
  const common = { viewBox: '0 0 16 16', 'aria-hidden': true, className: `event-icon ${type}` } as const;
  if (type === 'goal') return <svg {...common}><circle cx="8" cy="8" r="6.4" fill="none" stroke="currentColor" strokeWidth="1.3" /><path d="M8 5l2.6 1.9-1 3H6.4l-1-3z" fill="currentColor" /><path d="M8 5V1.8M10.6 6.9l3-1M9.6 9.9l1.9 2.6M6.4 9.9l-1.9 2.6M5.4 6.9l-3-1" stroke="currentColor" strokeWidth="1" fill="none" /></svg>;
  if (type === 'card_yellow' || type === 'card_red') return <svg {...common}><rect x="4" y="1.8" width="8" height="12.4" rx="1.4" transform="rotate(8 8 8)" /></svg>;
  if (type === 'goal_disallowed') return <svg {...common}><circle cx="8" cy="8" r="6.4" fill="none" stroke="currentColor" strokeWidth="1.3" /><path d="M4.2 11.8l7.6-7.6" stroke="currentColor" strokeWidth="1.5" /></svg>;
  if (type === 'offside') return <svg {...common}><path d="M4.2 1.6v12.8" stroke="currentColor" strokeWidth="1.4" fill="none" /><path d="M4.9 2.4h7.6L10.4 5.6l2.1 3.2H4.9z" fill="currentColor" /></svg>;
  if (type === 'period_end' || type === 'period_start') return <svg {...common}><circle cx="8" cy="8" r="6.4" fill="none" stroke="currentColor" strokeWidth="1.3" /><path d="M8 4.4V8l2.4 1.6" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" /></svg>;
  return <svg {...common}><circle cx="8" cy="8" r="3.4" fill="currentColor" /></svg>;
}

type BannerProps = { title: string; from: number; to: number; clock: number; running: boolean; onToggle: () => void; onExit: () => void };

/** Shown above the playback bar while a clip is replaying on the main pitch. */
export function ReplayBanner({ title, from, to, clock, running, onToggle, onExit }: BannerProps) {
  const progress = to > from ? Math.max(0, Math.min(1, (clock - from) / (to - from))) : 1;
  const done = clock >= to;
  return <div className="replay-banner" role="status">
    <strong>Replay</strong><span className="replay-title">{title}</span>
    <span className="replay-progress" aria-hidden="true"><i style={{ width: `${progress * 100}%` }} /></span>
    <button type="button" onClick={onToggle}>{done ? 'Replay again' : running ? 'Pause' : 'Resume'}</button>
    <button type="button" onClick={onExit}>Back to live</button>
  </div>;
}
