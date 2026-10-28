import { StrictMode, createContext, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { requestExplanation, requestRefereeAnswer, type GenerationResult } from './api/client';
import { AskPanel } from './AskPanel';
import { useProgress } from './useProgress';
import './style.css';
import { sampleTracking, type Frame, type TrackingSample } from './tracking';
import { EventIcon, ReplayBanner, type Clip } from './Clip';

type Team = { teamId: string; name: string; shortName: string; colors: string[] };
type Match = { matchId: string; label: string; kickoffDate: string; durationMs: number; home: Team; away: Team };
type Player = { playerId: string; teamId: string; name: string; shirt: number; position: string; starter: boolean };
/** "r_law11_offside" -> "Law 11"; internal rule ids are never shown to viewers. */
const lawLabels = (ruleIds?: string[]) => [...new Set((ruleIds ?? []).map(id => id.match(/^r_law(\d+)_/)?.[1]).filter(Boolean))].map(n => `Law ${n}`).join(', ');

type Event = { eventId: string; matchMs: number; availableMs: number; type: string; teamId?: string; playerId?: string; outcome?: string; start?: { x: number; y: number }; decision?: { decisionType: string; recordedRuling: string; reversesEventId?: string; ruleIds?: string[] } };
type TeamStats = { teamId: string; shots: number; shotsOnTarget: number; passes: number; passesCompleted: number; passCompletionPct: number | null; possessionPct: number; goals: number; yellowCards: number; redCards: number };
type DatasetView = { match: Match; players: Player[]; events: Event[]; stats: TeamStats[]; cutoffMs: number; error: string | null };
type UiEvent = Event & { title: string; detail: string; minute: string; kind: 'goal' | 'card' | 'break' | 'review' };
const API = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '');
const url = (path: string) => `${API}${path}`;
const MATCH_END_MS = 96 * 60_000;
const emptyView: DatasetView = { match: { matchId: '', label: '', kickoffDate: '', durationMs: 0, home: { teamId: '', name: '', shortName: '', colors: [] }, away: { teamId: '', name: '', shortName: '', colors: [] } }, players: [], events: [], stats: [], cutoffMs: 0, error: null };
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
const prefsKey = 'next-whistle-viewer-v2';
const defaults: Prefs = { perspective: 'Neutral', level: 'beginner', language: 'English', theme: 'light' };
function readPrefs(): Prefs { try { return { ...defaults, ...JSON.parse(localStorage.getItem(prefsKey) ?? '{}') }; } catch { return defaults; } }
try { document.documentElement.dataset.theme = readPrefs().theme; } catch { /* theme falls back to the stylesheet default */ }
function formatClock(ms: number) { const seconds = Math.floor(ms / 1000); return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`; }
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
    load().catch(error => { if (error.name !== 'AbortError') setData(current => ({ ...current, error: 'Could not load the match data. Please try again.' })); });
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
  const detail = event.decision ? `Recorded ruling: ${event.decision.recordedRuling.replaceAll('_', ' ')}${lawLabels(event.decision.ruleIds) ? ` · ${lawLabels(event.decision.ruleIds)}` : ''}.` : playerName ? `${playerName}${event.outcome ? ` · ${event.outcome.replaceAll('_', ' ')}` : ''}.` : `${event.type.replaceAll('_', ' ')} recorded.`;
  const kind = event.type.includes('goal') ? 'goal' : event.type.includes('card') ? 'card' : event.type.startsWith('period') ? 'break' : event.decision ? 'review' : 'break';
  return { ...event, title, detail, minute, kind };
}
function App() {
  const [prefs, setPrefs] = useState(readPrefs);
  useEffect(() => { document.documentElement.dataset.theme = prefs.theme; localStorage.setItem(prefsKey, JSON.stringify(prefs)); }, [prefs]);
  const matchId = window.location.pathname.match(/^\/match\/([^/]+)/)?.[1];
  return matchId ? <ReplayApp matchId={matchId} prefs={prefs} setPrefs={setPrefs} /> : <MatchPicker prefs={prefs} setPrefs={setPrefs} />;
}
function Waking({ what }: { what: string }) {
  const [slow, setSlow] = useState(false);
  useEffect(() => { const timer = window.setTimeout(() => setSlow(true), 3000); return () => window.clearTimeout(timer); }, []);
  return <div className="waking" role="status"><span className="waking-dot" aria-hidden="true" /><div><strong>Loading {what}…</strong>{slow && <p>Still loading. This can take a few seconds.</p>}</div></div>;
}
type ScheduleItem = { slot: number; kind: 'live' | 'replay' | 'result'; startsAtMs: number; matchId?: string; home: Team; away: Team; liveMatchMs?: number; score?: { home: number; away: number }; keyMoments?: { minute: number; kind: string; team: string }[] };
const MOMENTS: Record<string, string> = { goal: 'Goal', penalty: 'Penalty', offside: 'Offside', tactical_foul_yellow: 'Yellow card', comeback: 'Comeback', lead_change: 'Lead change', activity_surge: 'Pressure spell' };
const dayLabel = (ms: number) => new Date(ms).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
const timeLabel = (ms: number) => new Date(ms).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
function Side({ team, away }: { team: Team; away?: boolean }) { return <div className={`lp-team ${away ? 'away' : ''}`}>{away ? <><strong>{team.name}</strong><TeamBadge team={team} /></> : <><TeamBadge team={team} /><strong>{team.name}</strong></>}</div>; }
function MatchPicker({ prefs, setPrefs }: { prefs: Prefs; setPrefs: (p: Prefs) => void }) {
  const [items, setItems] = useState<ScheduleItem[]>([]); const [error, setError] = useState(''); const [loadedAt, setLoadedAt] = useState(0); const [now, setNow] = useState(Date.now());
  const load = () => fetch(url('/api/schedule'), { cache: 'no-store' }).then(response => { if (!response.ok) throw new Error(); return response.json(); }).then(result => { setItems(result.items); setLoadedAt(Date.now()); setError(''); }).catch(() => setError('The match list could not be loaded. Please try again in a moment.'));
  useEffect(() => { void load(); const refresh = window.setInterval(() => void load(), 60_000); const tick = window.setInterval(() => setNow(Date.now()), 15_000); return () => { window.clearInterval(refresh); window.clearInterval(tick); }; }, []);
  return <div className="picker lp">
    <svg className="lp-lines" viewBox="0 0 1280 1000" preserveAspectRatio="none" aria-hidden="true" focusable="false"><g fill="none" stroke="currentColor" strokeWidth="3"><rect x="60" y="120" width="1160" height="800" rx="6" /><path d="M640 120v800" /><circle cx="640" cy="520" r="110" /><path d="M60 330h190v380H60M1220 330h-190v380h190" /></g></svg>
    <div className="lp-wrap">
      <header className="lp-top"><a className="brand" href="/"><Mark /><span>Next Whistle</span></a><small>Fictional clubs · AI-explained decisions</small></header>
      <section className="lp-hero">
        <div className="lp-copy">
          <h1>Watch the match. <span>Understand the whistle.</span></h1>
          <p className="lp-lede">Replay a full match, tap any moment and get an explanation checked against the recorded evidence. Ask the referee why a call was made.</p>
          <ol className="lp-steps"><li><b>1</b>Set up your view</li><li><b>2</b>Pick a match and press play</li><li><b>3</b>Tap a moment, or ask the Ref</li></ol>
        </div>
        <form className="lp-setup" onSubmit={e => e.preventDefault()}>
          <div><strong>Set up your view</strong><p>Saved on this device. Change it any time.</p></div>
          <fieldset><legend>Viewer level</legend><div className="lp-seg">{(['beginner', 'intermediate', 'pro'] as Level[]).map(level => <button type="button" key={level} aria-pressed={prefs.level === level} onClick={() => setPrefs({ ...prefs, level })}>{level[0].toUpperCase() + level.slice(1)}</button>)}</div><p className="hint">{LEVEL_HINTS[prefs.level]}</p></fieldset>
          <label>Language<select value={prefs.language} onChange={e => setPrefs({ ...prefs, language: e.target.value })}>{LANGUAGES.map(language => <option key={language}>{language}</option>)}</select></label>
          <label>Perspective<select value={prefs.perspective} onChange={e => setPrefs({ ...prefs, perspective: e.target.value })}>{PERSPECTIVES.map(perspective => <option key={perspective}>{perspective}</option>)}</select></label>
        </form>
      </section>
      <div className="lp-head"><h2>Matches</h2><span>Scroll for older matches</span></div>
      {error && !items.length ? <div className="empty-panel"><p role="alert">{error}</p><Button onClick={() => void load()}>Retry</Button></div> : !items.length ? <Waking what="matches" /> : <div className="lp-shell">
        <div className="lp-timeline" tabIndex={0} role="region" aria-label="Match timeline, newest first">
          {items.map(item => {
            const live = item.kind === 'live'; const result = item.kind === 'result';
            const minute = Math.floor(((item.liveMatchMs ?? 0) + Math.max(0, now - loadedAt)) / 60_000) + 1;
            const body = <div className="lp-body">
              <div className="lp-tags"><span className={`lp-pill ${live ? 'live' : ''}`}>{live && <i />}{live ? 'Live now' : result ? 'Result' : 'Replay'}</span><span className="lp-meta">{live ? `${minute}'` : result ? 'Final score' : 'Full match'}</span></div>
              <div className="lp-teams"><Side team={item.home} />{result && item.score ? <span className="lp-score" aria-label={`${item.home.name} ${item.score.home}, ${item.away.name} ${item.score.away}`}>{item.score.home}–{item.score.away}</span> : <span className="lp-vs">VS</span>}<Side team={item.away} away /></div>
              {result && !!item.keyMoments?.length && <ul className="lp-moments" aria-label="Key moments">{item.keyMoments.map((moment, index) => <li key={index}>{moment.minute}' {MOMENTS[moment.kind] ?? moment.kind}</li>)}</ul>}
            </div>;
            return <div className="lp-entry" key={item.slot}>
              <div className="lp-when">{live ? <>Now<small>Live</small></> : <>{dayLabel(item.startsAtMs)}<small>{timeLabel(item.startsAtMs)}</small></>}</div>
              <div className="lp-rail"><span className={`lp-node ${item.kind}`} /></div>
              {item.matchId ? <a className={`lp-card ${live ? 'live' : ''}`} href={`/match/${item.matchId}`}>{body}<div className={`lp-action ${live ? 'live' : 'replay'}`}>{live ? 'Join live →' : 'Watch the replay →'}<small>{live ? 'Pick up where it is now' : 'From kick-off'}</small></div></a> : <div className="lp-card result">{body}</div>}
            </div>;
          })}
        </div>
        <div className="lp-fade" />
      </div>}
      <p className="lp-foot">All clubs, players and matches are fictional. Football rules are paraphrased from the IFAB Laws of the Game.</p>
    </div>
  </div>;
}
function ReplayApp({ matchId, prefs, setPrefs }: { matchId: string; prefs: Prefs; setPrefs: (p: Prefs) => void }) {
  const [catalog, setCatalog] = useState<Match[]>([]); const [liveEdge, setLiveEdge] = useState(0); const [clock, setClock] = useState(0); const [playing, setPlaying] = useState(true); const [selected, setSelected] = useState<string | null>(null); const [panel, setPanel] = useState<Panel>('insights'); const [tracked, setTracked] = useState<string[]>([]); const [hovered, setHovered] = useState<string | null>(null); const [trackingFrames, setTrackingFrames] = useState<Frame[]>([]); const [trackingOrder, setTrackingOrder] = useState<string[]>([]);
  const [liveClock, setLiveClock] = useState<{ offset: number; serverTimeMs: number; matchMs: number; matchId: string } | null>(null); const started = useRef(false);
  const clockRef = useRef(clock); clockRef.current = clock;
  const trackingCursor = useRef(-1);
  const [replay, setReplay] = useState<{ clip: Clip; title: string; from: number; to: number; restore: number; running: boolean } | null>(null);
  // A replay moves the clock itself, from the start of the clip to its end, so the pitch, feed, score and stats replay together.
  useEffect(() => {
    if (!replay?.running) return;
    let last = performance.now();
    const timer = window.setInterval(() => { const now = performance.now(); const step = now - last; last = now; setClock(current => Math.min(replay.to, current + step)); }, 40);
    return () => window.clearInterval(timer);
  }, [replay?.clip.clipId, replay?.running, replay?.to]);
  useEffect(() => { if (replay?.running && clock >= replay.to) setReplay(current => current && { ...current, running: false }); }, [clock, replay]);
  useEffect(() => { let retry: number; const loadCatalog = () => fetch(url('/api/matches')).then(r => { if (!r.ok) throw new Error(); return r.json(); }).then(v => setCatalog(v.matches ?? [])).catch(() => { retry = window.setTimeout(loadCatalog, 4000); }); loadCatalog(); fetch(url('/api/live-state'), { cache: 'no-store' }).then(r => { if (!r.ok) throw new Error(); return r.json(); }).then(s => { setLiveClock({ offset: s.serverTimeMs - Date.now(), serverTimeMs: s.serverTimeMs, matchMs: s.matchMs, matchId: s.matchId }); }).catch(() => undefined); return () => window.clearTimeout(retry); }, []);
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
          setTrackingFrames(current => [...current, ...rows].slice(-16));
        }
      } catch { /* Tracking can recover on the next poll; match events and stats remain available. */ }
      finally { busy = false; }
    };
    fetch(url(`/api/matches/${match.matchId}/tracking/manifest`)).then(r => { if (!r.ok) throw new Error(); return r.json(); }).then(manifest => {
      if (!active) return;
      setTrackingOrder(manifest.player_order ?? []);
      void run(manifest);
      timer = window.setInterval(() => void run(manifest), 250);
    }).catch(() => undefined);
    return () => { active = false; window.clearInterval(timer); };
  }, [match?.matchId]);
  const dataCurrent = data.cutoffMs <= clock;
  const events = useMemo(() => (dataCurrent ? data.events : []).map(event => eventPresentation(event, data)).filter(event => event.availableMs <= clock), [data.events, data.players, data.match, dataCurrent, clock]);
  // The timeline list stays where the viewer was while a replay rewinds the clock for the pitch, feed and score.
  const listClock = replay ? Math.max(clock, replay.restore) : clock;
  const panelEvents = useMemo(() => replay ? data.events.map(event => eventPresentation(event, data)).filter(event => event.availableMs <= listClock) : events, [events, replay?.restore, replay === null, data.events, listClock]);
  const stats = dataCurrent ? data.stats : []; const homeStats = stats.find(t => t.teamId === data.match.home.teamId); const awayStats = stats.find(t => t.teamId === data.match.away.teamId);
  const reversed = new Set(events.map(e => e.decision?.reversesEventId).filter(Boolean));
  const goals = (teamId: string) => events.filter(e => e.type === 'goal' && e.teamId === teamId && !reversed.has(e.eventId)).length;
  const selectedEvent = events.find(e => e.eventId === selected) ?? null;
  const displayedPlayers = data.players.filter(p => p.starter);
  const trackingSample = sampleTracking(trackingFrames.filter(frame => frame[0] <= clock), trackingOrder, clock);
  const gridRef = useRef<HTMLDivElement>(null); const dragging = useRef(false);
  const [panelWidth, setPanelWidth] = useState(320); const [panelOpen, setPanelOpen] = useState(true);
  const isLive = !!liveClock && !!match && liveClock.matchId === match.matchId;
  const liveAt = () => liveClock && match ? Math.min(liveClock.matchMs + Math.max(0, Date.now() + liveClock.offset - liveClock.serverTimeMs), match.durationMs) : 0;
  useEffect(() => { if (started.current || !liveClock || !match) return; started.current = true; const secondHalfStart = 46 * 60_000; const edge = isLive ? liveAt() : secondHalfStart + Math.floor(Math.random() * Math.max(1, Math.min(35 * 60_000, match.durationMs - secondHalfStart - 1))); setLiveEdge(isLive ? edge : match.durationMs); setClock(edge); }, [liveClock, match, isLive]);
  useEffect(() => {
    if (!liveClock || !match) return;
    let last = Date.now();
    const timer = window.setInterval(() => {
      const step = Date.now() - last; last = Date.now();
      if (isLive) { const edge = liveAt(); setLiveEdge(edge); setClock(current => playing ? edge : Math.min(current, edge)); }
      else { setLiveEdge(match.durationMs); setClock(current => playing ? Math.min(match.durationMs, current + step) : current); }
    }, 100);
    return () => window.clearInterval(timer);
  }, [liveClock, match?.matchId, match?.durationMs, isLive, playing]);
  const seek = (ms: number, event?: UiEvent) => { const t = Math.max(0, Math.min(liveEdge, event ? Math.max(ms, event.availableMs) : ms)); setReplay(null); setPlaying(false); setClock(t); setSelected(event?.eventId ?? null); };
  const startReplay = (clip: Clip, event: UiEvent) => {
    const from = clip.fromMs; const to = Math.min(clip.toMs, liveEdge);
    setPlaying(false); setSelected(event.eventId); setClock(from);
    setReplay(current => ({ clip, title: `${typeLabel(event.type)} · ${event.minute}`, from, to, restore: current?.restore ?? clockRef.current, running: true }));
  };
  const toggleReplay = () => { if (!replay) return; if (clock >= replay.to) { setClock(replay.from); setReplay({ ...replay, running: true }); } else setReplay({ ...replay, running: !replay.running }); };
  const goToLive = () => { if (liveClock) window.location.href = `/match/${liveClock.matchId}`; };
  const goLive = () => { setReplay(null); setClock(liveEdge); setPlaying(true); setSelected(null); };
  const resizeTo = (clientX: number) => { const rect = gridRef.current?.getBoundingClientRect(); if (!rect) return; setPanelOpen(true); setPanelWidth(Math.round(Math.max(220, Math.min(560, rect.width * .55, rect.right - clientX - 8)))); };
  const resizeKey = (event: React.KeyboardEvent) => { if (event.key === 'ArrowLeft') { event.preventDefault(); setPanelOpen(true); setPanelWidth(width => Math.min(560, width + 16)); } else if (event.key === 'ArrowRight') { event.preventDefault(); setPanelWidth(width => Math.max(220, width - 16)); } else if (event.key === 'Home') { event.preventDefault(); setPanelOpen(true); setPanelWidth(260); } else if (event.key === 'Enter') { event.preventDefault(); setPanelOpen(value => !value); } };
  const toggleTracking = (id: string) => setTracked(current => current.includes(id) ? current.filter(v => v !== id) : current.length < 3 ? [...current, id] : current);
  const activePlayer = displayedPlayers.find(p => p.playerId === (hovered ?? tracked.at(-1)));
  const metricCards = homeStats && awayStats;
  if (!match) return <div className="picker"><main>{catalog.length ? <><h1>Match not found</h1><a href="/">Back to matches</a></> : <Waking what="the match" />}</main></div>;
  if (data.error) return <div className="picker"><main><h1>{match.label}</h1><p role="alert">{data.error}</p><Button onClick={() => window.location.reload()}>Retry</Button></main></div>;
  return <Context.Provider value={data}><div className="app-shell">
    <header className="live-header"><a className="brand" href="/" aria-label="Next Whistle match picker"><Mark /><span>Next Whistle</span></a><div className="compact-score"><div><TeamBadge team={match.home} /><span>{match.home.name}</span></div><strong aria-label={`${match.home.name} ${goals(match.home.teamId)}, ${match.away.name} ${goals(match.away.teamId)}`}>{goals(match.home.teamId)}<i>–</i>{goals(match.away.teamId)}</strong><div><span>{match.away.name}</span><TeamBadge team={match.away} /></div></div><div className="header-live"><span className="header-date">{new Date().toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })}</span><Personalize prefs={prefs} onChange={setPrefs} /><span className="header-clock"><strong>{formatClock(clock)}</strong><small>{clock < 45 * 60_000 ? 'First half' : 'Second half'}</small></span><button className={`live-state ${isLive && playing ? 'is-live' : ''}`} onClick={isLive ? () => { setClock(liveEdge); setPlaying(true); } : goToLive}><span />{isLive && playing && Math.abs(clock - liveEdge) < 2500 ? 'Live' : 'Go live'}</button></div><section className="match-insights" aria-label="Generated match statistics"><div className="insight-grid">{metricCards ? <><Metric label="Possession" value={`${homeStats.possessionPct}% – ${awayStats.possessionPct}%`} detail={`${match.home.shortName} — ${match.away.shortName}`} /><Metric label="Shots" value={`${homeStats.shots} – ${awayStats.shots}`} detail={`Through ${formatClock(data.cutoffMs)}`} /><Metric label="On target" value={`${homeStats.shotsOnTarget} – ${awayStats.shotsOnTarget}`} detail="Attempts on target" /><Metric label="Pass completion" value={`${homeStats.passCompletionPct ?? '—'}% – ${awayStats.passCompletionPct ?? '—'}%`} detail="Completed passes / attempts" /></> : <div className="metric-card">Loading statistics for {formatClock(clock)}…</div>}</div></section></header>
    <main className="workspace"><h1 className="sr-only">{match.label} live match</h1>
      <div className={`replay-grid ${panelOpen ? '' : 'panel-collapsed'}`} ref={gridRef} style={{ '--panel-width': `${panelOpen ? panelWidth : 0}px` } as React.CSSProperties}><section className="replay-stage" aria-label="Live match replay"><Pitch clock={clock} event={selectedEvent} tracking={trackingSample} tracked={tracked} activePlayer={activePlayer} setHovered={setHovered} toggleTracking={toggleTracking} events={events} />{replay && <ReplayBanner title={replay.title} from={replay.from} to={replay.to} clock={clock} running={replay.running} onToggle={toggleReplay} onExit={goLive} />}<div className="playback" role="group" aria-label="Playback controls"><button className="play-toggle" onClick={() => { if (replay) toggleReplay(); else setPlaying(value => !value); }} aria-label={(replay ? replay.running : playing) ? 'Pause' : 'Play'}>{(replay ? replay.running : playing) ? <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="6" y="5" width="4" height="14" rx="1" /><rect x="14" y="5" width="4" height="14" rx="1" /></svg> : <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5.5v13a1 1 0 0 0 1.5.86l10.5-6.5a1 1 0 0 0 0-1.72L9.5 4.64A1 1 0 0 0 8 5.5Z" /></svg>}</button><span className="playback-time">{formatClock(clock)}</span><label className="scrubber"><span className="sr-only">Match time</span><input type="range" min="0" max={liveEdge} step="1000" value={clock} onChange={event => seek(Number(event.target.value))} /></label><button type="button" className={`playback-live ${isLive && playing ? 'is-live' : ''}`} onClick={isLive ? goLive : goToLive}><i />{isLive && playing ? 'Live' : 'Go live'}</button></div></section><div className="panel-divider"><div className="panel-resizer" role="separator" aria-orientation="vertical" aria-label="Resize match details panel. Arrow keys adjust, Home resets, Enter collapses." aria-valuemin={220} aria-valuemax={560} aria-valuenow={panelOpen ? panelWidth : 0} tabIndex={0} onPointerDown={event => { dragging.current = true; event.currentTarget.setPointerCapture(event.pointerId); }} onPointerMove={event => { if (dragging.current) resizeTo(event.clientX); }} onPointerUp={() => { dragging.current = false; }} onPointerCancel={() => { dragging.current = false; }} onDoubleClick={() => setPanelWidth(260)} onKeyDown={resizeKey}><span aria-hidden="true" /></div><button type="button" className="panel-collapse" aria-label={panelOpen ? 'Collapse match details panel' : 'Expand match details panel'} aria-expanded={panelOpen} onClick={() => setPanelOpen(value => !value)}>{panelOpen ? '›' : '‹'}</button></div><SidePanel matchId={matchId} language={prefs.language} level={prefs.level} panel={panel} setPanel={setPanel} clock={listClock} events={panelEvents} selected={selected} seek={seek} onReplay={startReplay} replayId={replay?.clip.clipId} stats={stats} /></div>
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
const TAB_ICONS: Record<Panel, React.ReactNode> = {
  insights: <><path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-4 10.5c.7.7 1 1.4 1 2.5h6c0-1.1.3-1.8 1-2.5A6 6 0 0 0 12 3z" /></>,
  stats: <path d="M5 20V11M12 20V4M19 20v-6" />,
  players: <><circle cx="9" cy="8" r="3" /><path d="M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6M16 5.5a3 3 0 0 1 0 5M18 14c2 .8 3 2.6 3 5" /></>,
  decisions: <path d="M5 21V4M5 4h12l-2.5 4L17 12H5" />,
  ask: <><path d="M4 5h16v11h-9l-5 4v-4H4z" /><path d="M10 9.5c0-1.7 3.5-1.7 3.5 0 0 1.5-1.8 1.4-1.8 3M11.7 14.5h.01" /></>,
  timeline: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
};
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

function SidePanel({ matchId, language, level, panel, setPanel, clock, events, selected, seek, onReplay, replayId, stats }: { matchId: string; language: string; level: Level; panel: Panel; setPanel: (panel: Panel) => void; clock: number; events: UiEvent[]; selected: string | null; seek: (ms: number, event?: UiEvent) => void; onReplay: (clip: Clip, event: UiEvent) => void; replayId?: string; stats: TeamStats[] }) {
  const data = useData(); const home = stats.find(s => s.teamId === data.match.home.teamId); const away = stats.find(s => s.teamId === data.match.away.teamId);
  const [explanation, setExplanation] = useState<GenerationUiState>(emptyGenerationState);
  const [refereeAnswers, setRefereeAnswers] = useState<Record<string, GenerationUiState>>({});
  const moments = events.filter(e => e.kind !== 'break' || e.type === 'period_end' || e.type === 'period_start').slice().reverse();
  const [clips, setClips] = useState<Clip[]>([]);
  const clockNow = useRef(clock); clockNow.current = clock;
  useEffect(() => {
    if (panel !== 'timeline') return;
    let live = true;
    const load = () => fetch(url(`/api/matches/${matchId}/clips?cutoffMs=${Math.floor(clockNow.current)}`)).then(r => r.ok ? r.json() : null).then(v => { if (live && v) setClips(v.clips ?? []); }).catch(() => undefined);
    load(); const timer = window.setInterval(load, 5000);
    return () => { live = false; window.clearInterval(timer); };
  }, [panel, matchId]);
  // each clip is shown on the last of its events that appears in the timeline
  const clipFor = new Map<string, Clip>();
  for (const clip of clips) { const last = [...clip.focusEventIds].reverse().find(id => moments.some(e => e.eventId === id)); if (last) clipFor.set(last, clip); }
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
  return <section className="side-panel" aria-label="Match details"><nav className="panel-tabs">{PANEL_TABS.map(tab => <button key={tab} className={panel === tab ? 'active' : ''} aria-pressed={panel === tab} onClick={() => go(tab)}><svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">{TAB_ICONS[tab]}</svg>{tab[0].toUpperCase() + tab.slice(1)}</button>)}</nav><div key={panel} className="panel-content slide" style={{ '--dir': dir } as React.CSSProperties} onTouchStart={event => { touchX.current = event.touches[0].clientX; }} onTouchEnd={onTouchEnd}>
    {panel === 'insights' && <><div className="panel-heading"><div><span className="panel-kicker">Current reading</span><h2>What is shaping the match</h2></div><span>{formatClock(clock)}</span></div><div className="explanation"><p className="finding">{moments[0]?.title ?? 'The match is underway.'}</p><p>{moments[0]?.detail ?? 'Match evidence will appear here as the replay progresses.'}</p><ul className="signal-list"><li><span>Possession</span><strong>{home?.possessionPct ?? '—'}% {data.match.home.name}</strong></li><li><span>Shots</span><strong>{home?.shots ?? 0} – {away?.shots ?? 0}</strong></li>{level !== 'beginner' && <li><span>On target</span><strong>{home?.shotsOnTarget ?? 0} – {away?.shotsOnTarget ?? 0}</strong></li>}<li><span>Pass completion</span><strong>{home?.passCompletionPct ?? '—'}% – {away?.passCompletionPct ?? '—'}%</strong></li></ul>{level === 'pro' && <div className="momentum"><span className="estimate-label">Shot share {homeShare}% – {100 - homeShare}%</span><div className="momentum-track"><i className="midpoint" /><i className="momentum-value" style={{ left: `${homeShare}%` }} /></div></div>}{level === 'beginner' && <p className="insight-detail">Tip: shots on target force the goalkeeper to act; most goals come from them.</p>}<ExplainAction event={moments.find(event => event.kind !== 'break')} state={explanation} onGenerate={() => void explainLatest()} /></div></>}
    {panel === 'stats' && <><div className="panel-heading"><h2>Match pulse</h2><span>Through {formatClock(clock)}</span></div><div className="stat-strip"><div><span>Possession</span><strong>{home?.possessionPct ?? '—'}<span>%</span></strong></div><div><span>Shots</span><strong>{home?.shots ?? 0} – {away?.shots ?? 0}</strong></div><div><span>On target</span><strong>{home?.shotsOnTarget ?? 0} – {away?.shotsOnTarget ?? 0}</strong></div></div><div className="pressure"><div><span>Passes completed</span><strong>{home?.passesCompleted ?? 0} – {away?.passesCompleted ?? 0}</strong></div><p>Statistics are calculated from generated events available by {formatClock(clock)}.</p></div></>}
    {panel === 'players' && <><div className="panel-heading"><h2>Players in focus</h2><span>{data.players.length} starters</span></div><div className="player-list">{data.players.map(player => <article key={player.playerId}><TeamBadge team={player.teamId === data.match.home.teamId ? data.match.home : data.match.away} /><div><h3>{player.name}</h3><p>{player.position} · #{player.shirt}</p></div><strong>{events.filter(e => e.playerId === player.playerId).length}</strong></article>)}</div></>}
    {panel === 'decisions' && <><div className="panel-heading"><h2>Recorded decisions</h2><span>{formatClock(clock)}</span></div>{moments.filter(e => e.decision).length ? moments.filter(e => e.decision).map(e => <article className="decision" key={e.eventId}><span className="status">{e.decision?.recordedRuling.replaceAll('_', ' ')}</span><h3>{e.title}</h3><p>{e.detail}</p><small>{lawLabels(e.decision?.ruleIds)}</small><AskRef event={e} language={language} state={refereeAnswers[e.eventId] ?? emptyGenerationState} onAsk={questionId => void askReferee(e, questionId)} /></article>) : <div className="empty-panel"><span>Decisions</span><h2>No decision visible yet</h2><p>Recorded rulings appear when their evidence cutoff is reached.</p></div>}</>}
    {panel === 'ask' && <AskPanel matchId={matchId} language={languageCode(language)} level={level} clockMs={clock}
      decision={askDecision ? { eventId: askDecision.eventId, title: askDecision.title, minute: askDecision.minute } : null}
      suggestions={(REFEREE_QUESTIONS[askDecision?.decision?.decisionType ?? ''] ?? []).map(question => ({ id: question.id, text: languageCode(language) === 'es' ? question.es : question.en }))}
      onShowDecision={eventId => { const target = events.find(row => row.eventId === eventId); if (target) { seek(target.matchMs, target); go('decisions'); } }} />}
    {panel === 'timeline' && <section className="timeline-panel"><div className="panel-heading"><h2>Match timeline</h2><span>{moments.length} moments</span></div><ol className="v-timeline"><li className="v-now"><span className="v-dot" /><span>Now · {formatClock(clock)}</span></li>{moments.map(e => <li key={e.eventId} className={`v-item ${e.kind}`}><span className="v-dot"><EventIcon type={e.type} /></span><button className={`v-card ${selected === e.eventId ? 'selected' : ''}`} onClick={() => seek(e.matchMs, e)}><span className="v-meta"><time>{e.minute}</time><em>{typeLabel(e.type)}</em></span><strong>{e.title}</strong><span className="v-detail">{e.detail}</span></button>{clipFor.get(e.eventId) && <button type="button" className={`clip-button ${replayId === clipFor.get(e.eventId)!.clipId ? 'active' : ''}`} onClick={() => onReplay(clipFor.get(e.eventId)!, e)}><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M5 3l8 5-8 5z" /></svg>Replay on pitch<small>{Math.round((clipFor.get(e.eventId)!.toMs - clipFor.get(e.eventId)!.fromMs) / 1000)} s</small></button>}</li>)}</ol></section>}
  </div></section>;
}
createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>);
