import { StrictMode, createContext, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { requestExplanation, requestRefereeAnswer, type GenerationResult } from './api/client';
import { AskPanel } from './AskPanel';
import { useProgress } from './useProgress';
import './style.css';

type Team = { teamId: string; name: string; shortName: string; colors: string[] };
type Match = { matchId: string; label: string; kickoffDate: string; home: Team; away: Team };
type Player = { playerId: string; teamId: string; name: string; shirt: number; position: string; starter: boolean };
type Event = { eventId: string; matchMs: number; availableMs: number; type: string; teamId?: string; playerId?: string; outcome?: string; start?: { x: number; y: number }; decision?: { decisionType: string; recordedRuling: string; reversesEventId?: string; ruleIds?: string[] } };
type TeamStats = { teamId: string; shots: number; shotsOnTarget: number; passes: number; passesCompleted: number; passCompletionPct: number | null; possessionPct: number; goals: number; yellowCards: number; redCards: number };
type Frame = [number, number, number, number, number, number[], 'controlled' | 'flight' | 'dead'];
type TrackingSample = { positions: Record<string, [number, number]>; ball: [number, number] };
type DatasetView = { match: Match; players: Player[]; events: Event[]; stats: TeamStats[]; cutoffMs: number; error: string | null };
type UiEvent = Event & { title: string; detail: string; minute: string; kind: 'goal' | 'card' | 'break' | 'review' };
const API = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '');
const url = (path: string) => `${API}${path}`;
const MATCH_END_MS = 96 * 60_000;
const emptyView: DatasetView = { match: { matchId: '', label: '', kickoffDate: '', home: { teamId: '', name: '', shortName: '', colors: [] }, away: { teamId: '', name: '', shortName: '', colors: [] } }, players: [], events: [], stats: [], cutoffMs: 0, error: null };
const Context = createContext<DatasetView>(emptyView);
const useData = () => useContext(Context);

type Level = 'beginner' | 'intermediate' | 'pro';
type Theme = 'dark' | 'light';
type Layer = 'pressure' | 'passing' | 'offside' | 'none';
const LAYERS: [Layer, string][] = [['pressure', 'Pressure'], ['passing', 'Passing lanes'], ['offside', 'Offside line']];
type Prefs = { perspective: string; level: Level; language: string; theme: Theme };
const PERSPECTIVES = ['Neutral', 'Home supporter', 'Away supporter', 'Analyst'];
const LANGUAGES = ['English', 'Español', 'Français', 'Deutsch'];
const LEVEL_HINTS: Record<Level, string> = { beginner: 'Plain-language explanations, fewer numbers.', intermediate: 'Adds shot quality and key stats.', pro: 'Full detail: xG, momentum and rule references.' };
const prefsKey = 'next-whistle-viewer';
const defaults: Prefs = { perspective: 'Neutral', level: 'beginner', language: 'English', theme: 'dark' };
function readPrefs(): Prefs { try { return { ...defaults, ...JSON.parse(localStorage.getItem(prefsKey) ?? '{}') }; } catch { return defaults; } }
try { document.documentElement.dataset.theme = readPrefs().theme; } catch { /* theme falls back to the stylesheet default */ }
function formatClock(ms: number) { const seconds = Math.floor(ms / 1000); return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`; }
function sampleTracking(frames: Frame[], order: string[], clockMs: number): TrackingSample | null {
  if (!frames.length) return null;
  const last = frames[frames.length - 1]; const previous = frames.length > 1 ? frames[frames.length - 2] : last;
  const dt = Math.max(0, Math.min(.5, (clockMs - last[0]) / 1000));
  const positions = Object.fromEntries(order.map((id, index) => {
    const offset = index * 4; const x = last[5][offset] + last[5][offset + 2] * dt; const y = last[5][offset + 1] + last[5][offset + 3] * dt;
    return [id, [Math.max(0, Math.min(105, x)), Math.max(0, Math.min(68, y))] as [number, number]];
  }));
  const span = Math.max(.001, (last[0] - previous[0]) / 1000);
  const ball = [last[1] + (last[1] - previous[1]) / span * dt, last[2] + (last[2] - previous[2]) / span * dt] as [number, number];
  return { positions, ball: [Math.max(0, Math.min(105, ball[0])), Math.max(0, Math.min(68, ball[1]))] };
}
function badgeClass(teamId: string) { return teamId.replace(/^t_/, ''); }
function TeamBadge({ team }: { team: Team }) { return <span className={`team-badge ${badgeClass(team.teamId)}`} style={{ background: team.colors[0], color: team.colors[1] }} aria-label={team.name}>{team.shortName}</span>; }
function Mark() { return <span className="brand-mark" aria-hidden="true"><i /><i /><i /></span>; }
function Button({ children, className = '', ...props }: React.ButtonHTMLAttributes<HTMLButtonElement>) { return <button className={`button ${className}`.trim()} {...props}>{children}</button>; }
function useMatchData(match: Match, clockMs: number) {
  const [data, setData] = useState<DatasetView>({ ...emptyView, match });
  const eventCache = useRef<Event[]>([]); const loadedThrough = useRef(-1);
  useEffect(() => {
    if (!match.matchId) return;
    const controller = new AbortController();
    const cutoff = Math.floor(clockMs / 5000) * 5000;
    const get = async (path: string) => { const response = await fetch(url(path), { signal: controller.signal, cache: 'no-store' }); if (!response.ok) throw new Error(`Request failed (${response.status}): ${path}`); return response.json(); };
    const load = async () => {
      const [squads, stats] = await Promise.all([get(`/api/matches/${match.matchId}/squads`), get(`/api/matches/${match.matchId}/stats?cutoffMs=${cutoff}`)]);
      if (cutoff > loadedThrough.current) {
        const timeline = loadedThrough.current < 0
          ? await get(`/api/matches/${match.matchId}/timeline?cutoffMs=${cutoff}`)
          : await get(`/api/matches/${match.matchId}/replay?cutoffMs=${cutoff}&fromMs=${loadedThrough.current}`);
        if (loadedThrough.current < 0) eventCache.current = timeline.events ?? [];
        else eventCache.current = [...eventCache.current, ...(timeline.events ?? [])];
        loadedThrough.current = cutoff;
      }
      if (!controller.signal.aborted) setData({ match, players: squads.teams.flatMap((side: { teamId: string; players: Omit<Player, 'teamId'>[] }) => side.players.map(player => ({ ...player, teamId: side.teamId })).filter(player => player.starter).sort((a, b) => a.shirt - b.shirt)), events: eventCache.current, stats: stats.teams ?? [], cutoffMs: cutoff, error: null });
    };
    load().catch(error => { if (error.name !== 'AbortError') setData(current => ({ ...current, error: 'Could not load generated match data. Check that the backend is running, then retry.' })); });
    return () => controller.abort();
  }, [match.matchId, Math.floor(clockMs / 5000)]);
  return data;
}
function eventPresentation(event: Event, data: DatasetView): UiEvent {
  const home = event.teamId === data.match.home.teamId;
  const teamName = event.teamId ? (home ? data.match.home.name : data.match.away.name) : '';
  const playerName = data.players.find(player => player.playerId === event.playerId)?.name;
  const minute = `${Math.floor(event.matchMs / 60_000) + 1}'`;
  let title = `${playerName ?? teamName} ${event.type.replaceAll('_', ' ')}`.trim();
  if (event.type === 'goal_disallowed') title = `${teamName} goal disallowed`;
  if (event.type === 'period_end') title = 'Half-time';
  if (event.type === 'period_start') title = 'Kick-off';
  if (playerName && event.type.startsWith('card')) title = `${playerName} shown a ${event.type === 'card_red' ? 'red' : 'yellow'} card`;
  if (event.type === 'goal' && playerName) title = `${playerName} scores`;
  const detail = event.decision ? `Recorded ruling: ${event.decision.recordedRuling.replaceAll('_', ' ')}${event.decision.ruleIds?.length ? ` · ${event.decision.ruleIds.join(', ')}` : ''}.` : playerName ? `${playerName}${event.outcome ? ` · ${event.outcome.replaceAll('_', ' ')}` : ''}.` : `${event.type.replaceAll('_', ' ')} recorded.`;
  const kind = event.type.includes('goal') ? 'goal' : event.type.includes('card') ? 'card' : event.type.startsWith('period') ? 'break' : event.decision ? 'review' : 'break';
  return { ...event, title, detail, minute, kind };
}
function App() {
  const [prefs, setPrefs] = useState(readPrefs);
  useEffect(() => { document.documentElement.dataset.theme = prefs.theme; localStorage.setItem(prefsKey, JSON.stringify(prefs)); }, [prefs]);
  const matchId = window.location.pathname.match(/^\/match\/([^/]+)/)?.[1];
  return matchId ? <ReplayApp matchId={matchId} prefs={prefs} setPrefs={setPrefs} /> : <MatchPicker />;
}
function MatchPicker() {
  const [matches, setMatches] = useState<Match[]>([]); const [error, setError] = useState('');
  const load = () => fetch(url('/api/matches')).then(response => { if (!response.ok) throw new Error(); return response.json(); }).then(result => { setMatches(result.matches); setError(''); }).catch(() => setError('Match catalog unavailable. Start the backend and try again.'));
  useEffect(() => { void load(); }, []);
  return <div className="picker"><header className="topbar"><a className="brand" href="/"><Mark /><span>Next Whistle</span></a></header><main><header><h1>Choose a match</h1><p>Follow the action, inspect turning points, and understand the decisions shaping the game.</p></header>{error ? <div className="empty-panel"><p role="alert">{error}</p><Button onClick={() => void load()}>Retry</Button></div> : <div className="fixture-list">{matches.map((match, index) => <a className="fixture" href={`/match/${match.matchId}`} key={match.matchId}><div><span>{match.label} · Matchday {index + 1}</span><time>{new Date(`${match.kickoffDate}T12:00:00`).toLocaleDateString()}</time></div><div className="fixture-teams"><TeamBadge team={match.home} /><strong>{match.home.name}</strong><span>v</span><strong>{match.away.name}</strong><TeamBadge team={match.away} /></div><span className="open-match">Open match →</span></a>)}</div>}</main></div>;
}
function ReplayApp({ matchId, prefs, setPrefs }: { matchId: string; prefs: Prefs; setPrefs: (p: Prefs) => void }) {
  const [catalog, setCatalog] = useState<Match[]>([]); const [liveEdge, setLiveEdge] = useState(0); const [clock, setClock] = useState(0); const [playing, setPlaying] = useState(true); const [selected, setSelected] = useState<string | null>(null); const [panel, setPanel] = useState<Panel>('insights'); const [tracked, setTracked] = useState<string[]>([]); const [hovered, setHovered] = useState<string | null>(null); const [trackingFrames, setTrackingFrames] = useState<Frame[]>([]); const [trackingOrder, setTrackingOrder] = useState<string[]>([]);
  const [liveClock, setLiveClock] = useState<{ offset: number; epoch: number; duration: number } | null>(null);
  const clockRef = useRef(clock); clockRef.current = clock;
  const trackingCursor = useRef(-1);
  useEffect(() => { fetch(url('/api/matches')).then(r => r.json()).then(v => setCatalog(v.matches ?? [])).catch(() => undefined); fetch(url('/api/live-state'), { cache: 'no-store' }).then(r => { if (!r.ok) throw new Error(); return r.json(); }).then(s => { const lc = { offset: s.serverTimeMs - Date.now(), epoch: s.instanceStartMs - s.cycleNumber * s.durationMs, duration: s.durationMs }; setLiveClock(lc); setLiveEdge(s.matchMs); setClock(s.matchMs); }).catch(() => undefined); }, []);
  const match = catalog.find(item => item.matchId === matchId);
  const data = useMatchData(match ?? emptyView.match, clock);
  useEffect(() => {
    if (!match) return;
    let active = true; let busy = false; let timer = 0;
    trackingCursor.current = -1; setTrackingFrames([]); setTrackingOrder([]);
    const run = async (manifest: { player_order: string[]; chunks: { from_ms: number }[] }) => {
      if (!active || busy) return; busy = true;
      try {
        const cutoff = Math.floor(clockRef.current); const index = Math.floor(cutoff / 60_000); const chunk = manifest.chunks[index];
        if (!chunk || chunk.from_ms > cutoff) return;
        if (trackingCursor.current > cutoff) { trackingCursor.current = -1; setTrackingFrames([]); }
        const cursor = trackingCursor.current;
        const suffix = cursor >= 0 ? `&fromMs=${cursor}` : '';
        const response = await fetch(url(`/api/matches/${match.matchId}/tracking/chunks/${index}?cutoffMs=${cutoff}${suffix}`), { cache: 'no-store' });
        if (!response.ok || !active) return;
        const rows = (await response.json()).frames as Frame[];
        if (rows.length) {
          trackingCursor.current = rows[rows.length - 1][0];
          setTrackingFrames(current => [...current, ...rows].slice(-2));
        }
      } catch { /* Tracking can recover on the next poll; match events and stats remain available. */ }
      finally { busy = false; }
    };
    fetch(url(`/api/matches/${match.matchId}/tracking/manifest`)).then(r => { if (!r.ok) throw new Error(); return r.json(); }).then(manifest => {
      if (!active) return;
      setTrackingOrder(manifest.player_order ?? []);
      void run(manifest);
      timer = window.setInterval(() => void run(manifest), 500);
    }).catch(() => undefined);
    return () => { active = false; window.clearInterval(timer); };
  }, [match?.matchId]);
  const dataCurrent = data.cutoffMs <= clock;
  const events = useMemo(() => (dataCurrent ? data.events : []).map(event => eventPresentation(event, data)).filter(event => event.availableMs <= clock), [data.events, data.players, data.match, dataCurrent, clock]);
  const stats = dataCurrent ? data.stats : []; const homeStats = stats.find(t => t.teamId === data.match.home.teamId); const awayStats = stats.find(t => t.teamId === data.match.away.teamId);
  const reversed = new Set(events.map(e => e.decision?.reversesEventId).filter(Boolean));
  const goals = (teamId: string) => events.filter(e => e.type === 'goal' && e.teamId === teamId && !reversed.has(e.eventId)).length;
  const selectedEvent = events.find(e => e.eventId === selected) ?? null;
  const displayedPlayers = data.players.filter(p => p.starter);
  const trackingSample = sampleTracking(trackingFrames.filter(frame => frame[0] <= clock), trackingOrder, clock);
  const gridRef = useRef<HTMLDivElement>(null); const dragging = useRef(false);
  const [panelWidth, setPanelWidth] = useState(260); const [panelOpen, setPanelOpen] = useState(true);
  useEffect(() => { if (!liveClock) return; const timer = window.setInterval(() => { const elapsed = Date.now() + liveClock.offset - liveClock.epoch; const edge = ((elapsed % liveClock.duration) + liveClock.duration) % liveClock.duration; setLiveEdge(edge); setClock(current => playing ? edge : Math.min(current, edge)); }, 100); return () => window.clearInterval(timer); }, [liveClock, playing]);
  const seek = (ms: number, event?: UiEvent) => { const t = Math.max(0, Math.min(liveEdge, event ? Math.max(ms, event.availableMs) : ms)); setPlaying(false); setClock(t); setSelected(event?.eventId ?? null); };
  const resizeTo = (clientX: number) => { const rect = gridRef.current?.getBoundingClientRect(); if (!rect) return; setPanelOpen(true); setPanelWidth(Math.round(Math.max(220, Math.min(560, rect.width * .55, rect.right - clientX - 8)))); };
  const resizeKey = (event: React.KeyboardEvent) => { if (event.key === 'ArrowLeft') { event.preventDefault(); setPanelOpen(true); setPanelWidth(width => Math.min(560, width + 16)); } else if (event.key === 'ArrowRight') { event.preventDefault(); setPanelWidth(width => Math.max(220, width - 16)); } else if (event.key === 'Home') { event.preventDefault(); setPanelOpen(true); setPanelWidth(260); } else if (event.key === 'Enter') { event.preventDefault(); setPanelOpen(value => !value); } };
  const toggleTracking = (id: string) => setTracked(current => current.includes(id) ? current.filter(v => v !== id) : current.length < 3 ? [...current, id] : current);
  const activePlayer = displayedPlayers.find(p => p.playerId === (hovered ?? tracked.at(-1)));
  const metricCards = homeStats && awayStats;
  if (!match) return <div className="picker"><main><h1>{catalog.length ? 'Match not found' : 'Loading match…'}</h1><a href="/">Back to matches</a></main></div>;
  if (data.error) return <div className="picker"><main><h1>{match.label}</h1><p role="alert">{data.error}</p><Button onClick={() => window.location.reload()}>Retry</Button></main></div>;
  return <Context.Provider value={data}><div className="app-shell">
    <header className="live-header"><a className="brand" href="/" aria-label="Next Whistle match picker"><Mark /><span>Next Whistle</span></a><div className="compact-score"><div><TeamBadge team={match.home} /><span>{match.home.name}</span></div><strong aria-label={`${match.home.name} ${goals(match.home.teamId)}, ${match.away.name} ${goals(match.away.teamId)}`}>{goals(match.home.teamId)}<i>–</i>{goals(match.away.teamId)}</strong><div><span>{match.away.name}</span><TeamBadge team={match.away} /></div></div><div className="header-live"><span className="header-date">{new Date().toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })}</span><Personalize prefs={prefs} onChange={setPrefs} /><span className="header-clock"><strong>{formatClock(clock)}</strong><small>{clock < 45 * 60_000 ? 'First half' : 'Second half'}</small></span><button className={`live-state ${playing ? 'is-live' : ''}`} onClick={() => { setClock(liveEdge); setPlaying(true); }}><span />{playing && Math.abs(clock - liveEdge) < 2500 ? 'Live' : 'Go live'}</button></div><section className="match-insights" aria-label="Generated match statistics"><div className="insight-grid">{metricCards ? <><Metric label="Possession" value={`${homeStats.possessionPct}% – ${awayStats.possessionPct}%`} detail={`${match.home.shortName} — ${match.away.shortName}`} /><Metric label="Shots" value={`${homeStats.shots} – ${awayStats.shots}`} detail={`Through ${formatClock(data.cutoffMs)}`} /><Metric label="On target" value={`${homeStats.shotsOnTarget} – ${awayStats.shotsOnTarget}`} detail="Attempts on target" /><Metric label="Pass completion" value={`${homeStats.passCompletionPct ?? '—'}% – ${awayStats.passCompletionPct ?? '—'}%`} detail="Completed passes / attempts" /></> : <div className="metric-card">Loading statistics for {formatClock(clock)}…</div>}</div></section></header>
    <main className="workspace"><h1 className="sr-only">{match.label} live match</h1>
      <div className={`replay-grid ${panelOpen ? '' : 'panel-collapsed'}`} ref={gridRef} style={{ '--panel-width': `${panelOpen ? panelWidth : 0}px` } as React.CSSProperties}><section className="replay-stage" aria-label="Live match replay"><Pitch clock={clock} event={selectedEvent} tracking={trackingSample} tracked={tracked} activePlayer={activePlayer} setHovered={setHovered} toggleTracking={toggleTracking} events={events} /><div className="playback" role="group" aria-label="Playback controls"><button className="play-toggle" onClick={() => setPlaying(value => !value)} aria-label={playing ? 'Pause' : 'Play'}>{playing ? <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="6" y="5" width="4" height="14" rx="1" /><rect x="14" y="5" width="4" height="14" rx="1" /></svg> : <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5.5v13a1 1 0 0 0 1.5.86l10.5-6.5a1 1 0 0 0 0-1.72L9.5 4.64A1 1 0 0 0 8 5.5Z" /></svg>}</button><span className="playback-time">{formatClock(clock)}</span><label className="scrubber"><span className="sr-only">Match time</span><input type="range" min="0" max={liveEdge} step="1000" value={clock} onChange={event => seek(Number(event.target.value))} /></label><button type="button" className={`playback-live ${playing ? 'is-live' : ''}`} onClick={() => { setClock(liveEdge); setPlaying(true); }}><i />{playing ? 'Live' : 'Go live'}</button></div></section><div className="panel-divider"><div className="panel-resizer" role="separator" aria-orientation="vertical" aria-label="Resize match details panel. Arrow keys adjust, Home resets, Enter collapses." aria-valuemin={220} aria-valuemax={560} aria-valuenow={panelOpen ? panelWidth : 0} tabIndex={0} onPointerDown={event => { dragging.current = true; event.currentTarget.setPointerCapture(event.pointerId); }} onPointerMove={event => { if (dragging.current) resizeTo(event.clientX); }} onPointerUp={() => { dragging.current = false; }} onPointerCancel={() => { dragging.current = false; }} onDoubleClick={() => setPanelWidth(260)} onKeyDown={resizeKey}><span aria-hidden="true" /></div><button type="button" className="panel-collapse" aria-label={panelOpen ? 'Collapse match details panel' : 'Expand match details panel'} aria-expanded={panelOpen} onClick={() => setPanelOpen(value => !value)}>{panelOpen ? '›' : '‹'}</button></div><SidePanel matchId={matchId} language={prefs.language} level={prefs.level} panel={panel} setPanel={setPanel} clock={clock} events={events} selected={selected} seek={seek} stats={stats} /></div>
    </main>
  </div></Context.Provider>;
}
function Metric({ label, value, detail }: { label: string; value: string; detail: string }) { return <article className="metric-card"><div className="metric-heading"><span>{label}</span><strong>{value}</strong></div><p>{detail}</p></article>; }
function Personalize({ prefs, onChange }: { prefs: Prefs; onChange: (p: Prefs) => void }) {
  const [open, setOpen] = useState(false);
  return <div className="personalize"><button className="personalize-trigger" onClick={() => setOpen(!open)}>Personalize</button>{open && <form className="personalize-menu" onSubmit={e => e.preventDefault()}><header><strong>Your match view</strong><button type="button" onClick={() => setOpen(false)} aria-label="Close">×</button></header>
    <label>Perspective<select value={prefs.perspective} onChange={e => onChange({ ...prefs, perspective: e.target.value })}>{PERSPECTIVES.map(p => <option key={p}>{p}</option>)}</select></label>
    <label>Viewer level<select value={prefs.level} onChange={e => onChange({ ...prefs, level: e.target.value as Level })}><option value="beginner">Beginner</option><option value="intermediate">Intermediate</option><option value="pro">Pro</option></select></label><p className="level-hint">{LEVEL_HINTS[prefs.level]}</p>
    <label>Language<select value={prefs.language} onChange={e => onChange({ ...prefs, language: e.target.value })}>{LANGUAGES.map(l => <option key={l}>{l}</option>)}</select></label>
    <fieldset className="theme-field"><legend>Appearance</legend><div className="theme-toggle">{(['dark', 'light'] as Theme[]).map(theme => <button type="button" key={theme} aria-pressed={prefs.theme === theme} onClick={() => onChange({ ...prefs, theme })}>{theme === 'dark' ? 'Dark' : 'Light'}</button>)}</div></fieldset></form>}</div>;
}
function Pitch({ clock, event, tracking, tracked, activePlayer, setHovered, toggleTracking, events }: { clock: number; event: UiEvent | null; tracking: TrackingSample | null; tracked: string[]; activePlayer?: Player; setHovered: (id: string | null) => void; toggleTracking: (id: string) => void; events: UiEvent[] }) {
  const data = useData(); const [feedOpen, setFeedOpen] = useState(true); const [layer, setLayer] = useState<Layer>('pressure');
  const teamById = new Map([[data.match.home.teamId, 'home'], [data.match.away.teamId, 'away']]);
  const positions = tracking?.positions ?? {};
  const ball = tracking?.ball;
  const visibleFeed = events.slice(-40); const feedRef = useRef<HTMLDivElement>(null);
  useEffect(() => { const el = feedRef.current; if (el) el.scrollTop = el.scrollHeight; }, [visibleFeed.at(-1)?.eventId, feedOpen]);
  const highlighted = event?.start;
  const holderTeam = [...events].reverse().find(row => row.teamId && !['foul', 'substitution', 'card_yellow', 'card_red'].includes(row.type))?.teamId ?? data.match.home.teamId;
  const secondHalf = events.some(row => row.type === 'period_end');
  const attacksRight = (holderTeam === data.match.home.teamId) === !secondHalf;
  const placed = data.players.filter(player => positions[player.playerId]).map(player => ({ player, xy: positions[player.playerId] }));
  const teammates = placed.filter(row => row.player.teamId === holderTeam).filter(row => ball && Math.hypot(row.xy[0] - ball[0], row.xy[1] - ball[1]) > 4).sort((a, b) => Math.hypot(a.xy[0] - ball![0], a.xy[1] - ball![1]) - Math.hypot(b.xy[0] - ball![0], b.xy[1] - ball![1])).slice(0, 3);
  const defenderX = placed.filter(row => row.player.teamId !== holderTeam).map(row => row.xy[0]).sort((a, b) => attacksRight ? b - a : a - b);
  const offsideX = defenderX.length > 1 ? defenderX[1] : null;
  return <figure className={`pitch-shell ${feedOpen ? '' : 'feed-collapsed'}`}><div className="tactical-toolbar" role="group" aria-label="Tactical overlays">{LAYERS.map(([id, label]) => <button key={id} type="button" aria-pressed={layer === id} onClick={() => setLayer(current => current === id ? 'none' : id)}>{label}</button>)}</div><svg className="pitch" viewBox="-2.5 -2.5 110 73" role="img" aria-label={`Match positions at ${formatClock(clock)}`}><rect className="pitch-ground" x="-2.5" y="-2.5" width="110" height="73" /><g className="pitch-lines"><rect width="105" height="68" /><path d="M52.5 0V68" /><circle cx="52.5" cy="34" r="9.15" /><circle className="pitch-dot" cx="52.5" cy="34" r=".5" /><path d="M0 13.84H16.5V54.16H0M105 13.84H88.5V54.16H105M0 24.84H5.5V43.16H0M105 24.84H99.5V43.16H105" /><circle className="pitch-dot" cx="11" cy="34" r=".5" /><circle className="pitch-dot" cx="94" cy="34" r=".5" /><path d="M16.5 26.7A9.15 9.15 0 0 1 16.5 41.3M88.5 26.7A9.15 9.15 0 0 0 88.5 41.3" /></g><g className="pitch-goals"><rect x="-2" y="30.34" width="2" height="7.32" /><rect x="105" y="30.34" width="2" height="7.32" /></g>{ball && layer === 'pressure' && <circle className="pressure-zone" cx={ball[0]} cy={ball[1]} r="11" />}{ball && layer === 'passing' && <g className="passing-lanes">{teammates.map(row => <line key={row.player.playerId} x1={ball[0]} y1={ball[1]} x2={row.xy[0]} y2={row.xy[1]} />)}</g>}{layer === 'offside' && offsideX !== null && <line className="offside-line" x1={offsideX} x2={offsideX} y1="0" y2="68" />}{highlighted && <circle className="evidence-zone" cx={highlighted.x} cy={highlighted.y} r="5" />}{data.players.map(player => { const xy = positions[player.playerId]; if (!xy) return null; const side = teamById.get(player.teamId); const isTracked = tracked.includes(player.playerId); return <g key={player.playerId} className={`player-marker ${side} ${isTracked ? 'tracked' : ''}`} transform={`translate(${xy[0]} ${xy[1]})`} role="button" tabIndex={0} aria-label={`${player.name}, ${player.position}. ${isTracked ? 'Untrack' : 'Track player'}`} onMouseEnter={() => setHovered(player.playerId)} onMouseLeave={() => setHovered(null)} onFocus={() => setHovered(player.playerId)} onBlur={() => setHovered(null)} onClick={() => toggleTracking(player.playerId)} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') toggleTracking(player.playerId); }}><circle className="hit-area" r="2.6" />{isTracked && <circle className="selection-ring" r="1.9" />}<circle r="1.25" /><text y=".4" textAnchor="middle">{player.shirt}</text></g>; })}{ball && <circle className="ball-marker" cx={ball[0]} cy={ball[1]} r=".6" />}</svg>{activePlayer && <aside className="player-persona"><div className="persona-top"><span className={`persona-number ${teamById.get(activePlayer.teamId)}`}>{activePlayer.shirt}</span><div><strong>{activePlayer.name}</strong><span>{activePlayer.position}</span></div><em>{tracked.includes(activePlayer.playerId) ? 'Tracking' : 'Player profile'}</em></div><p>Project generated player profile. Match events shown here are limited to the current replay time.</p><dl><div><dt>Events</dt><dd>{events.filter(row => row.playerId === activePlayer.playerId).length}</dd></div></dl></aside>}<aside className={`match-feed-overlay ${feedOpen ? 'open' : 'closed'}`} aria-label="Match feed"><div className="feed-inner" ref={feedRef}><div className="feed-head"><span>Match feed</span><button className="feed-toggle" onClick={() => setFeedOpen(v => !v)}>{feedOpen ? 'Hide' : 'Show'}</button></div>{feedOpen && <div className="feed-list">{visibleFeed.length ? visibleFeed.map((row, index) => <article key={row.eventId} className={index === visibleFeed.length - 1 ? 'latest' : ''}><time>{row.minute}</time><div><strong>{row.title}</strong><p>{row.detail}</p></div></article>) : <article><time>{formatClock(clock)}</time><div><strong>Waiting for play</strong><p>Match events appear here as they become available.</p></div></article>}</div>}</div></aside><figcaption><span>Hover a player · Select up to 3</span></figcaption></figure>;
}
type Panel = 'insights' | 'stats' | 'players' | 'decisions' | 'ask' | 'timeline';
const PANEL_TABS: Panel[] = ['insights', 'stats', 'players', 'decisions', 'ask', 'timeline'];
const typeLabel = (type: string) => type.replace(/^card_/, '').replaceAll('_', ' ');
type GenerationUiState = { busy: boolean; result: GenerationResult | null; error: string };
const emptyGenerationState: GenerationUiState = { busy: false, result: null, error: '' };
const languageCode = (language: string) => language === 'Español' ? 'es' : language === 'Français' ? 'fr' : language === 'Deutsch' ? 'de' : 'en';
const REFEREE_QUESTIONS: Record<string, { id: string; en: string; es: string }[]> = {
  offside: [
    { id: 'q_off_why', en: 'Why was the flag raised?', es: '¿Por qué se levantó la bandera?' },
    { id: 'q_off_position', en: 'Was the attacker beyond the defender?', es: '¿Estaba el atacante más adelantado que el defensor?' },
    { id: 'q_off_review', en: 'Why was the goal ruled out after review?', es: '¿Por qué se anuló el gol después de la revisión?' },
  ],
  tactical_foul_yellow: [
    { id: 'q_yel_why', en: 'Why was this a yellow card?', es: '¿Por qué fue tarjeta amarilla?' },
    { id: 'q_yel_advantage', en: 'Why did the referee stop play?', es: '¿Por qué detuvo el juego el árbitro?' },
  ],
  penalty: [
    { id: 'q_pen_why', en: 'Why was a penalty awarded?', es: '¿Por qué se concedió un penalti?' },
    { id: 'q_pen_position', en: 'Did the foul occur inside the penalty area?', es: '¿Ocurrió la falta dentro del área penal?' },
  ],
};

function GenerationAnswer({ result, error }: { result: GenerationResult | null; error: string }) {
  if (!result && !error) return null;
  return <div aria-live="polite" aria-atomic="true">
    {error && <p role="alert">{error}</p>}
    {result && <div className="generation-answer">
      <p className="status">{result.generationEnabled === false ? 'AI answers paused · recorded facts only' : result.source === 'template' ? 'Facts only' : result.source === 'fake' ? 'Demo agent' : 'AI generated'} · {formatClock(result.cutoffMs)}</p>
      <p>{result.answer}</p>
      {!!result.ruleRefs?.length && <ul aria-label="Rule sources">{result.ruleRefs.map(rule => <li key={rule.ruleId}><a href={rule.sourceUrl} target="_blank" rel="noreferrer">IFAB Law {rule.lawNumber}: {rule.title}</a></li>)}</ul>}
      <small>{result.evidenceIds.length} evidence records · {result.workflowVersion}</small>
    </div>}
  </div>;
}

function ExplainAction({ event, state, onGenerate }: { event?: UiEvent; state: GenerationUiState; onGenerate: () => void }) {
  const progress = useProgress(state.busy);
  return <div>
    <Button className="primary" type="button" disabled={state.busy} onClick={onGenerate}>
      {state.busy ? progress : state.error ? 'Try again' : event ? 'Explain latest moment' : 'Summarize match so far'}
    </Button>
    <GenerationAnswer result={state.result} error={state.error} />
  </div>;
}

function AskRef({ event, language, state, onAsk }: { event: UiEvent; language: string; state: GenerationUiState; onAsk: (questionId: string) => void }) {
  const questions = REFEREE_QUESTIONS[event.decision?.decisionType ?? ''] ?? [];
  const [questionId, setQuestionId] = useState(questions[0]?.id ?? '');
  const selectedLanguage = languageCode(language);
  if (!questions.length) return <p>Questions are not available for this decision type yet.</p>;
  return <details className="ask-ref">
    <summary>Ask the Ref</summary>
    <form onSubmit={formEvent => { formEvent.preventDefault(); onAsk(questionId); }}>
      <label htmlFor={`question-${event.eventId}`}>Question</label>
      <select id={`question-${event.eventId}`} value={questionId} onChange={e => setQuestionId(e.target.value)}>
        {questions.map(question => <option key={question.id} value={question.id}>{selectedLanguage === 'es' ? question.es : question.en}</option>)}
      </select>
      <Button className="primary" type="submit" disabled={state.busy}>{state.busy ? 'Working…' : state.error ? 'Try again' : 'Get answer'}</Button>
    </form>
    <GenerationAnswer result={state.result} error={state.error} />
  </details>;
}

function SidePanel({ matchId, language, level, panel, setPanel, clock, events, selected, seek, stats }: { matchId: string; language: string; level: Level; panel: Panel; setPanel: (panel: Panel) => void; clock: number; events: UiEvent[]; selected: string | null; seek: (ms: number, event?: UiEvent) => void; stats: TeamStats[] }) {
  const data = useData(); const home = stats.find(s => s.teamId === data.match.home.teamId); const away = stats.find(s => s.teamId === data.match.away.teamId);
  const [explanation, setExplanation] = useState<GenerationUiState>(emptyGenerationState);
  const [refereeAnswers, setRefereeAnswers] = useState<Record<string, GenerationUiState>>({});
  const moments = events.filter(e => e.kind !== 'break' || e.type === 'period_end' || e.type === 'period_start').slice().reverse();
  const explainLatest = async () => {
    const event = moments.find(row => row.kind !== 'break');
    setExplanation({ busy: true, result: null, error: '' });
    try {
      const result = await requestExplanation(matchId, {
        cutoffMs: Math.floor(clock), eventId: event?.eventId,
        intent: event ? 'explain' : 'recap', language: languageCode(language), level,
      });
      setExplanation({ busy: false, result, error: '' });
    } catch (reason) {
      setExplanation({ busy: false, result: null, error: reason instanceof Error ? reason.message : 'Could not generate an explanation.' });
    }
  };
  const askReferee = async (event: UiEvent, questionId: string) => {
    setRefereeAnswers(current => ({ ...current, [event.eventId]: { busy: true, result: null, error: '' } }));
    try {
      const result = await requestRefereeAnswer(matchId, {
        cutoffMs: Math.floor(clock), eventId: event.eventId,
        questionId, language: languageCode(language), level,
      });
      setRefereeAnswers(current => ({ ...current, [event.eventId]: { busy: false, result, error: '' } }));
    } catch (reason) {
      setRefereeAnswers(current => ({ ...current, [event.eventId]: { busy: false, result: null, error: reason instanceof Error ? reason.message : 'Could not answer this question.' } }));
    }
  };
  const [dir, setDir] = useState(1); const touchX = useRef<number | null>(null);
  const askDecision = events.find(row => row.eventId === selected && row.decision) ?? moments.find(row => row.decision);
  const go = (next: Panel) => { setDir(PANEL_TABS.indexOf(next) >= PANEL_TABS.indexOf(panel) ? 1 : -1); setPanel(next); };
  const step = (delta: number) => { const next = PANEL_TABS[PANEL_TABS.indexOf(panel) + delta]; if (next) go(next); };
  const onTouchEnd = (event: React.TouchEvent) => { if (touchX.current === null) return; const dx = event.changedTouches[0].clientX - touchX.current; touchX.current = null; if (Math.abs(dx) > 50) step(dx < 0 ? 1 : -1); };
  const shots = (home?.shots ?? 0) + (away?.shots ?? 0); const homeShare = shots ? Math.round(((home?.shots ?? 0) / shots) * 100) : 50;
  return <section className="side-panel" aria-label="Match details"><nav className="panel-tabs">{PANEL_TABS.map(tab => <button key={tab} className={panel === tab ? 'active' : ''} onClick={() => go(tab)}>{tab[0].toUpperCase() + tab.slice(1)}</button>)}</nav><div key={panel} className="panel-content slide" style={{ '--dir': dir } as React.CSSProperties} onTouchStart={event => { touchX.current = event.touches[0].clientX; }} onTouchEnd={onTouchEnd}>
    {panel === 'insights' && <><div className="panel-heading"><div><span className="panel-kicker">Current reading</span><h2>What is shaping the match</h2></div><span>{formatClock(clock)}</span></div><div className="explanation"><p className="finding">{moments[0]?.title ?? 'The match is underway.'}</p><p>{moments[0]?.detail ?? 'Match evidence will appear here as the replay progresses.'}</p><ul className="signal-list"><li><span>Possession</span><strong>{home?.possessionPct ?? '—'}% {data.match.home.name}</strong></li><li><span>Shots</span><strong>{home?.shots ?? 0} – {away?.shots ?? 0}</strong></li>{level !== 'beginner' && <li><span>On target</span><strong>{home?.shotsOnTarget ?? 0} – {away?.shotsOnTarget ?? 0}</strong></li>}<li><span>Pass completion</span><strong>{home?.passCompletionPct ?? '—'}% – {away?.passCompletionPct ?? '—'}%</strong></li></ul>{level === 'pro' && <div className="momentum"><span className="estimate-label">Shot share {homeShare}% – {100 - homeShare}%</span><div className="momentum-track"><i className="midpoint" /><i className="momentum-value" style={{ left: `${homeShare}%` }} /></div></div>}{level === 'beginner' && <p className="insight-detail">Tip: shots on target force the goalkeeper to act; most goals come from them.</p>}<ExplainAction event={moments.find(event => event.kind !== 'break')} state={explanation} onGenerate={() => void explainLatest()} /></div></>}
    {panel === 'stats' && <><div className="panel-heading"><h2>Match pulse</h2><span>Through {formatClock(clock)}</span></div><div className="stat-strip"><div><span>Possession</span><strong>{home?.possessionPct ?? '—'}<span>%</span></strong></div><div><span>Shots</span><strong>{home?.shots ?? 0} – {away?.shots ?? 0}</strong></div><div><span>On target</span><strong>{home?.shotsOnTarget ?? 0} – {away?.shotsOnTarget ?? 0}</strong></div></div><div className="pressure"><div><span>Passes completed</span><strong>{home?.passesCompleted ?? 0} – {away?.passesCompleted ?? 0}</strong></div><p>Statistics are calculated from generated events available by {formatClock(clock)}.</p></div></>}
    {panel === 'players' && <><div className="panel-heading"><h2>Players in focus</h2><span>{data.players.length} starters</span></div><div className="player-list">{data.players.map(player => <article key={player.playerId}><TeamBadge team={player.teamId === data.match.home.teamId ? data.match.home : data.match.away} /><div><h3>{player.name}</h3><p>{player.position} · #{player.shirt}</p></div><strong>{events.filter(e => e.playerId === player.playerId).length}</strong></article>)}</div></>}
    {panel === 'decisions' && <><div className="panel-heading"><h2>Recorded decisions</h2><span>{formatClock(clock)}</span></div>{moments.filter(e => e.decision).length ? moments.filter(e => e.decision).map(e => <article className="decision" key={e.eventId}><span className="status">{e.decision?.recordedRuling.replaceAll('_', ' ')}</span><h3>{e.title}</h3><p>{e.detail}</p><small>{e.decision?.ruleIds?.join(', ')}</small><AskRef event={e} language={language} state={refereeAnswers[e.eventId] ?? emptyGenerationState} onAsk={questionId => void askReferee(e, questionId)} /></article>) : <div className="empty-panel"><span>Decisions</span><h2>No decision visible yet</h2><p>Recorded rulings appear when their evidence cutoff is reached.</p></div>}</>}
    {panel === 'ask' && <AskPanel matchId={matchId} language={languageCode(language)} level={level} clockMs={clock}
      decision={askDecision ? { eventId: askDecision.eventId, title: askDecision.title, minute: askDecision.minute } : null}
      suggestions={(REFEREE_QUESTIONS[askDecision?.decision?.decisionType ?? ''] ?? []).map(question => ({ id: question.id, text: languageCode(language) === 'es' ? question.es : question.en }))}
      onShowDecision={eventId => { const target = events.find(row => row.eventId === eventId); if (target) { seek(target.matchMs, target); go('decisions'); } }} />}
    {panel === 'timeline' && <section className="timeline-panel"><div className="panel-heading"><h2>Match timeline</h2><span>{moments.length} moments</span></div><ol className="v-timeline"><li className="v-now"><span className="v-dot" /><span>Now · {formatClock(clock)}</span></li>{moments.map(e => <li key={e.eventId} className={`v-item ${e.kind}`}><span className="v-dot" /><button className={`v-card ${selected === e.eventId ? 'selected' : ''}`} onClick={() => seek(e.matchMs, e)}><span className="v-meta"><time>{e.minute}</time><em>{typeLabel(e.type)}</em></span><strong>{e.title}</strong><span className="v-detail">{e.detail}</span><span className="clip-placeholder"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="10" /><path d="M10 8.5v7l6-3.5z" fill="currentColor" stroke="none" /></svg>Video clip · coming soon</span></button></li>)}</ol></section>}
  </div></section>;
}
createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>);
