"use client";

import { useEffect, useRef, useState } from "react";

function recognitionClass() {
  if (typeof window === "undefined") return null;
  return window.SpeechRecognition || window.webkitSpeechRecognition || null;
}

/**
 * Microphone button that dictates into a text field using the browser's speech recognition.
 * onText(chunk) receives each finished phrase; onStop() fires when listening ends.
 * Renders nothing in browsers without speech recognition (e.g. Firefox).
 */
export default function VoiceNoteButton({ onText, onStop, disabled }) {
  const [supported, setSupported] = useState(false);
  const [listening, setListening] = useState(false);
  const [error, setError] = useState("");
  const recRef = useRef(null);
  const handlers = useRef({ onText, onStop });
  handlers.current = { onText, onStop };

  useEffect(() => {
    setSupported(Boolean(recognitionClass()));
    return () => recRef.current?.abort();
  }, []);

  if (!supported) return null;

  function start() {
    const Recognition = recognitionClass();
    const rec = new Recognition();
    rec.continuous = true;
    rec.interimResults = false;
    rec.lang = "en-US";
    rec.onresult = (event) => {
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        if (event.results[i].isFinal) handlers.current.onText(event.results[i][0].transcript.trim());
      }
    };
    rec.onerror = (event) => {
      setError(event.error === "not-allowed" ? "Microphone access was blocked." : "Voice typing stopped.");
    };
    rec.onend = () => {
      setListening(false);
      recRef.current = null;
      handlers.current.onStop?.();
    };
    setError("");
    recRef.current = rec;
    rec.start();
    setListening(true);
  }

  return (
    <div className="voice-note">
      <button
        type="button"
        className={listening ? "primary voice-btn on" : "ghost voice-btn"}
        onClick={() => (listening ? recRef.current?.stop() : start())}
        disabled={disabled}
        aria-pressed={listening}
        title={listening ? "Stop voice typing" : "Voice type a note"}
      >
        {listening ? "● Listening… tap to stop" : "🎤 Voice type"}
      </button>
      {error ? <span className="note-state">{error}</span> : null}
    </div>
  );
}
