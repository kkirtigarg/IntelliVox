import { useState, useEffect, useRef, useCallback } from 'react';

/* ── Waveform bars ── */
const BARS = Array.from({ length: 20 }, (_, i) => ({
  id: i,
  dur: `${0.5 + Math.random() * 0.7}s`,
  delay: `${Math.random() * 0.5}s`,
}));

const WS_URL = 'ws://localhost:8765/ws';

/* ── useWhisperVoice hook ────────────────────────────────────────────────── */
function useWhisperVoice() {
  const [state, setState] = useState('idle');
  // idle | connecting | listening | processing | done | error
  const [transcript, setTranscript] = useState('');
  const [language, setLanguage]     = useState('');
  const [errorMsg, setErrorMsg]     = useState('');
  const [history, setHistory]       = useState([]);

  const wsRef       = useRef(null);
  const mediaRef    = useRef(null);   // MediaRecorder
  const chunksRef   = useRef([]);
  const streamRef   = useRef(null);

  /* open WebSocket once */
  const connectWS = useCallback(() => {
    if (wsRef.current && wsRef.current.readyState < 2) return; // already open/connecting
    const ws = new WebSocket(WS_URL);
    ws.binaryType = 'arraybuffer';

    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (msg.status === 'transcribing') {
        setState('processing');
      } else if (msg.status === 'done') {
        setTranscript(msg.transcript || '(no speech detected)');
        setLanguage(msg.language || '');
        setHistory((prev) => {
          const t = msg.transcript?.trim();
          if (!t) return prev;
          return [t, ...prev].slice(0, 6);
        });
        setState('done');
      } else if (msg.status === 'error') {
        setErrorMsg(msg.message || 'Unknown error');
        setState('error');
      }
    };

    ws.onerror = () => {
      setErrorMsg('Cannot connect to Whisper server (ws://localhost:8765). Is it running?');
      setState('error');
    };

    wsRef.current = ws;
  }, []);

  /* start recording */
  const startListening = useCallback(async () => {
    setTranscript('');
    setLanguage('');
    setErrorMsg('');
    setState('connecting');

    connectWS();

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;

      // Pick the best supported mime type
      const mime = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg'].find(
        (t) => MediaRecorder.isTypeSupported(t)
      ) || '';

      const recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : {});
      chunksRef.current = [];

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };

      recorder.onstop = async () => {
        const blob = new Blob(chunksRef.current, { type: mime || 'audio/webm' });
        const buf  = await blob.arrayBuffer();

        // Wait until WS is open (it may still be connecting)
        const send = () => {
          if (wsRef.current?.readyState === WebSocket.OPEN) {
            wsRef.current.send(buf);
          } else {
            setTimeout(send, 100);
          }
        };
        send();

        // stop mic tracks
        streamRef.current?.getTracks().forEach((t) => t.stop());
      };

      mediaRef.current = recorder;
      recorder.start(250); // collect chunks every 250ms
      setState('listening');
    } catch (err) {
      setErrorMsg('Microphone access denied: ' + err.message);
      setState('error');
    }
  }, [connectWS]);

  /* stop recording → triggers onstop → sends to WS */
  const stopListening = useCallback(() => {
    if (mediaRef.current?.state === 'recording') {
      mediaRef.current.stop();
      setState('processing');
    }
  }, []);

  const toggle = useCallback(() => {
    if (state === 'listening') {
      stopListening();
    } else if (state === 'idle' || state === 'done' || state === 'error') {
      startListening();
    }
  }, [state, startListening, stopListening]);

  /* auto reset after showing result */
  useEffect(() => {
    if (state !== 'done' && state !== 'error') return;
    const t = setTimeout(() => setState('idle'), 8000);
    return () => clearTimeout(t);
  }, [state]);

  /* Space-bar shortcut */
  useEffect(() => {
    const handler = (e) => {
      if (e.code === 'Space' && e.target === document.body) {
        e.preventDefault();
        toggle();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [toggle]);

  return { state, transcript, language, errorMsg, history, toggle };
}

/* ── Icons ── */
const MicIcon = () => (
  <svg width="52" height="52" viewBox="0 0 24 24" fill="none" stroke="currentColor"
       strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="mic-icon">
    <rect x="9" y="2" width="6" height="12" rx="3" fill="currentColor" stroke="none" opacity="0.9"/>
    <path d="M5 10a7 7 0 0 0 14 0"/>
    <line x1="12" y1="19" x2="12" y2="22"/>
    <line x1="8" y1="22" x2="16" y2="22"/>
  </svg>
);

const AgentIcon = () => (
  <svg width="12" height="12" viewBox="0 0 24 24" fill="white">
    <path d="M12 2a5 5 0 1 1 0 10A5 5 0 0 1 12 2zm0 12c5.33 0 8 2.67 8 4v2H4v-2c0-1.33 2.67-4 8-4z"/>
  </svg>
);

const FlagIcon = ({ lang }) => {
  const flags = { en:'🇬🇧', hi:'🇮🇳', es:'🇪🇸', fr:'🇫🇷', de:'🇩🇪', zh:'🇨🇳', ja:'🇯🇵', ko:'🇰🇷', ar:'🇸🇦', pt:'🇧🇷' };
  return <span style={{ fontSize:'1.1em' }}>{flags[lang] || '🌐'}</span>;
};

/* ── Status copy ── */
const STATUS_COPY = {
  idle:        { label: 'Ready',        text: 'Tap the mic or press Space to speak' },
  connecting:  { label: 'Connecting…',  text: 'Connecting to Whisper server…' },
  listening:   { label: 'Listening',    text: 'Speak now — tap again to stop' },
  processing:  { label: 'Transcribing', text: 'Running Whisper…' },
  done:        { label: 'Done ✓',       text: null },
  error:       { label: 'Error',        text: null },
};

/* ── App ── */
export default function App() {
  const { state, transcript, language, errorMsg, history, toggle } = useWhisperVoice();

  const isActive     = state === 'listening';
  const isProcessing = state === 'processing' || state === 'connecting';
  const isDone       = state === 'done';
  const isError      = state === 'error';
  const showCard     = isDone || isError || isProcessing;

  return (
    <div className="app">
      {/* Background orbs */}
      <div className="bg-orb bg-orb-1" />
      <div className="bg-orb bg-orb-2" />
      <div className="bg-orb bg-orb-3" />

      <div className="content">
        {/* Brand */}
        <div className="brand">
          <div className="brand-icon">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z" fill="white" stroke="none"/>
              <path d="M19 10v2a7 7 0 0 1-14 0v-2" stroke="white" strokeWidth="2"/>
              <line x1="12" y1="19" x2="12" y2="22" stroke="white" strokeWidth="2"/>
              <line x1="8" y1="22" x2="16" y2="22" stroke="white" strokeWidth="2"/>
            </svg>
          </div>
          <span className="brand-name">IntelliVox</span>
        </div>

        {/* Mic button */}
        <div className="mic-stage">
          <div className={`mic-rings ${isActive ? 'active' : ''}`}>
            <div className="ring ring-1" />
            <div className="ring ring-2" />

            <button
              id="mic-toggle-btn"
              className={`mic-btn ${isActive ? 'active' : ''} ${isError ? 'err' : ''}`}
              onClick={toggle}
              aria-label={isActive ? 'Stop listening' : 'Start listening'}
              aria-pressed={isActive}
            >
              {isProcessing ? (
                <div className="thinking-dots" style={{ display:'flex', alignItems:'center' }}>
                  <span /><span /><span />
                </div>
              ) : (
                <MicIcon />
              )}
            </button>
          </div>

          {/* Waveform */}
          <div className={`waveform ${isActive ? 'active' : ''}`} aria-hidden="true">
            {BARS.map((b) => (
              <div key={b.id} className="bar" style={{ '--dur': b.dur, '--delay': b.delay }} />
            ))}
          </div>
        </div>

        {/* Status */}
        <div className="status-wrap">
          <span className={`status-label ${isActive ? 'active' : ''} ${isError ? 'status-error' : ''}`}>
            {STATUS_COPY[state]?.label}
          </span>
          <div className={`status-text ${isProcessing ? 'thinking' : ''} ${isError ? 'error-text' : ''}`}>
            {isError
              ? errorMsg
              : STATUS_COPY[state]?.text
                ? STATUS_COPY[state].text
                : ''}
          </div>
        </div>

        {/* Transcript card */}
        <div className={`transcript-card ${showCard ? 'visible' : ''}`}>
          {isProcessing && (
            <div className="transcript-label" style={{ justifyContent:'center' }}>
              <div className="transcript-dot" />
              Running faster-whisper…
            </div>
          )}

          {isDone && transcript && (
            <>
              <div className="transcript-label">
                <div className="transcript-dot" />
                You said
                {language && (
                  <span style={{ marginLeft:'auto', display:'flex', alignItems:'center', gap:'4px' }}>
                    <FlagIcon lang={language} />
                    <span style={{ fontSize:'0.68rem', color:'var(--text-muted)', textTransform:'none', letterSpacing:0 }}>{language}</span>
                  </span>
                )}
              </div>
              <div className="transcript-text">{transcript}</div>

              <div className={`response-pill visible`}>
                <div className="avatar"><AgentIcon /></div>
                <span>Command received — ready to execute.</span>
              </div>
            </>
          )}

          {isError && (
            <div className="transcript-label" style={{ color:'#f87171' }}>
              ⚠ {errorMsg}
            </div>
          )}
        </div>

        {/* Keyboard hint */}
        <div className="hint">
          <span className="kbd">Space</span>
          <span>or tap mic to {isActive ? 'stop' : 'start'}</span>
          {!isActive && (
            <>
              <span style={{ margin:'0 4px', color:'var(--text-muted)' }}>·</span>
              <span style={{ fontSize:'0.7rem', color:'var(--text-muted)' }}>
                server: ws://localhost:8765
              </span>
            </>
          )}
        </div>
      </div>

      {/* History chips */}
      {history.length > 0 && (
        <div className="history-tray" role="list" aria-label="Recent commands">
          {history.map((cmd, i) => (
            <div key={i} className="history-chip" role="listitem" title={cmd}>{cmd}</div>
          ))}
        </div>
      )}
    </div>
  );
}
