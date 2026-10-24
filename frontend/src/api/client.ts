type SessionTokens = {
  accessToken: string;
  refreshToken: string;
};

export type GenerationResult = {
  requestId: string;
  matchId: string;
  datasetVersion: string;
  cutoffMs: number;
  status: 'ok' | 'fallback';
  source: 'generated' | 'fake' | 'template';
  workflowVersion: string;
  answer: string;
  evidenceIds: string[];
  eventId?: string;
  scope?: 'in_scope' | 'general_law' | 'redirect' | 'out_of_scope' | 'refused';
  redirectEventId?: string | null;
  ruleRefs?: { ruleId: string; lawNumber: number; title: string; sourceUrl: string }[];
};

const API = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '');
const SESSION_KEY = 'next-whistle-anonymous-session';
let sessionTask: Promise<SessionTokens> | null = null;
let memorySession: SessionTokens | null = null;

function readSession(): SessionTokens | null {
  if (memorySession) return memorySession;
  try {
    const value = sessionStorage.getItem(SESSION_KEY);
    if (!value) return null;
    const tokens = JSON.parse(value) as SessionTokens;
    return tokens.accessToken && tokens.refreshToken ? tokens : null;
  } catch {
    return null;
  }
}

function writeSession(tokens: SessionTokens) {
  memorySession = tokens;
  try { sessionStorage.setItem(SESSION_KEY, JSON.stringify(tokens)); } catch { /* Keep this tab working when browser storage is unavailable. */ }
}

async function createSession(): Promise<SessionTokens> {
  const response = await fetch(`${API}/api/sessions`, { method: 'POST' });
  if (!response.ok) throw new Error('Could not start a secure session. Please try again.');
  const tokens = await response.json() as SessionTokens;
  writeSession(tokens);
  return tokens;
}

async function refreshSession(tokens: SessionTokens): Promise<SessionTokens> {
  const response = await fetch(`${API}/api/sessions/refresh`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refreshToken: tokens.refreshToken }),
  });
  if (!response.ok) {
    memorySession = null;
    try { sessionStorage.removeItem(SESSION_KEY); } catch { /* Storage is optional; create a fresh in-memory session. */ }
    return createSession();
  }
  const refreshed = await response.json() as SessionTokens;
  writeSession(refreshed);
  return refreshed;
}

function withSession(task: () => Promise<SessionTokens>): Promise<SessionTokens> {
  if (!sessionTask) {
    sessionTask = task().finally(() => { sessionTask = null; });
  }
  return sessionTask;
}

async function postGeneration(path: string, body: Record<string, unknown>): Promise<GenerationResult> {
  let tokens = readSession();
  if (!tokens) tokens = await withSession(createSession);

  const send = (accessToken: string) => fetch(`${API}${path}`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${accessToken}`, 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  let response = await send(tokens.accessToken);
  if (response.status === 401) {
    tokens = await withSession(() => refreshSession(tokens!));
    response = await send(tokens.accessToken);
  }
  if (!response.ok) {
    const problem = await response.json().catch(() => ({})) as { title?: string };
    if (response.status === 429) throw new Error('Generation limit reached. Try again later.');
    throw new Error(problem.title ?? `Could not generate an answer (${response.status}).`);
  }
  return response.json() as Promise<GenerationResult>;
}

export function requestExplanation(
  matchId: string,
  body: { cutoffMs: number; eventId?: string; intent: 'explain' | 'recap'; language: string },
) {
  return postGeneration(`/api/matches/${encodeURIComponent(matchId)}/explanations`, body);
}

export type QuestionTurn = { question: string; answerHeadline: string };

export function requestRefereeAnswer(
  matchId: string,
  body: { cutoffMs: number; eventId?: string; questionId?: string; questionText?: string; history?: QuestionTurn[]; language: string },
) {
  return postGeneration(`/api/matches/${encodeURIComponent(matchId)}/referee/questions`, body);
}
