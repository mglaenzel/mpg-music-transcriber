# Music Transcriber — Architektur

Ziel: klang.io-artige Web-App. MP3/Audio hochladen → automatische Trennung in
Instrumentalspuren → Transkription jeder Spur zu Noten → Anzeige im Browser
(Notenbild + synchrone Wiedergabe, original + synthetisiert) → Export als
MusicXML (MuseScore-kompatibel), MIDI und PDF (wahlweise einzelnes Instrument
oder Gesamtpartitur).

## Entscheidungen (Stand 2026-09-26)

- **Transkriptions-Engine:** Hybrid. Start mit Open-Source-Pipeline (lokal,
  auf Apple-Silicon-GPU über MPS, später Linux/Docker mit optionaler
  NVIDIA-GPU). Die Pipeline ist hinter einem `TranscriptionProvider`-Interface
  gekapselt, damit später ein kommerzieller API-Provider (z.B. klang.io API)
  als Alternative eingesteckt werden kann, ohne den Rest der App zu ändern.
- **OpenRouter/LLMs:** Kein Bestandteil der Kern-Transkription (MIR-Aufgabe,
  keine LLM-Aufgabe). Vorgemerkt als optionaler späterer Baustein für
  Zusatzfunktionen (Titel-/Genre-Vorschläge, Chat-Hilfe) — noch nicht
  implementiert, kein Blocker für den Kern.
- **MVP-Umfang:** Volles Mehrspur-Set wie im klang.io-Screenshot: Piano,
  Gitarre, Bass, Drums, Vocals, Strings, Winds (so weit die jeweilige
  Quelltrennung sie liefert). Qualität pro Instrument wird iterativ verbessert.
- **Stack:** Backend Python/FastAPI (Audio-/ML-Ökosystem ist fast
  ausschließlich Python), Frontend React/TypeScript (Vite) mit
  OpenSheetMusicDisplay (OSMD) für MusicXML-Rendering + Audio-Sync. Beides in
  Docker-Containern, `docker-compose` für lokale Entwicklung auf dem Mac,
  gleiches Setup später auf Linux.

## Pipeline (pro Upload-Job)

1. **Ingest** — Upload (MP3/WAV/MP4/FLAC …), Normalisierung nach WAV 44.1kHz.
2. **Source Separation** — Demucs (`htdemucs`, Meta/Facebook Research, MIT-ish
   Lizenz) trennt in Stems: `vocals`, `drums`, `bass`, `other`. Für
   feinere Trennung (Gitarre vs. Klavier vs. Streicher vs. Bläser innerhalb
   von `other`) zusätzliche Klassifikations-/Trennschritte (Stufe 2,
   iterativ — startet mit `other` als "Sonstige Melodieinstrumente").
3. **Tempo/Key/Time-Signature-Erkennung** — librosa (Onset-/Beat-Tracking) auf
   dem Gesamtmix. BPM als Ganzzahl, Mapping auf italienische
   Tempobezeichnung (siehe `backend/app/pipeline/tempo.py`).
4. **Transkription pro Stem:**
   - Tonhöhen-Instrumente (Vocals, Bass, Other/Piano/Guitar/Strings/Winds):
     Basic Pitch (Spotify, Open Source, ONNX/TF-Modell) → Noten-Events
     (Pitch, Onset, Offset, Velocity).
   - Drums: Onset-Detection + Perkussions-Klassifikation → General-MIDI-
     Drum-Mapping (Kick/Snare/HiHat/Toms/Cymbals).
5. **Notation-Aufbereitung** — music21: Quantisierung auf Notenraster
   (abhängig vom erkannten Takt/Tempo), Taktart, Tonart, Instrument-Zuordnung
   zu Notensystemen (Klaviatur-Systeme für Piano, Bassschlüssel für Bass/
   Cello, etc.).
6. **Export:**
   - MusicXML (unkomprimiert `.musicxml` und komprimiert `.mxl`) — öffnet
     direkt in MuseScore.
   - MIDI (`.mid`) via music21/mido.
   - PDF — music21 exportiert MusicXML, MuseScore (Kommandozeile, im
     Docker-Image mitinstalliert) rendert PDF. Für "nur Instrument X" wird
     vorher eine gefilterte MusicXML-Datei erzeugt (nur die gewählten Parts)
     und diese gerendert.
7. **Bereitstellung** — Ergebnisse (Stems-Audio, Noten-JSON pro Spur für das
   Frontend, MusicXML/MIDI/PDF-Dateien) landen im Job-Storage, Frontend pollt/
   lädt den Job-Status.

## Frontend

- **Upload-Seite** (angelehnt an Screenshot 1): Drag&Drop MP3, Fortschrittsanzeige
  pro Pipeline-Schritt (Trennung → Transkription → Notensatz → Export).
- **Score-Seite** (angelehnt an Screenshot 2):
  - OSMD-Notenansicht, pro Instrument ein System, ein-/ausblendbar.
  - Tempo-Anzeige: `♩ = <BPM>` **plus** italienischer Begriff (z.B. „Andante").
  - Transport: Play/Pause/Seek, Umschalter „Original-MP3" vs. „Synth.
    Wiedergabe" (Original-Audio synchron zum Cursor, Synth über
    Tone.js/soundfont aus den transkribierten Noten).
  - Noteneditor-Tab (später, wie im Screenshot „Noteneditor").
  - Download-Menü: MusicXML, MIDI, PDF — bei PDF Auswahl „Alle Instrumente"
    oder Einzelauswahl.

## Deployment

- Lokal: `docker-compose up` — Backend läuft im Container nur auf CPU;
  Docker Desktop auf Apple Silicon kann die Mac-GPU nicht an Linux-
  Container durchreichen (Docker-Desktop-Einschränkung). Für GPU-
  beschleunigte lokale Tests (MPS/CoreML) läuft das Backend stattdessen
  nativ — verifiziertes Setup, gemessene Laufzeiten und bekannte
  Einschränkungen siehe README, Abschnitt "Variante B".
- Produktion: gleiches Image auf Linux, dort NVIDIA-GPU via `--gpus` möglich.
- Konfiguration über `.env` (Provider-Auswahl, Storage-Pfade, ggf. später
  API-Keys für kommerziellen Transkriptions-Provider oder OpenRouter).

## Offene Punkte / nächste Iterationsschritte

- Feinere Instrumenttrennung innerhalb von `other` (Klavier vs. Gitarre vs.
  Streicher vs. Bläser) — Kandidaten: weitere Demucs-Fine-Tunes oder
  Klassifikations-Modell auf Basic-Pitch-Output.
- Asynchrone Job-Verarbeitung (aktuell einfache Background-Tasks in FastAPI;
  bei Bedarf später Celery/Redis-Queue für Skalierung).
- Noteneditor (Notenwerte manuell korrigieren) — Phase 2.
- Optionaler kommerzieller Transkriptions-Provider als Plug-in.
