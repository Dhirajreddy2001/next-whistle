import { useRef, useState } from 'react';
import { requestRefereeAnswer, type GenerationResult, type QuestionTurn } from './api/client';

export type AskDecision = { eventId: string; title: string; minute: string };
type Suggestion = { id: string; text: string };
type Turn = { id: number; question: string; result: GenerationResult | null; error: string };

const MAX_LENGTH = 300;
const SCOPE_NOTE: Record<string, string> = {
  refused: 'Not answered',
  out_of_scope: 'Outside what I can answer',
  redirect: 'About a different decision',
  general_law: 'Laws of the Game',
};

const headline = (answer: string) => answer.split(/(?<=[.!?])\s/)[0].slice(0, 120);

export function AskPanel({ matchId, language, level, clockMs, decision, suggestions, onShowDecision }: {
  matchId: string; language: string; level: string; clockMs: number; decision: AskDecision | null; suggestions: Suggestion[];
  onShowDecision: (eventId: string) => void;
}) {
  const [text, setText] = useState('');
  const [aboutDecision, setAboutDecision] = useState(true);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const nextId = useRef(1);
  const activeDecision = aboutDecision ? decision : null;

  const ask = async (question: string, questionId?: string) => {
    const trimmed = question.trim();
    if (!trimmed || busy) return;
    const id = nextId.current++;
    const history: QuestionTurn[] = turns.filter(turn => turn.result).slice(-2).map(turn => ({ question: turn.question, answerHeadline: headline(turn.result!.answer) }));
    setBusy(true); setText('');
    setTurns(current => [...current, { id, question: trimmed, result: null, error: '' }].slice(-6));
    try {
      const result = await requestRefereeAnswer(matchId, {
        cutoffMs: Math.floor(clockMs), eventId: activeDecision?.eventId, language, level,
        ...(questionId && activeDecision ? { questionId } : { questionText: trimmed, history }),
      });
      setTurns(current => current.map(turn => turn.id === id ? { ...turn, result } : turn));
    } catch (reason) {
      const error = reason instanceof Error ? reason.message : 'Could not get an answer.';
      setTurns(current => current.map(turn => turn.id === id ? { ...turn, error } : turn));
    } finally { setBusy(false); }
  };

  return <section className="ask-panel" aria-label="Ask the Ref">
    <div className="panel-heading"><h2>Ask the Ref</h2></div>
    <p className="ask-scope">
      {decision
        ? <label><input type="checkbox" checked={aboutDecision} onChange={event => setAboutDecision(event.target.checked)} /> About {decision.minute} · {decision.title}</label>
        : 'No recorded decision yet. Ask a general question about the Laws of the Game.'}
    </p>
    {!!activeDecision && !!suggestions.length && <div className="ask-chips" role="group" aria-label="Suggested questions">
      {suggestions.map(suggestion => <button key={suggestion.id} type="button" disabled={busy} onClick={() => void ask(suggestion.text, suggestion.id)}>{suggestion.text}</button>)}
    </div>}
    <form className="ask-form" onSubmit={event => { event.preventDefault(); void ask(text); }}>
      <label className="sr-only" htmlFor="ask-text">Your question</label>
      <textarea id="ask-text" value={text} maxLength={MAX_LENGTH} rows={3} placeholder={activeDecision ? 'Ask about this decision…' : 'e.g. How long can a goalkeeper hold the ball?'} onChange={event => setText(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void ask(text); } }} />
      <div className="ask-foot"><span aria-live="polite">{text.length}/{MAX_LENGTH}</span><button type="submit" className="button primary" disabled={busy || !text.trim()}>{busy ? 'Checking…' : 'Ask'}</button></div>
    </form>
    <ol className="ask-thread" aria-live="polite">
      {turns.map(turn => <li key={turn.id}>
        <p className="ask-question">{turn.question}</p>
        {!turn.result && !turn.error && <p className="ask-wait">Checking the evidence…</p>}
        {turn.error && <p role="alert" className="ask-error">{turn.error}</p>}
        {turn.result && <div className="ask-answer">
          <p className="status">{SCOPE_NOTE[turn.result.scope ?? ''] ?? (turn.result.source === 'template' ? 'Facts only' : turn.result.source === 'fake' ? 'Demo agent' : 'AI generated')}</p>
          <p>{turn.result.answer}</p>
          {turn.result.scope === 'redirect' && turn.result.redirectEventId && <button type="button" className="ask-link" onClick={() => onShowDecision(turn.result!.redirectEventId!)}>Show that decision</button>}
          {!!turn.result.ruleRefs?.length && <ul aria-label="Rule sources">{turn.result.ruleRefs.map(rule => <li key={rule.ruleId}><a href={rule.sourceUrl} target="_blank" rel="noreferrer">IFAB Law {rule.lawNumber}: {rule.title}</a></li>)}</ul>}
          {!!turn.result.evidenceIds.length && <small>{turn.result.evidenceIds.length} evidence records</small>}
        </div>}
      </li>)}
    </ol>
  </section>;
}
