import { useState, useEffect, useRef, useCallback } from 'react';

/* ── Waveform bars ── */
const BARS = Array.from({ length: 20 }, (_, i) => ({
  id: i, dur: `${0.5 + Math.random() * 0.7}s`, delay: `${Math.random() * 0.5}s`,
}));

const WS_URL = import.meta.env.VITE_WS_URL || 'ws://127.0.0.1:8765/ws';

/* ── useAgentVoice hook ── */
function useAgentVoice() {
  const [phase, setPhase]         = useState('idle');
  // idle|listening|transcribing|planning|executing|confirm|done|error|clarify
  const [transcript, setTranscript] = useState('');
  const [language, setLanguage]   = useState('');
  const [statusText, setStatusText] = useState('');
  const [steps, setSteps]         = useState([]);
  const [confirmData, setConfirmData] = useState(null);
  const [richResult, setRichResult] = useState(null); // { label, text }
  const [history, setHistory]     = useState([]);
  const [errorMsg, setErrorMsg]   = useState('');

  const wsRef      = useRef(null);
  const mediaRef   = useRef(null);
  const chunksRef  = useRef([]);
  const streamRef  = useRef(null);

  /* ── WebSocket setup ── */
  const connectWS = useCallback(() => {
    if (wsRef.current?.readyState < 2) return;
    const ws = new WebSocket(WS_URL);
    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      handleServerMessage(msg);
    };
    ws.onerror = () => {
      setErrorMsg(`Cannot connect to agent server (${WS_URL}). Is it running?`);
      setPhase('error');
    };
    wsRef.current = ws;
  }, []);

  const handleServerMessage = (msg) => {
    switch (msg.type) {
      case 'transcribing':
        setPhase('transcribing');
        setStatusText('Transcribing your voice…');
        break;

      case 'transcribed':
        setTranscript(msg.text);
        setLanguage(msg.language || '');
        setHistory(prev => msg.text ? [msg.text, ...prev].slice(0, 5) : prev);
        break;

      case 'planning':
        setPhase('planning');
        setStatusText(msg.text);
        break;

      case 'plan':
        setPhase('executing');
        setSteps((msg.steps || []).map(s => ({ ...s, status: 'pending' })));
        setStatusText(msg.explanation || 'Executing…');
        break;

      case 'executing':
        setSteps(prev => prev.map((s, i) =>
          i === msg.step_index ? { ...s, status: 'running', displayText: msg.text } : s
        ));
        setStatusText(msg.text);
        break;

      case 'step_done':
        setSteps(prev => prev.map((s, i) =>
          i === msg.step_index ? { ...s, status: 'done', displayText: msg.text } : s
        ));
        break;

      case 'step_failed':
        setSteps(prev => prev.map((s, i) =>
          i === msg.step_index ? { ...s, status: 'failed', displayText: msg.text } : s
        ));
        break;

      case 'confirm':
        setPhase('confirm');
        setConfirmData({ text: msg.text, tool: msg.tool, step_index: msg.step_index });
        break;

      case 'safety_block':
        setPhase('error');
        setErrorMsg(msg.text);
        break;

      case 'clarify':
        setPhase('clarify');
        setStatusText(msg.text);
        break;

      case 'done':
        setPhase('done');
        setStatusText(msg.text);
        break;

      case 'rich_result':
        // Only show summary panel for explicit summary requests
        if (msg.label === 'Summary') {
          setRichResult({ label: msg.label, text: msg.text });
        }
        break;

      case 'error':
        setPhase('error');
        setErrorMsg(msg.text);
        break;

      case 'info':
        if ((msg.text || '').toLowerCase().includes('cancelled')) {
          setPhase('error');
          setErrorMsg(msg.text);
        } else {
          setStatusText(msg.text);
        }
        break;
    }
  };

  /* ── Send control message ── */
  const sendControl = useCallback((text) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(text);
    }
  }, []);

  const confirm = useCallback((yes) => {
    sendControl(yes ? 'confirm:yes' : 'confirm:no');
    setPhase('executing');
    setConfirmData(null);
  }, [sendControl]);

  const cancelTask = useCallback(() => {
    sendControl('cancel');
    setPhase('idle');
    setSteps([]);
    setStatusText('');
  }, [sendControl]);

  /* ── Mic control ── */
  const startListening = useCallback(async () => {
    setTranscript('');
    setLanguage('');
    setErrorMsg('');
    setSteps([]);
    setStatusText('');
    setRichResult(null);
    setPhase('listening');
    connectWS();

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      const mime = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg', 'audio/mp4']
        .find(t => MediaRecorder.isTypeSupported(t)) || '';
      const recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : {});
      chunksRef.current = [];
      recorder.ondataavailable = (e) => { if (e.data.size > 0) chunksRef.current.push(e.data); };
      recorder.onstop = async () => {
        // Final chunk may arrive after onstop in Electron — wait briefly
        await new Promise(r => setTimeout(r, 150));
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType || mime || 'audio/webm' });
        if (blob.size === 0) {
          setErrorMsg('No audio captured — try holding the mic button a little longer.');
          setPhase('error');
          streamRef.current?.getTracks().forEach(t => t.stop());
          return;
        }
        const buf  = await blob.arrayBuffer();
        const send = () => {
          if (wsRef.current?.readyState === WebSocket.OPEN) wsRef.current.send(buf);
          else setTimeout(send, 100);
        };
        send();
        streamRef.current?.getTracks().forEach(t => t.stop());
      };
      mediaRef.current = recorder;
      // Single blob on stop — timesliced chunks break mp4/webm in Electron
      recorder.start();
    } catch (err) {
      setErrorMsg('Microphone access denied: ' + err.message);
      setPhase('error');
    }
  }, [connectWS]);

  const stopListening = useCallback(() => {
    if (mediaRef.current?.state === 'recording') {
      mediaRef.current.requestData();
      mediaRef.current.stop();
      setPhase('transcribing');
    }
  }, []);

  const toggle = useCallback(() => {
    if (phase === 'listening')                  stopListening();
    else if (['idle','done','error'].includes(phase)) startListening();
  }, [phase, startListening, stopListening]);

  // Space bar shortcut
  useEffect(() => {
    const h = (e) => { if (e.code === 'Space' && e.target === document.body) { e.preventDefault(); toggle(); } };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [toggle]);

  // Auto-idle after done/error
  useEffect(() => {
    if (!['done','error','clarify'].includes(phase)) return;
    const t = setTimeout(() => { setPhase('idle'); setSteps([]); }, 10000);
    return () => clearTimeout(t);
  }, [phase]);

  return { phase, transcript, language, statusText, steps, confirmData, richResult, history, errorMsg, toggle, confirm, cancelTask, sendControl };
}

/* ── Icon components ── */
const MicIcon = () => (
  <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor"
       strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="mic-icon">
    <rect x="9" y="2" width="6" height="12" rx="3" fill="currentColor" stroke="none" opacity="0.9"/>
    <path d="M5 10a7 7 0 0 0 14 0"/>
    <line x1="12" y1="19" x2="12" y2="22"/>
    <line x1="8" y1="22" x2="16" y2="22"/>
  </svg>
);

const CheckIcon = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#34d399" strokeWidth="2.5" strokeLinecap="round">
    <polyline points="20 6 9 17 4 12"/>
  </svg>
);

const SpinnerIcon = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#a78bfa" strokeWidth="2.5" strokeLinecap="round" style={{animation:'spin 1s linear infinite'}}>
    <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4"/>
  </svg>
);

const XIcon = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#f87171" strokeWidth="2.5" strokeLinecap="round">
    <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
  </svg>
);

const LANG_FLAGS = { en:'🇬🇧', hi:'🇮🇳', es:'🇪🇸', fr:'🇫🇷', de:'🇩🇪', zh:'🇨🇳', ja:'🇯🇵', ko:'🇰🇷' };

/* ── Step status icon ── */
const StepIcon = ({ status }) => {
  if (status === 'done')    return <CheckIcon />;
  if (status === 'failed')  return <XIcon />;
  if (status === 'running') return <SpinnerIcon />;
  return <div style={{width:14,height:14,borderRadius:'50%',border:'1.5px solid rgba(255,255,255,0.15)'}} />;
};

/* ── Status label per phase ── */
const PHASE_LABEL = {
  idle:         'Ready',
  listening:    'Listening',
  transcribing: 'Transcribing',
  planning:     'Planning',
  executing:    'Executing',
  confirm:      'Confirm',
  clarify:      'Clarifying',
  done:         'Done ✓',
  error:        'Error',
};

/* ── App ── */
export default function App() {
  const { phase, transcript, language, statusText, steps, confirmData, richResult, history, errorMsg, toggle, confirm, cancelTask } = useAgentVoice();

  const isListening  = phase === 'listening';
  const isProcessing = ['transcribing','planning','executing'].includes(phase);
  const isDone       = phase === 'done';
  const isError      = phase === 'error';
  const isClarify    = phase === 'clarify';
  const isConfirm    = phase === 'confirm';
  const showSummary = richResult?.label === 'Summary';
  const showCard     = !!transcript || steps.length > 0 || isProcessing || isDone || isError || isClarify;

  return (
    <div className={`app ${showSummary ? 'has-summary' : ''}`}>
      <div className="bg-orb bg-orb-1" />
      <div className="bg-orb bg-orb-2" />
      <div className="bg-orb bg-orb-3" />

      {/* Confirmation modal */}
      {isConfirm && confirmData && (
        <div className="confirm-overlay">
          <div className="confirm-modal">
            <div className="confirm-icon">⚠</div>
            <div className="confirm-text">{confirmData.text}</div>
            <div className="confirm-actions">
              <button id="confirm-yes-btn" className="confirm-btn confirm-yes" onClick={() => confirm(true)}>Yes, do it</button>
              <button id="confirm-no-btn" className="confirm-btn confirm-no"  onClick={() => confirm(false)}>Skip</button>
            </div>
          </div>
        </div>
      )}

      <div className="app-layout">
      <div className="content">
        {/* Brand */}
        <div className="brand">
          <div className="brand-icon">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
              <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z" fill="white"/>
              <path d="M19 10v2a7 7 0 0 1-14 0v-2" stroke="white" strokeWidth="2"/>
              <line x1="12" y1="19" x2="12" y2="22" stroke="white" strokeWidth="2"/>
              <line x1="8" y1="22" x2="16" y2="22" stroke="white" strokeWidth="2"/>
            </svg>
          </div>
          <span className="brand-name">IntelliVox</span>
        </div>

        {/* Mic stage */}
        <div className="mic-stage">
          <div className={`mic-rings ${isListening ? 'active' : ''}`}>
            <div className="ring ring-1" />
            <div className="ring ring-2" />
            <button
              id="mic-toggle-btn"
              className={`mic-btn ${isListening ? 'active' : ''} ${isError ? 'err' : ''}`}
              onClick={toggle}
              disabled={isProcessing || isConfirm}
              aria-label={isListening ? 'Stop' : 'Start'}
              aria-pressed={isListening}
            >
              {isProcessing ? (
                <div className="thinking-dots" style={{display:'flex',alignItems:'center'}}><span/><span/><span/></div>
              ) : (
                <MicIcon />
              )}
            </button>
          </div>

          <div className={`waveform ${isListening ? 'active' : ''}`} aria-hidden="true">
            {BARS.map(b => <div key={b.id} className="bar" style={{'--dur':b.dur,'--delay':b.delay}} />)}
          </div>
        </div>

        {/* Status */}
        <div className="status-wrap">
          <span className={`status-label ${isListening ? 'active' : ''} ${isError ? 'status-error' : ''}`}>
            {PHASE_LABEL[phase] || phase}
          </span>
          <div className={`status-text ${isProcessing ? 'thinking' : ''} ${isError ? 'error-text' : ''}`}>
            {isError ? errorMsg : isClarify ? statusText : statusText || (phase === 'idle' ? 'Tap the mic or press Space · English only' : '')}
          </div>
        </div>

        {/* Main card */}
        <div className={`transcript-card ${showCard ? 'visible' : ''}`}>
          {/* Transcript */}
          {transcript && (
            <div style={{marginBottom: steps.length ? 16 : 0}}>
              <div className="transcript-label">
                <div className="transcript-dot" style={{background: isListening ? '#06b6d4' : '#a78bfa'}} />
                You said
                {(language === 'en' || !language) && (
                  <span style={{marginLeft:'auto',fontSize:'0.8em',opacity:0.7}}>
                    🇺🇸 English
                  </span>
                )}
                {language && language !== 'en' && (
                  <span style={{marginLeft:'auto',fontSize:'0.8em'}}>
                    {LANG_FLAGS[language] || '🌐'} {language}
                  </span>
                )}
              </div>
              <div className="transcript-text">{transcript}</div>
            </div>
          )}

          {/* Step list */}
          {steps.length > 0 && (
            <div className="steps-list">
              {steps.map((step, i) => (
                <div key={i} className={`step-item step-${step.status}`}>
                  <StepIcon status={step.status} />
                  <span className="step-text">{step.displayText || step.tool}</span>
                </div>
              ))}
            </div>
          )}

          {/* Done response */}
          {isDone && (
            <div className="response-pill visible" style={{marginTop: steps.length ? 12 : 0}}>
              <div className="avatar">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="white">
                  <path d="M12 2a5 5 0 1 1 0 10A5 5 0 0 1 12 2zm0 12c5.33 0 8 2.67 8 4v2H4v-2c0-1.33 2.67-4 8-4z"/>
                </svg>
              </div>
              <span>{statusText}</span>
            </div>
          )}

          {/* Processing indicator */}
          {isProcessing && !steps.length && (
            <div className="transcript-label" style={{justifyContent:'center'}}>
              <div className="transcript-dot" />
              {statusText || 'Working…'}
            </div>
          )}
        </div>

        {/* Controls row */}
        <div className="controls-row">
          <div className="hint">
            <span className="kbd">Space</span>
            <span>to {isListening ? 'stop' : 'speak'}</span>
          </div>
          {isProcessing && (
            <button id="cancel-btn" className="cancel-btn" onClick={cancelTask}>Cancel</button>
          )}
        </div>
      </div>

      {/* Summary panel — right side, only when user asked for a summary */}
      {showSummary && (
        <aside className="summary-panel">
          <div className="summary-panel-header">
            <span className="summary-panel-title">Summary</span>
          </div>
          <div className="summary-panel-body">
            {richResult.text.split('\n').map((line, i) => (
              <p key={i} className="summary-line">
                {line}
              </p>
            ))}
          </div>
        </aside>
      )}
      </div>

      {/* History */}
      {history.length > 0 && (
        <div className="history-tray" role="list">
          {history.map((cmd, i) => (
            <div key={i} className="history-chip" title={cmd}>{cmd}</div>
          ))}
        </div>
      )}

      <style>{`
        @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }

        .confirm-overlay {
          position: fixed; inset: 0; z-index: 100;
          background: rgba(6,6,18,0.8);
          backdrop-filter: blur(12px);
          display: flex; align-items: center; justify-content: center;
          animation: fadeIn 0.2s ease;
        }
        @keyframes fadeIn { from { opacity:0 } to { opacity:1 } }

        .confirm-modal {
          background: rgba(20,12,50,0.95);
          border: 1px solid rgba(124,58,237,0.4);
          border-radius: 20px;
          padding: 32px 36px;
          max-width: 380px;
          width: 90%;
          text-align: center;
          box-shadow: 0 0 80px rgba(124,58,237,0.3);
        }
        .confirm-icon { font-size: 2rem; margin-bottom: 12px; }
        .confirm-text {
          font-family: var(--font-ui);
          font-size: 0.95rem;
          color: var(--text-primary);
          line-height: 1.6;
          margin-bottom: 24px;
        }
        .confirm-actions { display: flex; gap: 12px; justify-content: center; }
        .confirm-btn {
          font-family: var(--font-ui);
          font-size: 0.88rem;
          font-weight: 600;
          padding: 10px 24px;
          border-radius: 50px;
          border: none;
          cursor: pointer;
          transition: all 0.2s;
        }
        .confirm-yes {
          background: linear-gradient(135deg, #7c3aed, #2563eb);
          color: white;
        }
        .confirm-yes:hover { transform: scale(1.04); box-shadow: 0 0 20px rgba(124,58,237,0.5); }
        .confirm-no {
          background: rgba(255,255,255,0.06);
          color: var(--text-secondary);
          border: 1px solid rgba(255,255,255,0.1);
        }
        .confirm-no:hover { background: rgba(255,255,255,0.1); }

        .steps-list {
          display: flex; flex-direction: column; gap: 8px;
          margin-top: 4px;
        }
        .step-item {
          display: flex; align-items: center; gap: 10px;
          padding: 8px 12px;
          border-radius: 10px;
          background: rgba(255,255,255,0.03);
          border: 1px solid rgba(255,255,255,0.05);
          transition: all 0.3s;
        }
        .step-running {
          background: rgba(124,58,237,0.08);
          border-color: rgba(124,58,237,0.2);
        }
        .step-done {
          background: rgba(52,211,153,0.05);
          border-color: rgba(52,211,153,0.15);
        }
        .step-failed {
          background: rgba(248,113,113,0.05);
          border-color: rgba(248,113,113,0.15);
        }
        .step-text {
          font-size: 0.82rem;
          color: var(--text-secondary);
          font-family: var(--font-ui);
        }
        .step-running .step-text { color: #c4b5fd; }
        .step-done    .step-text { color: #6ee7b7; }
        .step-failed  .step-text { color: #fca5a5; }

        .controls-row {
          display: flex; align-items: center; gap: 16px; margin-top: 20px;
        }
        .cancel-btn {
          font-family: var(--font-ui);
          font-size: 0.75rem;
          font-weight: 600;
          color: #f87171;
          background: rgba(248,113,113,0.08);
          border: 1px solid rgba(248,113,113,0.2);
          border-radius: 50px;
          padding: 5px 14px;
          cursor: pointer;
          transition: all 0.2s;
        }
        .cancel-btn:hover { background: rgba(248,113,113,0.15); }

        .mic-btn:disabled { cursor: not-allowed; opacity: 0.6; }
      `}</style>
    </div>
  );
}
