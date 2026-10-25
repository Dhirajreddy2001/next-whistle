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
  cached?: boolean;
  generationEnabled?: boolean;
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

function newIdempotencyKey(): string {
  try { return crypto.randomUUID(); } catch { return `k${Date.now().toString(36)}${Math.random().toString(36).slice(2, 12)}`; }
}

async function refusalMessage(response: Response): Promise<string> {
  const problem = await response.json().catch(() => ({})) as { title?: string; code?: string };
  const wait = Number(response.headers.get('Retry-After'));
  const after = wait > 0 ? ` Try again in ${wait >= 90 ? `${Math.ceil(wait / 60)} minutes` : `${wait} seconds`}.` : '';
  if (problem.code === 'busy') return `The assistant is busy right now.${after || ' Try again in a moment.'}`;
  if (problem.code === 'allowance_exhausted') return 'AI explanations are paused for now. Recorded facts are still available.';
  if (response.status === 429) return `You have reached the question limit.${after}`;
  return problem.title ?? `Could not generate an answer (${response.status}).`;
}

async function postGeneration(path: string, body: Record<string, unknown>): Promise<GenerationResult> {
  let tokens = readSession();
  if (!tokens) tokens = await withSession(createSession);

  // One key per user action: a retry after a token refresh or a network blip returns the same answer instead of generating twice.
  const idempotencyKey = newIdempotencyKey();
  const send = (accessToken: string) => fetch(`${API}${path}`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${accessToken}`, 'Content-Type': 'application/json', 'Idempotency-Key': idempotencyKey },
    body: JSON.stringify(body),
  });
  let response = await send(tokens.accessToken);
  if (response.status === 401) {
    tokens = await withSession(() => refreshSession(tokens!));
    response = await send(tokens.accessToken);
  }
  if (!response.ok) throw new Error(await refusalMessage(response));
  return response.json() as Promise<GenerationResult>;
}

export function requestExplanation(
  matchId: string,
  body: { cutoffMs: number; eventId?: string; intent: 'explain' | 'recap'; language: string; level?: string },
) {
  return postGeneration(`/api/matches/${encodeURIComponent(matchId)}/explanations`, body);
}

export type QuestionTurn = { question: string; answerHeadline: string };

export function requestRefereeAnswer(
  matchId: string,
  body: { cutoffMs: number; eventId?: string; questionId?: string; questionText?: string; history?: QuestionTurn[]; language: string; level?: string },
) {
  return postGeneration(`/api/matches/${encodeURIComponent(matchId)}/referee/questions`, body);
}
