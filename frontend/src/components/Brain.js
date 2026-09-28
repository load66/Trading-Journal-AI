import { useState, useRef, useEffect } from 'react';
import { X, Send, Brain as BrainIcon, RotateCcw } from 'lucide-react';
import { brainApi } from '../api';

// ── Markdown renderer ─────────────────────────────────────────────────────────

function parseLine(text) {
  const parts = [];
  let remaining = text;
  let key = 0;
  while (remaining.length) {
    const bold = remaining.match(/^([\s\S]*?)\*\*([\s\S]*?)\*\*([\s\S]*)$/);
    const code = remaining.match(/^([\s\S]*?)`([^`]+)`([\s\S]*)$/);
    if (bold && (!code || bold[1].length <= code[1].length)) {
      if (bold[1]) parts.push(bold[1]);
      parts.push(<strong key={key++}>{bold[2]}</strong>);
      remaining = bold[3];
    } else if (code) {
      if (code[1]) parts.push(code[1]);
      parts.push(<code key={key++} className="brain-inline-code">{code[2]}</code>);
      remaining = code[3];
    } else {
      parts.push(remaining);
      break;
    }
  }
  return parts.length === 1 && typeof parts[0] === 'string' ? parts[0] : parts;
}

function Markdown({ text }) {
  return (
    <div className="brain-markdown">
      {(text || '').split('\n').map((line, i) => {
        if (line.startsWith('### ')) return <div key={i} className="brain-md-h3">{parseLine(line.slice(4))}</div>;
        if (line.startsWith('## ')) return <div key={i} className="brain-md-h2">{parseLine(line.slice(3))}</div>;
        if (line.startsWith('# ')) return <div key={i} className="brain-md-h1">{parseLine(line.slice(2))}</div>;
        if (line.startsWith('- ') || line.startsWith('* ')) return <div key={i} className="brain-md-list">• {parseLine(line.slice(2))}</div>;
        if (/^\d+\. /.test(line)) {
          const num = line.match(/^\d+/)[0];
          return <div key={i} className="brain-md-list">{num}. {parseLine(line.replace(/^\d+\. /, ''))}</div>;
        }
        if (line === '') return <div key={i} className="brain-md-spacer" />;
        return <div key={i} className="brain-md-line">{parseLine(line)}</div>;
      })}
    </div>
  );
}

const SUGGESTIONS = [
  'Diagnose my biggest trading leak',
  'Where am I losing the most money?',
  'What is my best performing strategy?',
  'What time of day do I trade best?',
  'Compare my longs vs shorts',
  'Review my last 30 trading days',
];

export default function Brain({ accountId, open: openProp, onOpenChange }) {
  const [openLocal, setOpenLocal] = useState(false);
  const open = openProp ?? openLocal;
  const setOpen = (v) => { if (onOpenChange) onOpenChange(v); else setOpenLocal(v); };
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const bottomRef = useRef(null);
  const inputRef = useRef(null);
  const launcherRef = useRef(null);
  const wasOpen = useRef(false);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }, [messages, loading, error]);

  useEffect(() => {
    if (open && window.matchMedia('(min-width: 601px)').matches) inputRef.current?.focus();
    else if (!open && wasOpen.current) launcherRef.current?.focus();
    wasOpen.current = open;
  }, [open]);

  const send = async (text) => {
    const msg = (text || input).trim();
    if (!msg || loading) return;

    setInput('');
    setError('');
    const userMsg = { role: 'user', content: msg };
    const nextMessages = [...messages, userMsg];
    setMessages(nextMessages);
    setLoading(true);

    try {
      const res = await brainApi.chat(nextMessages, accountId);
      const answer = res?.data?.response;
      if (!answer) throw new Error('Brain returned an empty response.');
      setMessages(prev => [...prev, { role: 'assistant', content: answer }]);
    } catch (e) {
      setError(e.response?.data?.detail || e.message || 'Brain could not answer that question.');
    } finally {
      setLoading(false);
    }
  };

  const clearChat = () => {
    if (loading) return;
    setMessages([]);
    setInput('');
    setError('');
  };

  return (
    <>
      {open ? (
        <div
          className="brain-panel"
          role="dialog"
          aria-label="Brain, AI trading coach"
          onKeyDown={e => { if (e.key === 'Escape') setOpen(false); }}
        >
          <div className="brain-sheet-handle" aria-hidden="true" />

          <div className="brain-header">
            <div className="brain-title-wrap">
              <div className="brain-icon-wrap"><BrainIcon size={19} aria-hidden="true" /></div>
              <div>
                <div className="brain-title">Brain</div>
                <div className="brain-subtitle">
                  Journal AI <span className="brain-live-dot" /> Live journal data
                </div>
              </div>
            </div>
            <div className="brain-header-actions">
              {messages.length > 0 && (
                <button type="button" className="btn btn-ghost btn-icon brain-clear" onClick={clearChat} aria-label="New Brain chat" title="New chat">
                  <RotateCcw size={15} />
                </button>
              )}
              <button type="button" className="btn btn-ghost btn-icon" onClick={() => setOpen(false)} aria-label="Close Brain">
                <X size={16} />
              </button>
            </div>
          </div>

          <div className="brain-messages" aria-live="polite">
            {messages.length === 0 && (
              <div className="brain-welcome">
                <div className="brain-welcome-title">Ask your journal, not a generic chatbot.</div>
                <div className="brain-welcome-copy">
                  Brain can analyze your trades, P&amp;L, tickers, strategies, timing, hold time, management metrics, diary notes, and Day Reviews.
                </div>
                <div className="brain-suggestions">
                  {SUGGESTIONS.map((s, i) => (
                    <button key={i} type="button" className="brain-suggestion" onClick={() => send(s)}>
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {messages.map((msg, i) => (
              <div key={i} className={`brain-message-row ${msg.role === 'user' ? 'is-user' : 'is-ai'}`}>
                <div className={`brain-bubble ${msg.role === 'user' ? 'is-user' : 'is-ai'}`}>
                  {msg.role === 'assistant'
                    ? <Markdown text={msg.content} />
                    : <div className="brain-user-text">{msg.content}</div>}
                </div>
              </div>
            ))}

            {loading && (
              <div className="brain-thinking" aria-label="Brain is analyzing your journal">
                <BrainIcon size={14} aria-hidden="true" />
                <span>Analyzing journal data</span>
                <span className="brain-thinking-dots">
                  {[0, 1, 2].map(j => <i key={j} style={{ animationDelay: `${j * 0.18}s` }} />)}
                </span>
              </div>
            )}

            {error && (
              <div className="brain-error" role="alert">
                <strong>Brain couldn't answer.</strong>
                <span>{error}</span>
                <button type="button" onClick={() => send(messages[messages.length - 1]?.content)}>Retry</button>
              </div>
            )}
            <div ref={bottomRef} />
          </div>

          <div className="brain-composer">
            <textarea
              ref={inputRef}
              value={input}
              rows={1}
              onChange={e => setInput(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  send();
                }
              }}
              placeholder="Ask about any trade, pattern, ticker, strategy…"
              aria-label="Message Brain"
              disabled={loading}
            />
            <button
              type="button"
              className="btn btn-primary btn-icon brain-send"
              onClick={() => send()}
              disabled={loading || !input.trim()}
              aria-label="Send message"
            >
              <Send size={16} />
            </button>
          </div>
        </div>
      ) : (
        <button
          ref={launcherRef}
          type="button"
          className="brain-launcher"
          onClick={() => setOpen(true)}
          title="Open Brain, journal AI"
        >
          <BrainIcon size={18} aria-hidden="true" />
          <span className="brain-launcher-label">Brain</span>
        </button>
      )}
    </>
  );
}
