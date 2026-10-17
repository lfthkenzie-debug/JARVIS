"""Speech engine — microphone capture, STT, and TTS.

STT priority:
  1. OpenAI Whisper (local, if `openai-whisper` installed) — best accuracy, offline.
  2. Google Web Speech via SpeechRecognition (free, needs network).
TTS priority:
  1. Fish Audio cloud voice "JARVIS" (needs JARVIS_FISH_API_KEY — see config).
  2. macOS built-in `say` (zero-latency fallback).
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import threading
import queue as _queue
from pathlib import Path

from jarvis.config import (FISH_API_KEY, FISH_MODEL, FISH_VOICE_ID, TTS_RATE,
                           TTS_VOICE, log)
from jarvis.core.net import post_bytes


def _has_pyaudio() -> bool:
    try:
        import pyaudio  # type: ignore  # noqa: F401
        return True
    except Exception:
        return False


class Speaker:
    """Text-to-speech. Non-blocking option so JARVIS can keep thinking while talking."""

    FISH_URL = "https://api.fish.audio/v1/tts"

    def __init__(self, enabled: bool = True):
        self.fish = bool(FISH_API_KEY) and shutil.which("afplay") is not None
        self.enabled = enabled and (self.fish or shutil.which("say") is not None)
        if self.fish:
            log(f"TTS voice: Fish Audio — model {FISH_MODEL}.")
        elif not self.enabled:
            log("TTS disabled (no `say` binary) — console output only.")
        self._proc: subprocess.Popen | None = None
        self._audio_dir = Path(tempfile.mkdtemp(prefix="jarvis_tts_"))
        self._seq = 0

    def say(self, text: str, block: bool = True) -> None:
        if not text:
            return
        print(f"\x1b[1;34mJARVIS:\x1b[0m {text}")
        if not self.enabled:
            return
        text = _pre_speak_cleanup(text)
        if self.fish:
            if self._fish_say(text, block):
                return
            self.fish = False      # bad key / offline — use system voice all session
            log("Fish Audio unavailable — switched to macOS voice.")
        cmd = ["say", "-r", str(TTS_RATE), "-v", TTS_VOICE, text]
        if block:
            subprocess.run(cmd, check=False)
        else:
            self._proc = subprocess.Popen(cmd)

    def _fish_payload(self, text: str) -> dict:
        return {
            "text": text,
            "reference_id": FISH_VOICE_ID,
            "format": "mp3",
            "mp3_bitrate": 128,
            "latency": "normal",
            "normalize": True,
            "chunk_length": 300,
            "prosody": {"speed": round(TTS_RATE / 175, 3)},
        }

    def _fish_headers(self) -> dict:
        return {"Authorization": f"Bearer {FISH_API_KEY}", "model": FISH_MODEL}

    def synthesize(self, text: str) -> bytes | None:
        """Return Fish Audio MP3 bytes without playing — used by the web UI."""
        if not self.fish or not text:
            return None
        try:
            return post_bytes(self.FISH_URL, self._fish_payload(_pre_speak_cleanup(text)),
                              timeout=30, headers=self._fish_headers())
        except Exception as exc:
            log(f"Fish Audio synth error: {exc}")
            return None

    def _fish_say(self, text: str, block: bool) -> bool:
        self._seq += 1
        try:
            audio = post_bytes(self.FISH_URL, self._fish_payload(text),
                               timeout=30, headers=self._fish_headers())
        except Exception as exc:
            log(f"Fish Audio TTS error: {exc}")
            return False
        path = self._audio_dir / f"reply_{self._seq}.mp3"
        path.write_bytes(audio)
        for old in self._audio_dir.glob("reply_*.mp3"):   # keep only current clip
            if old != path:
                old.unlink(missing_ok=True)
        self._proc = subprocess.Popen(["afplay", str(path)],
                                      stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL)
        if block:
            self._proc.wait()
        return True

    def stop(self) -> None:
        for tool in ("say", "afplay"):
            subprocess.run(["killall", tool], check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _pre_speak_cleanup(text: str) -> str:
    """Strip markdown/code noise so `say` doesn't read symbols aloud."""
    for ch in ["*", "`", "#", "**"]:
        text = text.replace(ch, " ")
    return " ".join(text.split())[:900]     # `say` handles long strings poorly


class Listener:
    """Microphone -> text. Chooses the best available STT backend at startup."""

    def __init__(self, energy_threshold: int = 300):
        self.energy_threshold = energy_threshold
        self.backend = "none"
        self._sr = None
        self._recognizer = None
        self._whisper_model = None

        try:
            import whisper  # type: ignore
            self._whisper_model = whisper.load_model("base")
            self.backend = "whisper"
            log("STT backend: Whisper (local).")
            return
        except Exception:
            pass
        try:
            import speech_recognition as sr
            self._sr = sr
            self._recognizer = sr.Recognizer()
            self._recognizer.energy_threshold = energy_threshold
            self._recognizer.dynamic_energy_threshold = True
            self._recognizer.pause_threshold = 1.0
            self.backend = "google"
            log("STT backend: Google Web Speech (network).")
        except Exception as exc:
            log(f"STT unavailable: {exc}")

    # ------------------------------------------------------------ capture
    def _record(self, timeout: float, phrase_limit: float):
        """Capture one utterance. Returns (pcm_bytes, sample_rate, sample_width)."""
        if self._sr is not None and _has_pyaudio():
            sr = self._sr
            try:
                with sr.Microphone() as source:
                    self._recognizer.adjust_for_ambient_noise(source, duration=0.4)
                    audio = self._recognizer.listen(
                        source, timeout=timeout, phrase_time_limit=phrase_limit)
                return audio.get_raw_data(), audio.sample_rate, audio.sample_width
            except sr.WaitTimeoutError:
                return None
            except OSError as exc:
                log(f"Microphone error (grant Mic permission in System Settings): {exc}")
                return None
        return self._record_sounddevice(phrase_limit)

    def _record_sounddevice(self, phrase_limit: float):
        try:
            import sounddevice as sd  # type: ignore
            import numpy as np  # type: ignore
        except Exception:
            log("No audio input backend: pip install sounddevice (or pyaudio).")
            return None
        rate = 16000
        q: "queue.Queue" = _queue.Queue()

        def cb(indata, frames, time_info, status):
            q.put(indata.copy())

        try:
            with sd.InputStream(samplerate=rate, channels=1, dtype="int16",
                                callback=cb):
                chunks, elapsed, quiet = [], 0.0, 0.0
                while elapsed < phrase_limit + 6:
                    data = q.get(timeout=1)
                    chunks.append(data)
                    elapsed += len(data) / rate
                    rms = float(np.sqrt(np.mean(data.astype("float32") ** 2)))
                    quiet = quiet + 0.03 if rms < 150 else 0.0
                    if chunks and quiet > 0.8:
                        break
            audio = np.concatenate(chunks).flatten() if chunks else None
        except Exception as exc:
            log(f"Recording failed: {exc}")
            return None
        if audio is None or len(audio) < rate // 4:
            return None
        return audio.tobytes(), rate, 2

    # ------------------------------------------------------------ transcribe
    def listen(self, timeout: float = 8.0, phrase_limit: float = 12.0) -> str | None:
        """Block until one utterance is captured and transcribed (lowercased)."""
        captured = self._record(timeout, phrase_limit)
        if captured is None:
            return None
        pcm, rate, width = captured
        if self.backend == "whisper":
            try:
                import numpy as np
                frames = np.frombuffer(pcm, dtype=np.int16).astype("float32") / 32768.0
                result = self._whisper_model.transcribe(frames, fp16=False,
                                                        language="en")
                return result["text"].strip().lower()
            except Exception as exc:
                log(f"Whisper error: {exc}")
                return None
        try:
            audio = self._sr.AudioData(pcm, rate, width)
            return self._recognizer.recognize_google(audio).lower()
        except self._sr.UnknownValueError:
            return None
        except self._sr.RequestError as exc:
            log(f"STT service unreachable: {exc}")
            return None

    def listen_for_wake_word(self, stop_event: threading.Event) -> bool:
        """Lightweight wake detection: keep listening until 'jarvis' heard or stop."""
        while not stop_event.is_set():
            heard = self.listen(timeout=6, phrase_limit=4)
            if heard and "jarvis" in heard:
                return True
        return False
