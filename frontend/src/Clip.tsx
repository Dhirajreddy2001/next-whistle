import { useEffect, useRef, useState } from 'react';
import { interpolate, type Frame } from './tracking';

export type Clip = { clipId: string; kind: string; fromMs: number; toMs: number; focusEventIds: string[] };
type Manifest = { player_order: string[] };

const API = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '');
const manifests = new Map<string, Promise<Manifest>>();
const loadManifest = (matchId: string) => {
  if (!manifests.has(matchId)) manifests.set(matchId, fetch(`${API}/api/matches/${matchId}/tracking/manifest`).then(r => { if (!r.ok) throw new Error('manifest'); return r.json(); }));
  return manifests.get(matchId)!;
};

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

type Props = { matchId: string; clip: Clip; sides: Record<string, 'home' | 'away'>; highlight?: string; markMs: number; clock: number };

/** Replays a key moment as an SVG pitch, drawn from the same tracking frames as the live view. */
export function ClipReplay({ matchId, clip, sides, highlight, markMs, clock }: Props) {
  const root = useRef<HTMLDivElement>(null);
  const [frames, setFrames] = useState<Frame[]>([]);
  const [order, setOrder] = useState<string[]>([]);
  const [failed, setFailed] = useState(false);
  const [visible, setVisible] = useState(false);
  const [playing, setPlaying] = useState(() => !(typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches));
  const [t, setT] = useState(0);

  useEffect(() => {
    const node = root.current;
    if (!node || typeof IntersectionObserver === 'undefined') { setVisible(true); return; }
    const watcher = new IntersectionObserver(entries => setVisible(entries.some(entry => entry.isIntersecting)), { threshold: .25 });
    watcher.observe(node);
    return () => watcher.disconnect();
  }, []);

  const clockNow = useRef(clock); clockNow.current = clock;
  const complete = clock >= clip.toMs; // until then the clip stops at the viewer's clock, so it never shows play from their future
  useEffect(() => {
    if (!visible) return;
    let active = true;
    (async () => {
      try {
        const manifest = await loadManifest(matchId);
        const loaded: Frame[] = [];
        for (let index = Math.floor(clip.fromMs / 60_000); index <= Math.floor(clip.toMs / 60_000); index++) {
          const response = await fetch(`${API}/api/matches/${matchId}/tracking/chunks/${index}?cutoffMs=${complete ? clip.toMs : Math.floor(clockNow.current)}`);
          if (!response.ok) continue;
          loaded.push(...((await response.json()).frames as Frame[]).filter(frame => frame[0] >= clip.fromMs && frame[0] <= (complete ? clip.toMs : clockNow.current)));
        }
        if (!active) return;
        if (loaded.length < 2) throw new Error('empty');
        setOrder(manifest.player_order); setFrames(loaded);
      } catch { if (active) setFailed(true); }
    })();
    return () => { active = false; };
  }, [visible, matchId, clip.clipId, complete]);

  const duration = frames.length ? frames[frames.length - 1][0] - clip.fromMs : 0;
  useEffect(() => {
    if (!visible || !playing || !duration) return;
    let frame = 0; let last = performance.now(); let pause = 0;
    const tick = (now: number) => {
      const step = now - last; last = now;
      if (pause > 0) { pause -= step; if (pause <= 0) setT(0); }
      else setT(current => { const next = current + step; if (next >= duration) { pause = 1200; return duration; } return next; });
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [visible, playing, duration]);

  const sample = frames.length ? interpolate(frames, order, clip.fromMs + t) : null;
  const markAt = duration ? Math.max(0, Math.min(100, (markMs - clip.fromMs) / duration * 100)) : 0;
  const seconds = (ms: number) => `${Math.floor(ms / 60_000)}:${String(Math.floor(ms / 1000) % 60).padStart(2, '0')}`;
  if (failed) return <div className="clip-replay unavailable" ref={root}>Replay unavailable for this moment.</div>;
  return <div className="clip-replay" ref={root}>
    <svg className="clip-pitch" viewBox="-1 -1 107 70" role="img" aria-label={`Replay of the ${clip.kind.replaceAll('_', ' ')} at ${seconds(markMs)}`}>
      <rect className="clip-ground" x="-1" y="-1" width="107" height="70" />
      <g className="clip-lines"><rect width="105" height="68" /><path d="M52.5 0V68" /><circle cx="52.5" cy="34" r="9.15" /><path d="M0 13.84H16.5V54.16H0M105 13.84H88.5V54.16H105M0 24.84H5.5V43.16H0M105 24.84H99.5V43.16H105" /></g>
      {sample && order.map(id => { const at = sample.positions[id]; return at ? <g key={id} className={`clip-player ${sides[id] ?? 'away'} ${id === highlight ? 'focus' : ''}`} transform={`translate(${at[0].toFixed(2)} ${at[1].toFixed(2)})`}>{id === highlight && <circle className="clip-ring" r="2.6" />}<circle r="1.35" /></g> : null; })}
      {sample && <circle className="clip-ball" cx={sample.ball[0]} cy={sample.ball[1]} r=".75" />}
    </svg>
    <div className="clip-controls">
      <button type="button" className="clip-play" onClick={() => { if (t >= duration) setT(0); setPlaying(value => !value); }} aria-label={playing ? 'Pause replay' : 'Play replay'}>
        <svg viewBox="0 0 16 16" aria-hidden="true">{playing ? <path d="M4.5 3h2.5v10H4.5zM9 3h2.5v10H9z" /> : <path d="M5 3l8 5-8 5z" />}</svg>
      </button>
      <span className="clip-track"><input type="range" min={0} max={duration || 1} value={Math.min(t, duration || 1)} aria-label="Replay position" onChange={e => { setPlaying(false); setT(Number(e.target.value)); }} /><i className="clip-mark" style={{ left: `${markAt}%` }} /></span>
      <time>{seconds(clip.fromMs + t)}</time>
    </div>
    {!complete && <small className="clip-note">The rest of this replay appears as the match reaches it.</small>}
  </div>;
}
