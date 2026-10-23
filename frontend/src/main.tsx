import { StrictMode, useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';

type Panel = 'moments' | 'stats' | 'players' | 'explain' | 'referee';
type TeamKey = 'rvr' | 'hrb' | 'ash';
type MatchEvent = {
  id: string;
  minute: string;
  timeMs: number;
  availableMs: number;
  team: TeamKey | null;
  type: 'goal' | 'card' | 'break' | 'review';
  title: string;
  detail: string;
  player?: string;
  x?: number;
  y?: number;
};
type MatchMetrics = {
  possessionHome: number;
  xgHome: number;
  xgAway: number;
  winHome: number;
  winDraw: number;
  winAway: number;
  momentum: number;
};
type PlayerProfile = {
  id: string;
  team: 'home' | 'away';
  markerIndex: number;
  number: number;
  name: string;
  position: string;
  archetype: string;
  description: string;
  distanceKm: number;
  topSpeed: number;
  touches: number;
};
type TrackingFrame = [number, number, number, number, number, number[], 'controlled' | 'flight' | 'dead'];
type TrackingSample = { ball: [number, number]; players: Record<string, [number, number]> };

const MATCH_END_MS = 96 * 60 * 1000;
const TRACKING_REVISION = '1.1.0-ball-control-1';
const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '');
const apiUrl = (path: string) => `${API_BASE_URL}${path}`;
const DEMO_SNAPSHOT_MS = 75 * 60 * 1000;
const SHARED_LIVE_EPOCH_MS = Date.UTC(2026, 9, 6, 16, 0, 0);
type LiveClock = { serverOffsetMs: number; epochMs: number; durationMs: number };
const defaultLiveClock: LiveClock = { serverOffsetMs: 0, epochMs: SHARED_LIVE_EPOCH_MS, durationMs: MATCH_END_MS };
const sharedMatchMs = (clock = defaultLiveClock, nowMs = Date.now()) => {
  const elapsedMs = nowMs + clock.serverOffsetMs - clock.epochMs;
  return ((elapsedMs % clock.durationMs) + clock.durationMs) % clock.durationMs;
};
const events: MatchEvent[] = [
  { id: 'e_m_rvr_hrb_00412', minute: "24'", timeMs: 1421000, availableMs: 1421000, team: 'rvr', type: 'goal', title: 'Riverside goal', detail: 'Mara Voss finishes Sofia Marin’s pass after a three-pass counter.', player: 'Mara Voss', x: 91, y: 39 },
  { id: 'e_m_rvr_hrb_00786', minute: "45'", timeMs: 2820000, availableMs: 2820000, team: null, type: 'break', title: 'Half-time', detail: 'Riverside lead by one at the interval.' },
  { id: 'e_m_rvr_hrb_01038', minute: "62'", timeMs: 3665000, availableMs: 3760000, team: 'hrb', type: 'review', title: 'Harbour goal ruled out', detail: 'The review finds Nia Cole 0.60 m beyond the second-last defender when the pass is played.', player: 'Nia Cole', x: 89, y: 33 },
  { id: 'e_m_rvr_hrb_01264', minute: "75'", timeMs: 4460000, availableMs: 4464000, team: 'hrb', type: 'goal', title: 'Harbour penalty scored', detail: 'Leila Okafor converts after a recorded foul inside the Riverside penalty area.', player: 'Leila Okafor', x: 11, y: 34 },
  { id: 'e_m_rvr_hrb_01328', minute: "79'", timeMs: 4728000, availableMs: 4728000, team: 'rvr', type: 'card', title: 'Riverside caution', detail: 'This event remains hidden until the replay reaches 78:48.', player: 'Tomas Vale', x: 52, y: 46 },
];
const snapshots = [
  { at: 0, home: [[8, 50], [25, 28], [28, 70], [44, 40], [48, 65], [60, 24], [61, 52], [72, 76], [77, 42], [85, 62], [90, 35]], away: [[94, 50], [76, 25], [79, 68], [62, 38], [64, 62], [51, 26], [52, 75], [40, 46], [31, 22], [28, 58], [18, 43]], ball: [51, 50] },
  { at: 3092000, home: [[7, 50], [20, 30], [19, 68], [36, 42], [42, 65], [53, 27], [57, 56], [67, 77], [72, 42], [80, 62], [86, 31]], away: [[95, 50], [80, 20], [82, 73], [65, 36], [67, 67], [54, 18], [52, 72], [39, 50], [27, 26], [22, 55], [12, 38]], ball: [12, 38] },
  { at: DEMO_SNAPSHOT_MS, home: [[8, 50], [31, 24], [27, 76], [49, 30], [51, 67], [63, 18], [65, 50], [73, 74], [80, 28], [84, 37], [89, 56]], away: [[95, 50], [86, 18], [88, 74], [78, 44], [73, 61], [60, 27], [58, 70], [45, 45], [37, 20], [34, 62], [23, 38]], ball: [84, 37] },
];
const metricFrames = [
  { at: 0, possessionHome: 50, xgHome: 0, xgAway: 0, winHome: 38, winDraw: 30, momentum: 0 },
  { at: 24 * 60 * 1000, possessionHome: 53, xgHome: .62, xgAway: .21, winHome: 58, winDraw: 24, momentum: 34 },
  { at: 45 * 60 * 1000, possessionHome: 55, xgHome: .89, xgAway: .44, winHome: 63, winDraw: 23, momentum: 42 },
  { at: 62 * 60 * 1000, possessionHome: 52, xgHome: 1.08, xgAway: .96, winHome: 56, winDraw: 28, momentum: 12 },
  { at: 75 * 60 * 1000, possessionHome: 54, xgHome: 1.24, xgAway: 1.39, winHome: 27, winDraw: 48, momentum: -9 },
  { at: MATCH_END_MS, possessionHome: 52, xgHome: 1.55, xgAway: 1.61, winHome: 24, winDraw: 52, momentum: -4 },
];
const playerProfiles: PlayerProfile[] = [
  { id: 'p_rvr_01', team: 'home', markerIndex: 0, number: 1, name: 'Eli Ward', position: 'Goalkeeper', archetype: 'Sweeper keeper', description: 'Starts attacks early and holds a high position behind the back line.', distanceKm: 4.1, topSpeed: 25.4, touches: 38 },
  { id: 'p_rvr_02', team: 'home', markerIndex: 1, number: 2, name: 'Tomas Vale', position: 'Right back', archetype: 'Overlap runner', description: 'Provides width, then recovers quickly when possession turns over.', distanceKm: 8.7, topSpeed: 32.1, touches: 61 },
  { id: 'p_rvr_03', team: 'home', markerIndex: 2, number: 3, name: 'Idris Bell', position: 'Centre back', archetype: 'Front-foot defender', description: 'Steps into midfield to contest direct passes before they settle.', distanceKm: 7.2, topSpeed: 29.2, touches: 69 },
  { id: 'p_rvr_04', team: 'home', markerIndex: 3, number: 4, name: 'Jonah Price', position: 'Centre back', archetype: 'Cover defender', description: 'Protects the space behind the press and keeps the defensive line balanced.', distanceKm: 6.9, topSpeed: 28.7, touches: 72 },
  { id: 'p_rvr_05', team: 'home', markerIndex: 4, number: 5, name: 'Ravi Shah', position: 'Left back', archetype: 'Progressive carrier', description: 'Moves the ball through pressure and supports attacks from deeper areas.', distanceKm: 8.9, topSpeed: 31.8, touches: 67 },
  { id: 'p_rvr_06', team: 'home', markerIndex: 5, number: 6, name: 'Sofia Marin', position: 'Holding midfield', archetype: 'Tempo setter', description: 'Receives under pressure and changes the direction of Riverside attacks.', distanceKm: 9.4, topSpeed: 29.8, touches: 83 },
  { id: 'p_rvr_07', team: 'home', markerIndex: 6, number: 8, name: 'Avery Brooks', position: 'Central midfield', archetype: 'Box-to-box runner', description: 'Connects both penalty areas and leads the counter-press after turnovers.', distanceKm: 10.2, topSpeed: 31.2, touches: 74 },
  { id: 'p_rvr_08', team: 'home', markerIndex: 7, number: 7, name: 'Lena Park', position: 'Right wing', archetype: 'Direct winger', description: 'Attacks the outside shoulder and creates separation with early acceleration.', distanceKm: 8.5, topSpeed: 33.4, touches: 49 },
  { id: 'p_rvr_09', team: 'home', markerIndex: 8, number: 9, name: 'Mara Voss', position: 'Striker', archetype: 'Channel runner', description: 'Pins centre backs, then breaks into the space behind when the pass opens.', distanceKm: 8.1, topSpeed: 32.8, touches: 42 },
  { id: 'p_rvr_10', team: 'home', markerIndex: 9, number: 10, name: 'Noor Haddad', position: 'Attacking midfield', archetype: 'Chance creator', description: 'Finds pockets between the lines and looks for the final pass.', distanceKm: 8.8, topSpeed: 30.6, touches: 71 },
  { id: 'p_rvr_11', team: 'home', markerIndex: 10, number: 11, name: 'Cleo Mensah', position: 'Left wing', archetype: 'Inside forward', description: 'Drives toward goal from wide positions and combines around the box.', distanceKm: 8.3, topSpeed: 33.0, touches: 53 },
  { id: 'p_hrb_01', team: 'away', markerIndex: 0, number: 1, name: 'Mina Holt', position: 'Goalkeeper', archetype: 'Line keeper', description: 'Protects the goal area and prefers secure distribution into midfield.', distanceKm: 3.8, topSpeed: 24.6, touches: 34 },
  { id: 'p_hrb_02', team: 'away', markerIndex: 1, number: 2, name: 'Priya Nair', position: 'Right back', archetype: 'Defensive fullback', description: 'Controls wide space first and joins attacks when the route is secure.', distanceKm: 8.4, topSpeed: 31.1, touches: 58 },
  { id: 'p_hrb_03', team: 'away', markerIndex: 2, number: 4, name: 'Ada Byrne', position: 'Centre back', archetype: 'Aerial stopper', description: 'Attacks crosses and direct balls while organizing the defensive line.', distanceKm: 7.0, topSpeed: 28.9, touches: 64 },
  { id: 'p_hrb_04', team: 'away', markerIndex: 3, number: 5, name: 'Inez Costa', position: 'Centre back', archetype: 'Ball-playing defender', description: 'Breaks the first line with measured passes into central midfield.', distanceKm: 7.3, topSpeed: 29.4, touches: 78 },
  { id: 'p_hrb_05', team: 'away', markerIndex: 4, number: 3, name: 'Nia Cole', position: 'Left back', archetype: 'High outlet', description: 'Advances early to stretch the pitch and support attacks on the left.', distanceKm: 9.1, topSpeed: 32.2, touches: 66 },
  { id: 'p_hrb_06', team: 'away', markerIndex: 5, number: 6, name: 'Yara Finch', position: 'Holding midfield', archetype: 'Ball winner', description: 'Closes central passing lanes and protects the defence during transitions.', distanceKm: 9.7, topSpeed: 30.1, touches: 76 },
  { id: 'p_hrb_07', team: 'away', markerIndex: 6, number: 8, name: 'Mae Tan', position: 'Central midfield', archetype: 'Press-resistant carrier', description: 'Escapes pressure with close control and carries play into the next line.', distanceKm: 10.0, topSpeed: 31.0, touches: 81 },
  { id: 'p_hrb_08', team: 'away', markerIndex: 7, number: 7, name: 'Zoe Quinn', position: 'Right wing', archetype: 'Touchline winger', description: 'Stays wide to create one-versus-one situations and crossing angles.', distanceKm: 8.6, topSpeed: 33.2, touches: 51 },
  { id: 'p_hrb_09', team: 'away', markerIndex: 8, number: 9, name: 'Leila Okafor', position: 'Striker', archetype: 'Penalty-box finisher', description: 'Times short movements inside the box and attacks loose second balls.', distanceKm: 7.9, topSpeed: 31.9, touches: 39 },
  { id: 'p_hrb_10', team: 'away', markerIndex: 9, number: 10, name: 'Esme Laurent', position: 'Attacking midfield', archetype: 'Between-lines creator', description: 'Receives behind midfield and turns quickly toward the final third.', distanceKm: 8.9, topSpeed: 30.8, touches: 73 },
  { id: 'p_hrb_11', team: 'away', markerIndex: 10, number: 11, name: 'Talia Reed', position: 'Left wing', archetype: 'Counterattack runner', description: 'Accelerates into open space as soon as Harbour recover possession.', distanceKm: 8.2, topSpeed: 33.6, touches: 47 },
];
const trackingPlayerOrder = playerProfiles.map(player => player.id);
console.assert(events.every((event, index) => index === 0 || event.timeMs > events[index - 1].timeMs), 'Events must be chronological');
console.assert(snapshots.every((snapshot, index) => index === 0 || snapshot.at > snapshots[index - 1].at), 'Snapshots must be chronological');

function sampleTracking(frames: TrackingFrame[], clockMs: number): TrackingSample | null {
  if (!frames.length) return null;
  const upperIndex = frames.findIndex(frame => frame[0] >= clockMs);
  const next = frames[upperIndex === -1 ? frames.length - 1 : upperIndex];
  const previous = frames[Math.max(0, (upperIndex === -1 ? frames.length - 1 : upperIndex) - 1)];
  const durationSeconds = Math.max(.001, (next[0] - previous[0]) / 1000);
  const t = next[0] === previous[0] ? 0 : Math.max(0, Math.min(1, (clockMs - previous[0]) / (next[0] - previous[0])));
  const h00 = 2 * t ** 3 - 3 * t ** 2 + 1; const h10 = t ** 3 - 2 * t ** 2 + t;
  const h01 = -2 * t ** 3 + 3 * t ** 2; const h11 = t ** 3 - t ** 2;
  const players = Object.fromEntries(trackingPlayerOrder.map((id, index) => {
    const offset = index * 4; const start = previous[5]; const end = next[5];
    const x = h00 * start[offset] + h10 * durationSeconds * start[offset + 2] + h01 * end[offset] + h11 * durationSeconds * end[offset + 2];
    const y = h00 * start[offset + 1] + h10 * durationSeconds * start[offset + 3] + h01 * end[offset + 1] + h11 * durationSeconds * end[offset + 3];
    return [id, [Math.max(0, Math.min(105, x)), Math.max(0, Math.min(68, y))]];
  })) as Record<string, [number, number]>;
  return { ball: [previous[1] + (next[1] - previous[1]) * t, previous[2] + (next[2] - previous[2]) * t], players };
}

function formatClock(timeMs: number) { const seconds = Math.floor(timeMs / 1000); return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`; }
function getMetrics(timeMs: number): MatchMetrics {
  const nextIndex = metricFrames.findIndex(frame => frame.at >= timeMs);
  const next = metricFrames[nextIndex === -1 ? metricFrames.length - 1 : nextIndex];
  const previous = metricFrames[Math.max(0, (nextIndex === -1 ? metricFrames.length - 1 : nextIndex) - 1)];
  const progress = next.at === previous.at ? 0 : Math.max(0, Math.min(1, (timeMs - previous.at) / (next.at - previous.at)));
  const interpolate = (start: number, end: number) => start + (end - start) * progress;
  const winHome = Math.round(interpolate(previous.winHome, next.winHome));
  const winDraw = Math.round(interpolate(previous.winDraw, next.winDraw));
  return {
    possessionHome: Math.round(interpolate(previous.possessionHome, next.possessionHome)),
    xgHome: interpolate(previous.xgHome, next.xgHome),
    xgAway: interpolate(previous.xgAway, next.xgAway),
    winHome,
    winDraw,
    winAway: 100 - winHome - winDraw,
    momentum: Math.round(interpolate(previous.momentum, next.momentum)),
  };
}
function Button({ children, className = '', ...props }: React.ButtonHTMLAttributes<HTMLButtonElement>) { return <button className={`button ${className}`.trim()} {...props}>{children}</button>; }
function Mark() { return <span className="brand-mark" aria-hidden="true"><i /><i /><i /></span>; }
function TeamBadge({ team }: { team: TeamKey }) { const short = { rvr: 'RVR', hrb: 'HRB', ash: 'ASH' }[team]; return <span className={`team-badge ${team}`} aria-hidden="true">{short}</span>; }

function Pitch({ clockMs, selectedEvent, trackedIds, hoveredId, onHover, onToggleTrack, trackingSample }: { clockMs: number; selectedEvent: MatchEvent | null; trackedIds: string[]; hoveredId: string | null; onHover: (id: string | null) => void; onToggleTrack: (id: string) => void; trackingSample: TrackingSample | null }) {
  const upcomingIndex = snapshots.findIndex(item => item.at > clockMs);
  const baseIndex = upcomingIndex === -1 ? snapshots.length - 1 : Math.max(0, upcomingIndex - 1);
  const snapshot = snapshots[baseIndex] ?? snapshots[0];
  const nextSnapshot = snapshots[baseIndex + 1];
  const progress = nextSnapshot ? Math.max(0, Math.min(1, (clockMs - snapshot.at) / (nextSnapshot.at - snapshot.at))) : 0;
  const phase = clockMs / 1000;
  const animatePlayers = (players: number[][], nextPlayers?: number[][]) => players.map(([x, y], index) => {
    const baseX = nextPlayers ? x + (nextPlayers[index][0] - x) * progress : x;
    const baseY = nextPlayers ? y + (nextPlayers[index][1] - y) * progress : y;
    return [Math.max(2, Math.min(98, baseX + Math.sin(phase * .72 + index * 1.37) * .75)), Math.max(3, Math.min(97, baseY + Math.cos(phase * .61 + index * .93) * 1.05))];
  });
  const fallbackHome = animatePlayers(snapshot.home, nextSnapshot?.home);
  const fallbackAway = animatePlayers(snapshot.away, nextSnapshot?.away);
  const trackedPosition = (player: PlayerProfile): [number, number] | null => { const position = trackingSample?.players[player.id]; return position ? [position[0] / 1.05, position[1] / .68] : null; };
  const home = playerProfiles.filter(player => player.team === 'home').map(player => trackedPosition(player) ?? fallbackHome[player.markerIndex]);
  const away = playerProfiles.filter(player => player.team === 'away').map(player => trackedPosition(player) ?? fallbackAway[player.markerIndex]);
  const fallbackPlayers = [...fallbackHome, ...fallbackAway];
  const possessionSequence = [6, 9, 7, 8, 16, 20, 17, 19, 5, 10, 15, 18];
  const touchDuration = 4; const touchStep = Math.floor(phase / touchDuration); const touchProgress = (phase % touchDuration) / touchDuration;
  const carrier = fallbackPlayers[possessionSequence[touchStep % possessionSequence.length]];
  const receiver = fallbackPlayers[possessionSequence[(touchStep + 1) % possessionSequence.length]];
  const passProgress = Math.max(0, (touchProgress - .82) / .18); const passEased = passProgress * passProgress * (3 - 2 * passProgress);
  const footOffset = carrier[0] < receiver[0] ? .8 : -.8;
  const fallbackBall = touchProgress < .82 ? [carrier[0] + footOffset, carrier[1] + .35] : [carrier[0] + (receiver[0] - carrier[0]) * passEased, carrier[1] + (receiver[1] - carrier[1]) * passEased];
  const ball = trackingSample ? [trackingSample.ball[0] / 1.05, trackingSample.ball[1] / .68] : fallbackBall;
  const highlight = selectedEvent && selectedEvent.availableMs <= clockMs && selectedEvent.x !== undefined ? { x: selectedEvent.x, y: selectedEvent.y! } : null;
  const activeProfile = playerProfiles.find(player => player.id === hoveredId) ?? playerProfiles.find(player => player.id === trackedIds[trackedIds.length - 1]);
  const positionFor = (player: PlayerProfile) => (player.team === 'home' ? home : away)[player.markerIndex];
  const trailPoints = (player: PlayerProfile) => {
    const [x, y] = positionFor(player);
    const direction = player.team === 'home' ? 1 : -1;
    return Array.from({ length: 7 }, (_, point) => {
      const lag = 6 - point;
      const trailX = x - direction * lag * .8 + Math.sin(player.markerIndex + lag * .7) * .22;
      const trailY = y + Math.cos(player.markerIndex * .8 + lag) * lag * .12;
      return `${trailX * 1.05},${trailY * .68}`;
    }).join(' ');
  };
  const renderPlayer = (player: PlayerProfile, position: number[]) => {
    const [x, y] = position;
    const tracked = trackedIds.includes(player.id);
    return <g className={`player-marker ${player.team} ${tracked ? 'tracked' : ''}`} key={player.id} transform={`translate(${x * 1.05} ${y * .68})`} role="button" tabIndex={0} aria-label={`${player.name}, number ${player.number}, ${player.position}. ${tracked ? 'Remove from tracking' : 'Track player'}`} aria-pressed={tracked} onMouseEnter={() => onHover(player.id)} onMouseLeave={() => onHover(null)} onFocus={() => onHover(player.id)} onBlur={() => onHover(null)} onClick={() => onToggleTrack(player.id)} onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onToggleTrack(player.id); } }}>
      <circle className="hit-area" r="3.6" />
      {tracked && <circle className="selection-ring" r="2.75" />}
      <circle r="1.8" /><text y=".65" textAnchor="middle">{player.number}</text>
    </g>;
  };
  return <figure className="pitch-shell">
    <svg className="pitch" viewBox="0 0 105 68" role="img" aria-labelledby="pitch-title pitch-desc">
      <title id="pitch-title">Player positions at {formatClock(clockMs)}</title><desc id="pitch-desc">Riverside attack from left to right. The selected incident is highlighted when evidence is available.</desc>
      <rect className="pitch-ground" x="0" y="0" width="105" height="68" rx="1" />
      <g className="pitch-lines"><rect x="1" y="1" width="103" height="66" /><path d="M52.5 1V67" /><circle cx="52.5" cy="34" r="9.15" /><circle cx="52.5" cy="34" r=".55" className="pitch-dot" /><path d="M1 13.85H17.5V54.15H1M104 13.85H87.5V54.15H104" /><path d="M1 24.85H6.5V43.15H1M104 24.85H98.5V43.15H104" /><circle cx="11" cy="34" r=".55" className="pitch-dot" /><circle cx="94" cy="34" r=".55" className="pitch-dot" /></g>
      {highlight && <g className="evidence-zone"><circle cx={highlight.x * 1.05} cy={highlight.y * .68} r="5.5" /><path d={`M${highlight.x * 1.05 - 8} ${highlight.y * .68 + 7} L${highlight.x * 1.05 + 4} ${highlight.y * .68 - 4}`} /></g>}
      {trackedIds.map(id => playerProfiles.find(player => player.id === id)).filter((player): player is PlayerProfile => Boolean(player)).map(player => <polyline className={`tracking-trail ${player.team}`} points={trailPoints(player)} key={`trail-${player.id}`} />)}
      {playerProfiles.filter(player => player.team === 'home').map(player => renderPlayer(player, home[player.markerIndex]))}
      {playerProfiles.filter(player => player.team === 'away').map(player => renderPlayer(player, away[player.markerIndex]))}
      <circle className="ball-marker" cx={ball[0] * 1.05} cy={ball[1] * .68} r=".85" />
    </svg>
    {activeProfile && <aside className="player-persona" aria-live="polite"><div className="persona-top"><span className={`persona-number ${activeProfile.team}`}>{activeProfile.number}</span><div><strong>{activeProfile.name}</strong><span>{activeProfile.position} · {activeProfile.archetype}</span></div><em>{trackedIds.includes(activeProfile.id) ? 'Tracking' : 'Player profile'}</em></div><p>{activeProfile.description}</p><dl><div><dt>Distance</dt><dd>{activeProfile.distanceKm.toFixed(1)} km</dd></div><div><dt>Top speed</dt><dd>{activeProfile.topSpeed.toFixed(1)} km/h</dd></div><div><dt>Touches</dt><dd>{activeProfile.touches}</dd></div></dl></aside>}
    <figcaption><span>Hover a player · Select up to 3</span><span>Synthetic match visualization</span></figcaption>
  </figure>;
}

function Timeline({ clockMs, liveEdgeMs, onSeek, selectedId }: { clockMs: number; liveEdgeMs: number; onSeek: (time: number, event?: MatchEvent) => void; selectedId: string | null }) {
  const visible = events.filter(event => event.availableMs <= clockMs);
  return <section className="timeline-panel" aria-labelledby="timeline-title"><div className="panel-heading"><h2 id="timeline-title">Match timeline</h2><span>{visible.length} moments available</span></div><div className="timeline-track" aria-hidden="true"><span style={{ width: `${clockMs / liveEdgeMs * 100}%` }} />{events.map(event => event.availableMs <= liveEdgeMs ? <i key={event.id} style={{ left: `${event.timeMs / liveEdgeMs * 100}%` }} /> : null)}</div><div className="event-row">{visible.map(event => <Button key={event.id} className={`event-chip ${selectedId === event.id ? 'selected' : ''}`} onClick={() => onSeek(event.timeMs, event)} aria-pressed={selectedId === event.id}><span>{event.minute}</span>{event.title}</Button>)}</div></section>;
}

function LiveMetrics({ metrics, clockMs }: { metrics: MatchMetrics; clockMs: number }) {
  const awayPossession = 100 - metrics.possessionHome;
  const momentumTeam = metrics.momentum > 5 ? 'Riverside' : metrics.momentum < -5 ? 'Harbour' : 'Even';
  return <section className="match-insights" aria-labelledby="match-insights-title">
    <header><div><span className="eyebrow">Live match centre</span><h2 id="match-insights-title">At {formatClock(clockMs)}</h2></div><span className="estimate-label">Updates with replay clock</span></header>
    <div className="insight-grid">
      <article className="metric-card possession-card"><div className="metric-heading"><span>Possession</span><strong>{metrics.possessionHome}% <i>–</i> {awayPossession}%</strong></div><div className="split-bar" aria-label={`Riverside ${metrics.possessionHome} percent possession, Harbour ${awayPossession} percent`}><i style={{ width: `${metrics.possessionHome}%` }} /><i style={{ width: `${awayPossession}%` }} /></div><div className="metric-legend"><span>RVR</span><span>HRB</span></div></article>
      <article className="metric-card"><div className="metric-heading"><span>Expected goals</span><strong>{metrics.xgHome.toFixed(2)} <i>–</i> {metrics.xgAway.toFixed(2)}</strong></div><p>Chance quality through the current replay time</p></article>
      <article className="metric-card win-card"><div className="metric-heading"><span>Win chance</span><em>Stat estimate</em></div><div className="probability-values"><strong>{metrics.winHome}%<small>RVR</small></strong><strong>{metrics.winDraw}%<small>Draw</small></strong><strong>{metrics.winAway}%<small>HRB</small></strong></div><div className="probability-bar" aria-label={`Riverside win ${metrics.winHome} percent, draw ${metrics.winDraw} percent, Harbour win ${metrics.winAway} percent`}><i style={{ width: `${metrics.winHome}%` }} /><i style={{ width: `${metrics.winDraw}%` }} /><i style={{ width: `${metrics.winAway}%` }} /></div></article>
      <article className="metric-card momentum-card"><div className="metric-heading"><span>Momentum</span><strong>{momentumTeam}</strong></div><div className="momentum-track" aria-label={`${momentumTeam} has the current momentum`}><i className="midpoint" /><i className="momentum-value" style={{ left: `${50 + metrics.momentum / 2}%` }} /></div><div className="metric-legend"><span>RVR</span><span>HRB</span></div></article>
    </div>
  </section>;
}

function SidePanel({ panel, setPanel, clockMs, selectedEvent, metrics }: { panel: Panel; setPanel: (panel: Panel) => void; clockMs: number; selectedEvent: MatchEvent | null; metrics: MatchMetrics }) {
  const visible = events.filter(event => event.availableMs <= clockMs);
  const shots = visible.filter(event => event.type === 'goal' || event.type === 'review').length + 5;
  const views: { id: Panel; label: string }[] = [{ id: 'moments', label: 'Moments' }, { id: 'stats', label: 'Stats' }, { id: 'players', label: 'Players' }, { id: 'explain', label: 'Explain' }, { id: 'referee', label: 'Referee' }];
  return <section className="side-panel" aria-label="Match evidence"><label className="panel-switcher"><span>Evidence view</span><select value={panel} onChange={event => setPanel(event.target.value as Panel)}>{views.map(view => <option value={view.id} key={view.id}>{view.label}</option>)}</select></label><div className="panel-content">
    {panel === 'moments' && <><div className="panel-heading"><h2>Visible moments</h2><span>Through {formatClock(clockMs)}</span></div><div className="moment-list">{visible.slice(-4).reverse().map(event => <article className={`moment ${selectedEvent?.id === event.id ? 'selected' : ''}`} key={event.id}><time>{event.minute}</time><div><h3>{event.title}</h3>{selectedEvent?.id === event.id && <p>{event.detail}</p>}</div></article>)}</div></>}
    {panel === 'stats' && <><div className="panel-heading"><h2>Match pulse</h2><span>Through {formatClock(clockMs)}</span></div><div className="stat-strip"><div><span>Possession</span><strong>{metrics.possessionHome}<span>%</span></strong></div><div><span>Shots</span><strong>{shots}</strong></div><div><span>RVR xG</span><strong>{metrics.xgHome.toFixed(2)}</strong></div></div><div className="pressure"><div><span>Current momentum</span><strong>{metrics.momentum > 5 ? 'Riverside' : metrics.momentum < -5 ? 'Harbour' : 'Even'}</strong></div><span><i style={{ width: `${50 + metrics.momentum / 2}%` }} /></span><p>Calculated from visible possession, chance quality, and final-third entries.</p></div></>}
    {panel === 'players' && <><div className="panel-heading"><h2>Players in focus</h2><span>At {formatClock(clockMs)}</span></div><div className="player-list"><article><TeamBadge team="rvr" /><div><h3>Mara Voss</h3><p>1 goal · 3 shots</p></div><strong>8.1</strong></article><article><TeamBadge team="hrb" /><div><h3>Leila Okafor</h3><p>1 penalty goal · 2 shots</p></div><strong>7.0</strong></article><article><TeamBadge team="hrb" /><div><h3>Nia Cole</h3><p>1 reviewed run · 3 recoveries</p></div><strong>7.8</strong></article></div></>}
    {panel === 'explain' && <><div className="panel-heading"><h2>Why it mattered</h2><span>Facts-only preview</span></div><div className="explanation"><p className="finding">Harbour’s penalty changed the visible score to 1–1.</p><p>The supplied snapshot places the foul inside Riverside’s penalty area. Okafor then scores from the penalty mark.</p><Button className="primary" onClick={() => setPanel('moments')}>Show the evidence</Button></div></>}
    {panel === 'referee' && <><div className="panel-heading"><h2>Ask the ref</h2><span>Decision at 74:20</span></div><div className="decision"><span className="status">Awarded</span><h3>Penalty kick</h3><p>The recorded contact occurs inside the penalty area while the ball is in play.</p><dl><div><dt>Incident</dt><dd>Foul</dd></div><div><dt>Final decision</dt><dd>Penalty</dd></div><div><dt>Outcome</dt><dd>Goal</dd></div></dl></div></>}
  </div></section>;
}

function ReplayApp() {
  const [clockMs, setClockMs] = useState(sharedMatchMs); const [liveEdgeMs, setLiveEdgeMs] = useState(sharedMatchMs); const [playing, setPlaying] = useState(true); const [speed, setSpeed] = useState<1 | 4>(1); const [panel, setPanel] = useState<Panel>('moments'); const [selectedEvent, setSelectedEvent] = useState<MatchEvent | null>(null);
  const [liveClock, setLiveClock] = useState<LiveClock>(defaultLiveClock);
  const [trackingFrames, setTrackingFrames] = useState<TrackingFrame[]>([]);
  const resumeLiveWhenVisible = useRef(false);
  const [trackedIds, setTrackedIds] = useState<string[]>([]); const [hoveredId, setHoveredId] = useState<string | null>(null); const [trackingNotice, setTrackingNotice] = useState('Select up to three players to compare their movement.');
  const visibleEvents = useMemo(() => events.filter(event => event.availableMs <= clockMs), [clockMs]); const homeScore = visibleEvents.filter(event => event.type === 'goal' && event.team === 'rvr').length; const awayScore = visibleEvents.filter(event => event.type === 'goal' && event.team === 'hrb').length;
  const metrics = useMemo(() => getMetrics(clockMs), [clockMs]);
  const trackedPlayers = useMemo(() => trackedIds.map(id => playerProfiles.find(player => player.id === id)).filter((player): player is PlayerProfile => Boolean(player)), [trackedIds]);
  const trackingChunkIndex = Math.floor(clockMs / 60_000);
  const trackingSample = useMemo(() => sampleTracking(trackingFrames, clockMs), [trackingFrames, clockMs]);
  useEffect(() => { if (window.location.protocol === 'file:') return; const controller = new AbortController(); const load = async () => { const response = await fetch(apiUrl(`/api/matches/m_rvr_hrb/tracking/chunks/${trackingChunkIndex}?revision=${TRACKING_REVISION}`), { signal: controller.signal }); if (!response.ok) throw new Error('Tracking unavailable'); const chunk = await response.json(); const frames = Array.isArray(chunk.frames) ? chunk.frames : []; const nextResponse = await fetch(apiUrl(`/api/matches/m_rvr_hrb/tracking/chunks/${trackingChunkIndex + 1}?revision=${TRACKING_REVISION}`), { signal: controller.signal }); if (nextResponse.ok) { const nextChunk = await nextResponse.json(); if (Array.isArray(nextChunk.frames) && nextChunk.frames.length) frames.push(nextChunk.frames[0]); } setTrackingFrames(frames); }; load().catch(error => { if (error?.name !== 'AbortError') setTrackingFrames([]); }); return () => controller.abort(); }, [trackingChunkIndex]);
  useEffect(() => { if (window.location.protocol === 'file:') return; let active = true; fetch(apiUrl('/api/live-state'), { cache: 'no-store' }).then(response => response.ok ? response.json() : Promise.reject()).then(state => { if (!active || state.matchId !== 'm_rvr_hrb') return; const synchronizedClock = { serverOffsetMs: state.serverTimeMs - Date.now(), epochMs: state.instanceStartMs - state.cycleNumber * state.durationMs, durationMs: state.durationMs }; setLiveClock(synchronizedClock); setLiveEdgeMs(state.matchMs); setClockMs(state.matchMs); }).catch(() => undefined); return () => { active = false; }; }, []);
  useEffect(() => { const timer = window.setInterval(() => { const nextEdge = sharedMatchMs(liveClock); setLiveEdgeMs(nextEdge); setClockMs(current => playing || current > nextEdge ? nextEdge : current); }, 100); return () => window.clearInterval(timer); }, [playing, liveClock]);
  const goLive = () => { const currentLiveEdge = sharedMatchMs(liveClock); setLiveEdgeMs(currentLiveEdge); setClockMs(currentLiveEdge); setPlaying(true); };
  const togglePlayback = () => { if (playing) setPlaying(false); else goLive(); };
  useEffect(() => { const handleKey = (event: KeyboardEvent) => { if (event.target instanceof HTMLInputElement || event.target instanceof HTMLButtonElement) return; if (event.code === 'Space') { event.preventDefault(); if (playing) setPlaying(false); else goLive(); } if (event.code === 'ArrowLeft') { setPlaying(false); setClockMs(value => Math.max(0, value - 5000)); } if (event.code === 'ArrowRight') setClockMs(value => Math.min(liveEdgeMs, value + 5000)); }; window.addEventListener('keydown', handleKey); return () => window.removeEventListener('keydown', handleKey); }, [liveEdgeMs, playing]);
  useEffect(() => { const handleVisibility = () => { if (document.hidden) { setPlaying(current => { resumeLiveWhenVisible.current = current; return false; }); } else if (resumeLiveWhenVisible.current) { resumeLiveWhenVisible.current = false; goLive(); } }; document.addEventListener('visibilitychange', handleVisibility); return () => document.removeEventListener('visibilitychange', handleVisibility); }, [liveClock]);
  useEffect(() => { if (clockMs >= MATCH_END_MS) setPlaying(false); if (selectedEvent && selectedEvent.availableMs > clockMs) setSelectedEvent(null); }, [clockMs, selectedEvent]);
  const toggleTrack = (id: string) => { setTrackedIds(current => { if (current.includes(id)) { setTrackingNotice('Player removed. Select another player from the pitch.'); return current.filter(playerId => playerId !== id); } if (current.length >= 3) { setTrackingNotice('You can track up to three players. Remove one before adding another.'); return current; } setTrackingNotice(current.length === 2 ? 'Three-player tracking limit reached.' : `Select ${2 - current.length} more player${current.length === 1 ? '' : 's'} to compare movement.`); return [...current, id]; }); };
  const seek = (time: number, event?: MatchEvent) => { setPlaying(false); setClockMs(Math.max(0, Math.min(liveEdgeMs, time))); if (event) setSelectedEvent(event); };
  return <div className="app-shell"><header className="topbar"><a className="brand" href="/" aria-label="Next Whistle match picker"><Mark /><span>Next Whistle</span></a><nav aria-label="Primary navigation"><a className="active" href="/match/m_rvr_hrb">Replay</a></nav><button className="live-state is-live" onClick={goLive} aria-label="Jump to the live point"><span />Live</button></header><main className="workspace"><header className="match-header"><div className="competition"><a href="/">All matches</a><span>/</span><span>Northbank Cup · Matchday 1</span></div><div className="scoreline"><div className="team-name"><TeamBadge team="rvr" /><span>Riverside FC</span></div><strong className="score" aria-label={`Riverside ${homeScore}, Harbour ${awayScore}`}>{homeScore}<span>–</span>{awayScore}</strong><div className="team-name away-name"><span>Harbour Town</span><TeamBadge team="hrb" /></div></div><div className="match-meta"><span className="clock">{formatClock(clockMs)}</span><span>{playing ? 'Live' : 'Paused'}</span><span>Willowbank Ground</span></div></header><LiveMetrics metrics={metrics} clockMs={clockMs} /><div className="replay-grid"><section className="replay-stage" aria-label="Live match replay"><Pitch clockMs={clockMs} selectedEvent={selectedEvent} trackedIds={trackedIds} hoveredId={hoveredId} onHover={setHoveredId} onToggleTrack={toggleTrack} trackingSample={trackingSample} /><section className="tracking-dock" aria-labelledby="tracking-title"><div className="tracking-summary"><div><span>Player tracking</span><strong id="tracking-title">{trackedIds.length} of 3 selected</strong></div><p aria-live="polite">{trackingNotice}</p></div><div className="tracking-chips">{trackedPlayers.map(player => <button key={player.id} className={player.team} onClick={() => toggleTrack(player.id)} aria-label={`Stop tracking ${player.name}`}><span>{player.number}</span>{player.name}<i aria-hidden="true">×</i></button>)}</div></section><div className="playback" aria-label="Playback controls"><Button className="play" onClick={togglePlayback} aria-pressed={playing}>{playing ? 'Pause' : 'Play live'}</Button><label className="scrubber"><span className="sr-only">Replay time, ending at the live point</span><input type="range" min="0" max={liveEdgeMs} step="1000" value={Math.round(clockMs)} onChange={event => seek(Number(event.target.value))} /></label><Button className="speed" onClick={() => setSpeed(value => value === 1 ? 4 : 1)} aria-label={`Playback speed ${speed} times`}>{speed}×</Button></div></section><SidePanel panel={panel} setPanel={setPanel} clockMs={clockMs} selectedEvent={selectedEvent} metrics={metrics} /></div><Timeline clockMs={clockMs} liveEdgeMs={liveEdgeMs} selectedId={selectedEvent?.id ?? null} onSeek={(time, event) => seek(time, event)} /></main></div>;
}

function MatchPicker() {
  const fixtures = [{ id: 'm_rvr_hrb', round: 'Northbank Cup · Matchday 1', date: '16 Aug 2026', home: 'Riverside FC', away: 'Harbour Town', homeKey: 'rvr' as TeamKey, awayKey: 'hrb' as TeamKey }, { id: 'm_hrb_ash', round: 'Northbank Cup · Matchday 2', date: '23 Aug 2026', home: 'Harbour Town', away: 'Ashgrove United', homeKey: 'hrb' as TeamKey, awayKey: 'ash' as TeamKey }];
  return <div className="picker"><header className="topbar"><a className="brand" href="/"><Mark /><span>Next Whistle</span></a></header><main><header><h1>Choose a match to replay</h1><p>Follow the clock, inspect turning points, and review decisions without future spoilers.</p></header><div className="fixture-list">{fixtures.map((fixture, index) => <a className="fixture" href={`/match/${fixture.id}`} key={fixture.id}><div><span>{fixture.round}</span><time>{fixture.date}</time></div><div className="fixture-teams"><TeamBadge team={fixture.homeKey} /><strong>{fixture.home}</strong><span>v</span><strong>{fixture.away}</strong><TeamBadge team={fixture.awayKey} /></div><span className="open-match">Open replay →</span></a>)}</div></main></div>;
}
function App() { const isReplay = window.location.protocol === 'file:' || window.location.pathname.startsWith('/match/') || new URLSearchParams(window.location.search).get('view') === 'replay'; return isReplay ? <ReplayApp /> : <MatchPicker />; }
createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>);
