# Music Transcriber

Audio hochladen → automatische Trennung in Instrumentalspuren →
Notentranskription → Anzeige im Browser (synchron zum Original-Audio) →
Export als MusicXML, MIDI und PDF. Siehe [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
für die vollständige Architektur- und Entscheidungsübersicht.

## Lokale Entwicklung

### Variante A — Docker Compose (empfohlen für schnellen Start / Linux-Parität)

```bash
docker compose up --build
```

- Frontend: http://localhost:5174
- Backend: http://localhost:8000/api/health

Hinweis: Docker Desktop auf Apple Silicon reicht die GPU nicht an Container
durch. Im Container läuft die Pipeline also auf CPU (`DEVICE=cpu` in
`docker-compose.yml`) — funktioniert, ist aber langsamer als nativ.

**Wichtig, da dieses Projekt in einem OneDrive-Ordner liegt:** Docker Desktop
auf macOS hängt sich zuverlässig auf, wenn man Verzeichnisse *aus* einem
OneDrive-/iCloud-Cloud-Storage-Pfad in einen Container bind-mounted (der
Cloud-Dateiprovider blockiert die Dateisystem-Synchronisation mit Docker's
VirtioFS). Deshalb mounten `backend/app` und `frontend/src` hier **nicht**
live in die Container — der Code wird beim Build fest ins Image kopiert.
Nach Codeänderungen im Backend/Frontend also neu bauen:

```bash
docker compose build backend   # bzw. frontend
docker compose up -d backend
```

Für zügiges Iterieren ist Variante B (nativ) angenehmer, oder das Projekt in
ein lokales, nicht-synchronisiertes Verzeichnis verschieben (z.B. `~/dev/...`)
und von dort mit `docker compose up --watch`/Bind-Mounts arbeiten.

### Variante B — Backend nativ auf dem Mac (GPU-beschleunigt, kein Docker)

Läuft direkt mit Apple-GPU-Beschleunigung: PyTorch/Demucs über **MPS**
(Metal), Basic Pitch über **CoreML** (Apple Neural Engine/GPU). Kein
Homebrew/sudo nötig — [uv](https://docs.astral.sh/uv/) lädt eine portable
Python-3.11-Distribution und verwaltet das venv:

```bash
cd backend
uv venv --python 3.11 .venv
uv pip install -p .venv/bin/python -r requirements.txt -r requirements-native-macos.txt
./run_native.sh
```

`run_native.sh` setzt `DEVICE=mps`, `TRANSCRIPTION_PROVIDER=local` und
startet auf **Port 8001** (bewusst nicht 8000, damit es nicht mit einem
parallel laufenden Docker-Backend kollidiert — beide können gleichzeitig
laufen).

**Wichtig:** Ruft man `http://localhost:8001` direkt im Browser auf,
kommt `{"detail":"Not Found"}` — das ist korrekt, das Backend ist eine
reine JSON-API ohne Seite unter `/` (Health-Check: `/api/health`,
interaktive API-Docs: `/api/docs` bzw. `/docs`). Die eigentliche
Web-Oberfläche ist das separate Frontend, das gegen diese API spricht.
Um sie gegen das *native* (statt das Docker-)Backend laufen zu lassen:

```bash
docker run --rm -d --name mpg-frontend-native -p 5175:5174 \
  -e VITE_API_BASE_URL=http://localhost:8001 \
  mpgmusic-trancriber-frontend
```

Dann `http://localhost:5175` öffnen — **nicht** `host.docker.internal`
als API-URL verwenden: die im Frontend gebündelte JS läuft im Browser auf
dem Host, nicht im Container, muss das native Backend also ganz normal
über `localhost` erreichen. `host.docker.internal` ist nur für
Container-zu-Host-Verbindungen relevant, nicht für den Browser selbst.
Container stoppen mit `docker stop mpg-frontend-native`.

MP3-Dekodierung läuft über `soundfile`/`libsndfile` (seit 1.1 mit
eingebauter MP3-Unterstützung) — **kein separates ffmpeg nötig**. Für
PDF-Export wird MuseScore 4 genutzt, falls unter
`/Applications/MuseScore 4.app` installiert (Pfad in `run_native.sh`
anpassen, falls es woanders liegt oder eine andere Version ist).

**Gemessene Beschleunigung** (2026-09-27, `jingle.bells.mp3`, ~111s,
Apple-Silicon-GPU via MPS/CoreML, kompletter Durchlauf über die echte
HTTP-API inkl. 6-Stem-Trennung + Transkription + Notensatz + Export):
**18 Sekunden**, gegenüber mehreren Minuten im Docker-Container auf CPU
(`DEVICE=cpu`, keine GPU-Passthrough auf Apple Silicon möglich, siehe
Variante A).

**Bekannte Einschränkung — MuseScore 4 CLI:** In dieser
Automatisierungsumgebung (Bash-Tool ohne eingeloggte GUI-Session) stürzt
`mscore -o ...pdf ...` beim PDF-Export ab (`std::system_error: mutex lock
failed`) — ein bekanntes Verhalten von Qt/Cocoa-GUI-Apps ohne echte
Fensterserver-Session, kein Bug in unserer Pipeline. In einem normalen
Terminal (eingeloggte GUI-Session) sollte es funktionieren; falls nicht,
einfach für den PDF-Export auf Variante A (Docker) zurückfallen — MusicXML/
MIDI/Notenanzeige/Cursor-Sync sind davon nicht betroffen und liefen im Test
einwandfrei.

**Hinweis für spätere Codeänderungen:** `run_native.sh` startet bewusst
*ohne* `--reload` — mit `--reload` löste OneDrives kontinuierliche
Synchronisation (Dateizugriffe sogar innerhalb von `.venv/`) ständige
Neustarts aus, die laufende Jobs killten (In-Memory-Job-Store weg). Nach
Codeänderungen das Skript manuell neu starten.

Frontend separat starten:

```bash
cd frontend
npm install
npm run dev
```

## Status

**Erster End-to-End-Durchlauf erfolgreich verifiziert** (2026-09-26, via
Docker Compose, synthetische Test-WAV mit Akkorden/Bass/Kick bei 100 BPM):
Upload → Demucs-Trennung → Basic-Pitch- bzw. Onset-basierte Drum-Transkription
→ music21-Notensatz → MusicXML/MIDI/PDF-Export (inkl. Instrument-Filter) →
Frontend-Anzeige mit OSMD, Tempo/italienischer Bezeichnung, Instrument-
Ein/Ausblenden und Audio-Cursor-Sync. Alle Downloads (MusicXML, MIDI, PDF
komplett + gefiltert, Stem-Audio, Track-MusicXML, Quell-Audio) liefern
gültige Dateien.

Dabei behobene Bugs: ungültiges `onset_detect`-Argument in der
Drum-Transkription; nicht quantisierte Notendauern führten zu
"inexpressible duration"-Fehlern beim MusicXML-Export (jetzt auf
16tel-Raster quantisiert); MuseScore-PDF-Rendering crashte im Container
mangels Display (`QT_QPA_PLATFORM=offscreen` behebt das); alle Stimmen
standen fälschlich im Bassschlüssel und trugen kryptische
Instrumentkürzel (Notation setzt jetzt passende Schlüssel + eigene
Instrumentnamen).

Bekannte Einschränkung: Die BPM-Erkennung (`librosa.beat.beat_track`) kann
bei Testsignalen mit dünnem Rhythmusgerüst eine Oktav-Ambiguität zeigen
(z.B. 50 statt 100 BPM erkannt, wenn Perkussion nur auf jedem zweiten
Schlag liegt) — betrifft nur die Tempo-Schätzung, nicht Trennung/
Transkription/Notensatz. Feintuning (z.B. Plausibilitätsbereich, mehrere
Kandidaten vergleichen) ist ein offener Punkt in `docs/ARCHITECTURE.md`.

**Test mit echter MP3 durchgeführt** (2026-09-27, `jingle.bells.mp3`,
öffentlich verfügbare Weihnachtslied-Aufnahme, ~1:15 Min): kompletter
Durchlauf erfolgreich, BPM 161 → "Vivace" (für dieses Stück plausibel),
PDF/MusicXML/MIDI erzeugt. Dabei einen weiteren Bug gefunden und behoben:
jede Stimme bekam ihre eigene `MetronomeMark`, wodurch "♩ = 161" vierfach
übereinander gedruckt wurde — jetzt nur noch bei der ersten Stimme gesetzt.

**Qualitätsbefund der Transkription selbst:** Bei echter, polyphoner Musik
zeigt die aktuelle Basis-Pipeline (Demucs 4-Stem + Basic Pitch) deutliche
Ungenauigkeiten — u.a. vereinzelte Noten in extremen Registern (viele
Hilfslinien) und unplausibel lang gehaltene Töne, vermutlich weil die
Stimmtrennung Reste anderer Instrumente/Percussion in die "Vocals"- und
"Other"-Stems durchlässt, die Basic Pitch dann fehlinterpretiert. Das ist
kein Implementierungsfehler, sondern der erwartete Qualitätsunterschied
zwischen dieser offenen Pipeline und klang.io's proprietärem Modell (siehe
"Entscheidungen" oben).

**Zweite Feedback-Runde umgesetzt** (2026-09-27, Nutzer-Review): 6-Stem-
Trennung (`htdemucs_6s`: Vocals/Piano/Gitarre/Bass/Drums/Sonstige statt
4-Stem, damit Piano/Gitarre nicht mehr in einem Stem vermischt werden),
feste Spurreihenfolge (Vocals, Piano, Gitarre, Bass, Sonstige, Drums
zuletzt), Tonarterkennung (`music21` Krumhansl-Schmuckler-Analyse statt
hartkodiertem C-Dur — deutlich weniger Vorzeichen), Ausreißer-Filter für
unplausible Tonhöhen pro Instrument (behebt "Wand aus Hilfslinien"),
explizite Systemumbrüche alle 4 Takte (PDF: von 1 auf 4 Takte/Zeile,
25 statt 20 Seiten für ein ~1:15-Stück), italienische Tempobezeichnung
jetzt direkt im Notenbild (`MetronomeMark(text=...)`, vorher nur in der
UI), korrekter Titel/Komponist aus Dateiname statt "Music21 Fragment"/
"Music21", einfache Chroma-basierte Akkorderkennung pro Takt
(`ChordSymbol` über der obersten Stimme). Frontend: Transport-Leiste
(Play/Pause, Seek, Tempo) ist jetzt oben fixiert und bleibt beim
Auto-Scroll des Notencursors erreichbar; Instrument-Toggles, Wiedergabe-
Quelle und Downloads sind in ein Hamburger-Menü verschoben.

**Zur Geschwindigkeit:** Die Pipeline läuft im Docker-Container aktuell
nur auf CPU (`DEVICE=cpu`). Docker Desktop auf Apple Silicon kann die
Mac-GPU (Metal/MPS) nicht an Linux-Container durchreichen — das ist eine
Docker-Desktop-Einschränkung, keine Fehlkonfiguration unsererseits. Für
GPU-Beschleunigung auf dem Mac muss das Backend nativ laufen (Variante B
oben, `DEVICE=mps`); auf einem späteren Linux-Produktivsystem mit
NVIDIA-GPU funktioniert GPU-Passthrough in Docker regulär
(`DEVICE=cuda`, siehe auskommentierter `deploy.resources`-Block in
`docker-compose.yml`).

Nächste sinnvolle Verbesserungsschritte: weitere Nachbearbeitung/Glättung
der Basic-Pitch-Ausgabe, Genauigkeit der Akkorderkennung erhöhen (aktuell
nur Dur-/Moll-Dreiklänge, kein 7er/Sus/Inversionen), ggf. Vergleich mit
einem kommerziellen Provider für eine Qualitäts-Baseline.

**Vierte Runde — der echte "1 Takt pro Zeile"-Bug + zwei Feature-Requests**
(2026-09-27): Der Zoom/Breiten-Fix aus Runde 3 half nicht in jedem Fall.
Root Cause: `part.makeMeasures()` (music21) splittete bei dichten,
unregelmäßigen Transkriptionen nicht zuverlässig an jeder 4.0-quarterLength-
Grenze — einzelne "Takte" enthielten bis zu 6.25 statt 4.0 Beats Inhalt, was
sie im Rendering unnötig breit machte. Fix in
[`notation.py`](backend/app/pipeline/notation.py): Takte werden jetzt
**explizit selbst gebaut** (jede Note wird per `offset_ql // 4.0` einem
festen `Measure`-Objekt zugeordnet) statt music21s Heuristik zu vertrauen;
dabei auch eine Quantisierungs-Kollision behoben, bei der zwei eigentlich
getrennte Noten nach dem Runden auf denselben Zeitraster-Punkt fielen und
sich dadurch überlappten. Mit `jingle.bells.mp3` verifiziert: alle 72 Takte
über alle 6 Spuren exakt 4.0 quarterLengths, 0 Bindebögen, korrektes
4-Takte-pro-Zeile-Layout.

Dazu zwei Nutzer-Feature-Requests umgesetzt:
- **Synth-Wiedergabe war stumm/brach ab:** `<audio src=".mid">` funktioniert
  in Browsern nicht — MIDI ist kein abspielbares Audioformat, nur eine
  Liste von Noten-Events. Neuer einfacher additiver Sinus-Synthesizer in
  [`synth.py`](backend/app/pipeline/synth.py) rendert die MIDI-Datei
  serverseitig einmalig zu einer echten WAV-Datei (kein neues externes
  Tool/Abhängigkeit — nutzt nur bereits vorhandenes `mido`+`numpy`+
  `soundfile`), servierbar über `/api/jobs/{id}/synth-audio`. Klanglich
  einfach (Keyboard-Demo-Charakter, keine echten Instrumentenklänge),
  aber tatsächlich abspielbar.
- **Job ging bei Seiten-Reload verloren:** Der Job-Store war rein
  In-Memory und das Frontend hatte keine Möglichkeit, nach einem Reload
  wieder an denselben Job zu kommen. Jetzt: jeder Job wird zusätzlich als
  `job.json` neben seinen Export-Dateien persistiert
  ([`job_store.py`](backend/app/core/job_store.py)) und beim Server-Start
  wieder eingelesen — übersteht also auch einen Backend-Neustart, nicht
  nur einen Seiten-Reload. Neuer `GET /api/jobs`-Endpunkt liefert eine
  Liste bisheriger Transkriptionen; das Frontend zeigt sie als klickbare
  "Bibliothek" auf dem Startbildschirm und hält den aktuellen Job in der
  URL (`?job=<id>`) fest, sodass ein Reload denselben Job wieder anzeigt
  statt zum leeren Upload-Screen zurückzuspringen.

**Dritte Runde — echte Bugs beim Testen mit realer Musik gefunden**
(2026-09-27):

- **Leere Notenanzeige im Browser:** OpenSheetMusicDisplay 1.9 stürzte beim
  Rendern ab (`Cannot read properties of undefined (reading 'denominator')`
  in `createGraphicalTies`), sobald zwei Bindebogen-Partner nicht direkt
  aneinandergrenzten. Zwei Ursachen, beide behoben in
  [`notation.py`](backend/app/pipeline/notation.py):
  1. Notendauern, die über einen Takt hinausragten, wurden von music21
     automatisch in gebundene Noten über den Taktstrich hinweg aufgeteilt
     → Notendauern werden jetzt am Taktende gekappt.
  2. Der eigentliche Hauptfall: Basic Pitch liefert gelegentlich zwei
     zeitlich nahe, aber nicht identische Note-Events derselben Tonhöhe
     (kein exaktes Simultan-Match für unser Akkord-Merging). Zwei
     überlappende Noten derselben Tonhöhe in einer Stimme lässt music21
     einen (musikalisch unsinnigen) Bindebogen zwischen ihnen erzeugen →
     jede Notendauer wird jetzt zusätzlich auf den Start der nächsten Note
     gekappt (keine Überlappungen mehr).
  Als Sicherheitsnetz entfernt [`export.py`](backend/app/pipeline/export.py)
  zusätzlich alle `<tie>`/`<tied>`-Elemente direkt aus der fertigen
  MusicXML-Datei per Post-Processing — music21s eigener MusicXML-Writer
  kann beim `.write()` nochmal einen eigenen Notation-Durchlauf machen und
  dabei Bindebögen neu einfügen, selbst nachdem sie im Objektbaum vorher
  schon entfernt wurden.
- **"Immer nur 1 Takt pro Zeile" bestand bei dichten Mehrspur-Stücken
  weiter fort**, obwohl die Systemumbrüche im PDF schon griffen: OSMD
  respektiert `newSystemFromXML` nur, wenn die Takte auch in die
  Container-Breite passen — bei 6 dichten Notensystemen reichte weder
  Standard-Zoom (1.0) noch die 1100px-Lesetextbreite der App. Fix:
  [`ScoreViewer.tsx`](frontend/src/components/ScoreViewer.tsx) setzt jetzt
  `osmd.zoom = 0.7`, und die Notenansicht bricht per CSS bewusst aus der
  schmalen Prosa-Breite von `.app` aus auf volle Fensterbreite
  ([`styles.css`](frontend/src/styles.css)). Mit derselben MusicXML direkt
  gegen die OSMD-Bibliothek verifiziert: konsistent 4 Takte/Zeile über
  mehrere Systeme.
- **PDF-Export nativ (Variante B) crasht zuverlässig:** MuseScore 4.7.5s
  CLI stürzt beim programmatischen Aufruf ab (`std::system_error: mutex
  lock failed`) — reproduziert sowohl in der Automatisierungsumgebung als
  auch im echten Nutzer-Setup, mehrere Workarounds erfolglos getestet
  (Crash-Dump-Verzeichnis vorlegen, `QT_QPA_PLATFORM=offscreen` — auf
  macOS nicht verfügbar, Start via `open`/LaunchServices). Kein Bug in
  unserer Pipeline, sondern ein MuseScore-4-CLI-Problem auf diesem System.
  Die API gibt jetzt eine klare Fehlermeldung (HTTP 502 mit Erklärung)
  statt eines rohen 500-Tracebacks zurück; PDF-Export bleibt bis auf
  Weiteres eine Docker-only-Funktion (Variante A, MuseScore 3).

**Fünfte Runde — Drum-Notendarstellung, Scroll-Zittern**
(2026-09-27):

- **Drum-Noten lagen weiterhin außerhalb der Notenzeile**, obwohl die
  Oktave in Runde 3 korrigiert wurde. Root Cause per isoliertem Test
  gefunden: Mit Percussion-Clef ignoriert OSMD normale `<pitch>`-Noten
  komplett und positioniert sie alle an derselben Default-Stelle,
  unabhängig von der tatsächlichen Tonhöhe — bestätigt, indem vier
  `Note`-Objekte auf C4/F4/C5/G5 unter Percussion-Clef gerendert wurden
  und alle exakt gleich lagen. Perkussion muss stattdessen als
  `<unpitched><display-step>/<display-octave>` kodiert werden. Fix in
  [`notation.py`](backend/app/pipeline/notation.py): Drums nutzen jetzt
  `music21.note.Unpitched` statt `note.Note`; da `Unpitched`-Objekte
  nicht in einen `Chord` passen, werden gleichzeitige Hits (z.B.
  Kick+Hi-Hat) als separate Elemente auf demselben Offset eingefügt statt
  zusammengeführt. Mit echtem Material verifiziert: Kick/Snare/Hi-Hat
  jetzt klar unterscheidbar auf der Notenzeile.
- **Zittern beim Zeilenwechsel (3-4 Sek.):** OSMDs `followCursor`
  scrollt automatisch zum Cursor; unser Sync-Loop rief das aber bei
  jedem Animation-Frame auf (~60×/Sek.), wodurch der Browser das
  Smooth-Scroll bei jedem Zeilenwechsel ständig neu startete, statt es
  fertig laufen zu lassen. Fix in
  [`ScoreViewer.tsx`](frontend/src/components/ScoreViewer.tsx): Cursor-
  Sync auf max. 5×/Sek. gedrosselt. Per Scroll-Position-Sampling
  verifiziert: vorher dauerhafte Oszillation, jetzt eine einzige glatte,
  monoton konvergierende Bewegung unter 1 Sekunde.

**Sechste Runde — Bedienbarkeit, Spuranordnung, Liedtext**
(2026-09-27):

- **Instrumenten-Checkboxen schwer klickbar** ("hakelig und sehr
  mühsam"): `.instrument-toggle` in
  [`styles.css`](frontend/src/styles.css) von einer winzigen nativen
  Checkbox zu einer großen, komplett klickbaren Pille umgebaut (Padding,
  abgerundeter Rahmen, Hervorhebung bei aktivem Zustand, größere
  Checkbox).
- **Piano/Bass nicht direkt untereinander** (eigentlich rechte/linke
  Hand): `_TRACK_ORDER` in
  [`local_provider.py`](backend/app/pipeline/local_provider.py) so
  umsortiert, dass Piano direkt über Bass liegt.
- **Liedtext unter den Vocals + Ein-/Ausblenden:** Neues Modul
  [`lyrics.py`](backend/app/pipeline/lyrics.py) transkribiert die
  isolierte Vocals-Stem per `faster-whisper` (wortgenaue Timestamps,
  Sprache automatisch erkannt). Die Wörter werden in
  [`notation.py`](backend/app/pipeline/notation.py) den nächstgelegenen
  transkribierten Noten als `note.Lyric` zugeordnet. Im Frontend steuert
  ein neuer "Liedtext"-Toggle `osmd.EngravingRules.RenderLyrics` — exakt
  wie ein Instrumenten-Toggle, wie gewünscht. Mit echtem Song-Material
  verifiziert (Text erscheint unter der Vocals-Zeile und lässt sich
  sauber aus-/einblenden).
- **PDF-Instrumentenauswahl geprüft — dabei Bug gefunden und behoben:**
  Die UI dafür existierte schon (Hamburger-Menü, Sektion
  "PDF-Export – Instrumente wählen"), aber der Filter hat serverseitig
  nie funktioniert. Root Cause: [`jobs.py`](backend/app/api/routes/jobs.py)
  parst für den PDF-Export das bereits geschriebene `combined.musicxml`
  neu ein (`converter.parse`) — dabei vergibt music21 jedem `<score-part>`
  eine eigene generierte Id (z.B. `"P05a8dd43..."`), die nichts mehr mit
  unseren `track_id`-Werten ("piano", "bass", …) zu tun hat. Der Filter
  in `export_pdf()` verglich genau gegen diese IDs und traf nie —
  dasselbe Muster wie der frühere OSMD-Sichtbarkeits-Bug. Fix: Filterung
  in [`export.py`](backend/app/pipeline/export.py) läuft jetzt über
  `Part.partName` (das gesetzte Label, z.B. "Piano"), `jobs.py` löst die
  angefragten `track_id`s vorher über `job.tracks` in Labels auf. Vorher/
  Nachher verglichen: gefiltertes PDF (nur Piano+Bass) war vorher
  praktisch leer (nur "Music21 Fragment"-Titel, keine Takte mit Inhalt),
  jetzt enthält es korrekt nur die zwei gewählten Notenzeilen mit echtem
  Notenmaterial.

Offen (nächste Runde): MuseScore-Notensatzqualität (Layout, Spacing)
weiter verbessern; vom Nutzer erwähnte Skalierungsfrage bei sehr kleiner
Zoomstufe (4 Takte/Zeile) noch nicht untersucht.

**Siebte Runde — PDF-Titel-Bug, Takte-pro-Zeile-Regler, Klavier-Notensystem**
(2026-09-27):

- **PDF zeigte "Music21 Fragment" statt Liedtitel:** Root Cause:
  `converter.parse()` in [`jobs.py`](backend/app/api/routes/jobs.py)
  populiert `Score.metadata` nicht zuverlässig aus den
  `<work-title>`/`<movement-title>`-Tags, obwohl sie im MusicXML stehen —
  bestätigt per direktem music21-Test (Titel im File vorhanden, aber
  `score.metadata.title` nach `converter.parse()` `None`). Beim
  Instrumenten-gefilterten PDF kam zusätzlich noch dazu, dass
  `export_pdf()` für die Filterung einen komplett neuen, leeren
  `stream.Score()` baut, der gar kein Metadata-Objekt mehr hat. Fix:
  Titel (und Komponist) werden nach dem Neuparsen jetzt explizit wieder
  gesetzt (`_score_metadata()`-Helper) und beim gefilterten Export mit
  auf den neuen Score kopiert. Vorher/Nachher per PDF-Rendering
  verglichen.
- **"Takte pro Zeile"-Schieberegler** (Web + PDF gemeinsam, wie vom
  Nutzer gewünscht): Die bisher fest einprogrammierten
  System-Umbrüche alle 4 Takte
  ([`notation.py`](backend/app/pipeline/notation.py),
  `DEFAULT_MEASURES_PER_SYSTEM`) sind jetzt über eine neue, wieder-
  verwendbare Funktion `set_system_breaks()` nachträglich auf einem
  bereits gebauten Score neu setzbar — ohne dass neu transkribiert
  werden muss. Neuer Query-Parameter `?measures_per_system=N` auf
  `/musicxml` und `/pdf`
  ([`jobs.py`](backend/app/api/routes/jobs.py)); die Notengröße
  skaliert dabei proportional zur Baseline mit (OSMD-`zoom` im
  Frontend, `<millimeters>` beim PDF), damit die gewählte Dichte noch
  auf die Seite/den Bildschirm passt. Neuer Regler im Hamburger-Menü
  ([`ScoreViewer.tsx`](frontend/src/components/ScoreViewer.tsx)),
  entprellt (400ms), damit nicht bei jedem Pixel Drag neu geladen
  wird; Sichtbarkeits-/Liedtext-Zustand wird nach jedem Reload
  automatisch wiederhergestellt. Mit echtem Song-Material getestet
  (4 vs. 6 Takte/Zeile, Web und PDF).
- **MuseScore-Export auf Qualität der Web-Ansicht gebracht:** Klavier
  und Bass werden jetzt wie ein echtes Klaviersystem mit einer
  geschweiften Klammer (`layout.StaffGroup`, `symbol="brace"`)
  zusammengefasst, statt als zwei unabhängige Notenzeilen zu
  erscheinen — entspricht der bereits vorhandenen Web-Ansicht-Anordnung
  (rechte/linke Hand). Zusammen mit dem Titel-Fix und dem
  Takte-pro-Zeile-Regler liefert der PDF-Export jetzt strukturell
  dieselbe Darstellung wie die Web-Ansicht (Reihenfolge, Klammerung,
  Liedtext, Zeilenumbrüche). Per komplettem End-to-End-Durchlauf über
  den echten `/pdf`-Endpoint verifiziert (Docker, frisch hochgeladener
  Song): Titel korrekt, Klammer sichtbar, 4 Takte/Zeile, Liedtext unter
  Vocals.

Offen (nächste Runde): allgemeines MuseScore-Spacing/Layout-Feintuning
(Notenabstände, Taktbreiten-Ausgleich) für noch bessere Druckqualität;
Perkussions-Notenköpfe im PDF-Export erzeugen MuseScore-CLI-Warnungen
("unmapped drum note") für einzelne GM-Drum-Pitches — bisher nur als
harmlose Warnung beobachtet, optisch noch nicht mit echtem Drum-Material
im PDF verglichen.

**Achte Runde — PDF-Export war doch noch kaputt: erzwungene Zeilenumbrüche
gegen MuseScores eigenes Layout** (2026-09-28):

- Trotz der siebten Runde meldete der Nutzer weiterhin "nur 1 Takt pro
  Zeile" im PDF. Beim Nachstellen über den *echten* PDF-Endpoint (nicht
  nur meiner eigenen Nachbildung) zeigte sich: Das Problem trat
  tatsächlich in **beiden** Renderern auf (MuseScore 3 in Docker *und*
  MuseScore 4 nativ), nur unterschiedlich häufig — der vorige Test hatte
  zufällig Stellen ohne das Problem erwischt.
- **Root Cause:** Unsere fest einprogrammierten System-Umbrüche (alle
  N Takte, via `layout.SystemLayout(isNew=True)`) sagen MuseScore "hier
  beginnt eine neue Zeile", verhindern aber nicht, dass MuseScore
  *zusätzliche* Umbrüche einfügt, wenn ein dicht besetzter Takt
  (viele gleichzeitige 16tel-Akkorde über 6 Notenzeilen) nicht mehr in
  die verbleibende Zeilenbreite passt. Landet danach ein dünn besetzter
  Takt allein in seiner Zeile, streckt/justiert MuseScore ihn über die
  komplette Seitenbreite — genau das "1 Takt, riesig auseinandergezogen"
  Bild, das der Nutzer sah. Reproduziert und visuell bestätigt anhand
  eines vollständigen PDF-Renderings über mehrere Systeme hinweg.
- **Fix:** Für den PDF-Export werden jetzt *keine* Umbrüche mehr
  erzwungen — neue Funktion `notation.strip_system_breaks()` entfernt
  alle `SystemLayout`-Marker, MuseScores eigenes Casting-off entscheidet
  Zeilenumbrüche komplett selbst anhand der tatsächlichen Taktbreite und
  Seitenbreite. Der `measures_per_system`-Parameter steuert beim
  PDF-Export jetzt nur noch die Notengröße (`scale_mm`), nicht mehr eine
  exakte Taktzahl pro Zeile. Die Web-Ansicht (OSMD) behält ihre festen
  Umbrüche, da sie unterfüllte Systeme nicht streckt und das Problem dort
  nicht auftritt. Nach dem Fix: komplettes 6-seitiges PDF Seite für Seite
  geprüft, durchgängig 3 Takte/Zeile, keine gestreckten Einzeltakte mehr
  — auch an der vorher fehlerhaften Stelle (Takt 28–34).
- **Nebenbefund:** Natives MuseScore 4 stürzt nicht nur gelegentlich ab
  (bekannt), sondern erzeugte bei erfolgreichen Läufen zusätzlich
  dasselbe Layout-Problem, nur mit anderer Verteilung. Da MuseScore 4
  strikt schlechter ist als Docker/MuseScore 3 und keinen Mehrwert bringt,
  wird es in [`run_native.sh`](backend/run_native.sh) jetzt nicht mehr
  automatisch aktiviert — `/pdf` schlägt nativ standardmäßig klar mit
  Hinweis auf Docker fehl (HTTP 502), statt still ein schlechtes PDF zu
  liefern. Dafür fängt [`jobs.py`](backend/app/api/routes/jobs.py) jetzt
  auch `FileNotFoundError` (fehlende MuseScore-Binary) mit derselben
  freundlichen Fehlermeldung ab, nicht nur `CalledProcessError`
  (Absturz).

Offen (nächste Runde): allgemeines MuseScore-Spacing/Layout-Feintuning;
Drum-Notenköpfe im PDF optisch noch nicht mit echtem Material verglichen;
die "PDF herunterladen"-Links im Frontend sind normale `<a href>`-Downloads
— ein 502-Fehler vom Server zeigt sich dem Nutzer aktuell nur als
fehlgeschlagener Download, nicht als lesbare Fehlermeldung in der UI.

**Neunte Runde — MuseScore 4 nativ wieder aktiviert, PDF-Fehler jetzt in der UI sichtbar**
(2026-09-28):

- Der Layout-Fix aus Runde 8 (`strip_system_breaks`) betrifft MuseScore 3
  und MuseScore 4 gleichermaßen — beide bekommen dieselbe Datei ohne
  erzwungene Zeilenumbrüche. Ein erneuter Test mit MuseScore 4 nativ
  zeigt jetzt durchgängig sauberes Layout (6 Seiten, 3 Takte/Zeile, keine
  gestreckten Einzeltakte, geprüft auf allen Seiten). MuseScore 4 in
  [`run_native.sh`](backend/run_native.sh) ist deshalb wieder aktiviert —
  der PDF-Export läuft jetzt auch nativ (`:5175`/`:8001`), nicht mehr nur
  über Docker. Das bekannte gelegentliche CLI-Absturzrisiko von
  MuseScore 4 bleibt bestehen, äußert sich aber weiterhin als klarer
  HTTP-502-Fehler, nicht als schlechtes PDF.
- `jobs.py` fängt jetzt auch `FileNotFoundError` ab (fehlende
  MuseScore-Binary, z.B. wenn `MUSESCORE_BINARY` bewusst deaktiviert ist)
  mit derselben freundlichen Fehlermeldung wie ein CLI-Absturz.
- **"PDF herunterladen" zeigt Server-Fehler jetzt lesbar in der UI**
  statt als stillschweigend fehlgeschlagenen Download: Der Button in
  [`ScoreViewer.tsx`](frontend/src/components/ScoreViewer.tsx) lädt das
  PDF jetzt per `fetch()` statt über ein `<a href download>`. Bei einem
  Fehler (4xx/5xx) wird die `detail`-Nachricht der API direkt unter dem
  Button angezeigt (rot); bei Erfolg wird die Datei clientseitig als
  Blob heruntergeladen, mit dem vom Server vorgeschlagenen Dateinamen
  (`Content-Disposition`). Verifiziert: erzwungener 502
  (MuseScore-Binary vorübergehend auf einen ungültigen Pfad gesetzt)
  zeigt die Meldung sichtbar im Menü; nach Wiederherstellen der
  korrekten Konfiguration läuft derselbe Klick wieder sauber durch.

**Zehnte Runde — MusicXML-Download hatte den Layout-Fix nicht** (2026-09-28):

- Der Nutzer öffnete die heruntergeladene MusicXML-Datei direkt in der
  MuseScore-**Desktop**-App (nicht unseren PDF-Export) und sah wieder
  "1 Takt pro Zeile". Root Cause: `?measures_per_system=`-Downloads
  liefen weiterhin über `set_system_breaks()` (feste Umbrüche alle
  N Takte) — genau der Mechanismus, der in Runde 8 für den PDF-Export
  durch `strip_system_breaks()` ersetzt wurde, weil MuseScore
  unterfüllte Systeme streckt. Die MuseScore-Desktop-App nutzt
  denselben Layout-Code wie die CLI und zeigt daher exakt dasselbe
  Problem, sobald die Datei dort statt in OSMD geöffnet wird.
- **Fix:** Neuer Query-Parameter `?for_musescore=true` auf
  `/musicxml` ([`jobs.py`](backend/app/api/routes/jobs.py)): liefert
  eine Variante ganz ohne erzwungene Umbrüche plus derselben
  `<millimeters>`-Skalierung wie der PDF-Export (neue Hilfsfunktion
  `export.apply_scale_mm()`, aus `export_pdf()` herausgezogen, damit
  beide Pfade dieselbe Logik teilen). Der "MusicXML
  herunterladen"-Link im Frontend
  ([`ScoreViewer.tsx`](frontend/src/components/ScoreViewer.tsx),
  `musicXmlForMuseScoreUrl()` in
  [`api.ts`](frontend/src/lib/api.ts)) nutzt jetzt diese Variante. Die
  Web-Ansicht ist davon unberührt — OSMD lädt weiterhin die Variante
  mit festen Umbrüchen über den unveränderten `musicXmlUrl()`-Pfad,
  wie vom Nutzer gewünscht ("das lassen wir erst einmal so"). Verifiziert:
  heruntergeladene Datei enthält keine `new-system="yes"`-Marker mehr,
  `<millimeters>3.5</millimeters>`, korrekter Titel; per MuseScore-CLI-
  Rendering als Stellvertreter für die Desktop-App bestätigt (3 Takte/
  Zeile, keine gestreckten Einzeltakte) — Web-Ansicht weiterhin
  unverändert bei 4 Takten/Zeile.

**Elfte Runde — Grand Staff als eine Instrumentenspur** (2026-09-28):

- Nachdem Piano und Bass als geklammertes Klaviersystem notiert werden
  (siehe siebte Runde), gab es in den Toggle-Listen ("Instrumente
  anzeigen", "PDF-Export – Instrumente wählen") weiterhin zwei separate
  Einträge — man konnte z.B. den Bass-Schlüssel ausblenden, während
  Klammer und Violinschlüssel stehen blieben, was im Notenbild keinen
  Sinn ergibt.
- Neuer Helper `getDisplayTracks()` in
  [`ScoreViewer.tsx`](frontend/src/components/ScoreViewer.tsx) fasst
  Piano+Bass zu einem einzigen "Piano"-Toggle zusammen (nur wenn beide
  Spuren im Job vorhanden sind); alle anderen Instrumente bleiben
  unverändert einzeln. `toggleInstrument()`/`togglePdfInstrument()`
  nehmen jetzt eine Liste von `track_id`s entgegen und schalten sie
  gemeinsam um. Betrifft beide Toggle-Sektionen (Web-Sichtbarkeit *und*
  PDF-Instrumentenauswahl) sowie deren jeweilige Checkbox-Zustände.
  Getestet: Ausschalten von "Piano" blendet Klavier- *und*
  Bass-Notenzeile zusammen aus (Vocals folgt direkt von Gitarre),
  Wiedereinschalten stellt beide in korrekter Reihenfolge wieder her;
  PDF-Export mit der zusammengefassten Auswahl liefert weiterhin beide
  Notenzeilen mit Klammer.

**Zwölfte Runde — PDF-Download hieß nur noch "transkription.pdf"** (2026-09-28):

- Seit dem Umstieg auf `fetch()` + Blob-Download für den PDF-Button
  (neunte Runde, um Server-Fehler in der UI anzeigen zu können) hieß
  die heruntergeladene Datei immer nur "transkription.pdf" statt wie
  vorher nach dem Songtitel. Zwei Ursachen, beide in
  [`ScoreViewer.tsx`](frontend/src/components/ScoreViewer.tsx) bzw.
  [`main.py`](backend/app/main.py) behoben:
  1. Der `Content-Disposition`-Header ist bei einem Cross-Origin-Request
     (Frontend `:5175` → Backend `:8001`, andere Origin) für
     Frontend-JS grundsätzlich **nicht lesbar**, außer der Server
     erlaubt das explizit — `CORSMiddleware` hatte kein
     `expose_headers=["Content-Disposition"]` gesetzt, wodurch
     `res.headers.get("content-disposition")` immer `null` zurückgab.
  2. Zusätzlich erkannte die eigene Regex nur das einfache
     `filename="..."`-Format, aber FastAPIs `FileResponse` nutzt wegen
     der Sonderzeichen im Songtitel (z.B. "ü") RFC-5987-Kodierung
     (`filename*=UTF-8''...`, prozent-kodiert) — dafür jetzt ein
     eigenes Regex-Pattern mit `decodeURIComponent()`.
  3. Zusätzlich `cache: "no-store"` beim Fetch ergänzt, damit ein
     gecachter alter Response (z.B. von vor einem Backend-Fix) nicht
     wieder denselben veralteten Header/Inhalt ausliefert — genau das
     hatte die Fehlersuche hier zwischenzeitlich verschleiert.
  Verifiziert: `document.createElement("a")` instrumentiert, um den
  tatsächlich gesetzten `download`-Dateinamen bei einem echten
  Button-Klick abzufangen — liefert jetzt korrekt
  `"<Songtitel>.mp3_full_mps4.pdf"` statt `"transkription.pdf"`.

**Dreizehnte Runde — MuseScore-4-Absturz war viel häufiger als gedacht,
Klammer ging beim gefilterten PDF verloren** (2026-09-28):

- Bei ausschließlicher Nutzung von `:5175` (nativ, MuseScore 4) zeigte
  sich: der dokumentierte gelegentliche CLI-Absturz trat in Wirklichkeit
  in ca. jedem zweiten Aufruf auf, nicht nur selten. Bei genauerem
  Hinsehen (mehrfache direkte CLI-Läufe außerhalb unserer Pipeline,
  Exit-Code + Datei-Inhalt jeweils separat geprüft): MuseScore 4 stürzt
  **nach** dem korrekten Schreiben der PDF-Datei ab — offenbar während
  des eigenen Cleanups/Exits, nicht während der eigentlichen
  Konvertierung. In jedem der beobachteten Abstürze (Exit-Code 134 /
  SIGABRT) lag trotzdem eine vollständige, gültige PDF-Datei auf der
  Platte.
- **Fix:** `export_pdf()` in
  [`export.py`](backend/app/pipeline/export.py) ignoriert den
  Exit-Code jetzt und prüft statt dessen direkt, ob die Ausgabedatei
  tatsächlich erzeugt wurde — nur wenn die Datei fehlt, gilt es als
  echter Fehlschlag. (Ein erster Versuch mit blindem Retry bei jedem
  Absturz wurde wieder verworfen: teurer, unnötig, und schlug in
  Stichproben trotzdem manchmal 3× in Folge fehl, weil die Abstürze in
  Clustern auftreten.) Verifiziert: 8 aufeinanderfolgende Anfragen,
  3 davon mit tatsächlichem CLI-Absturz im Log, alle 8 lieferten ein
  gültiges PDF (HTTP 200).
- **Nebenbefund dabei entdeckt:** Der gefilterte PDF-Export (z.B. nur
  "Piano" ausgewählt, was Piano+Bass einschließt) verlor die
  Klavier-Klammer (`layout.StaffGroup`) komplett — sie hängt in
  music21 als Spanner auf Score-Ebene, nicht an den einzelnen `Part`-
  Objekten, und wurde beim Aufbau des gefilterten Render-Scores schlicht
  nicht mitkopiert. Fix: `export_pdf()` übernimmt jetzt jede
  `StaffGroup`, deren sämtliche referenzierten Parts im Filter
  enthalten sind. Verifiziert per Rendering: gefiltertes Piano+Bass-PDF
  zeigt die Klammer jetzt wieder korrekt.

**Vierzehnte Runde — Transponieren pro Instrument** (2026-09-28):

- Neue Funktion: einzelne Spuren um Halbtöne verschieben, z.B. Vocals von
  einer Tonart in eine andere, damit sie z.B. mit einem Alto-Sax gespielt
  werden können. Zwei Bedienwege pro Spur, wie besprochen: ein
  Halbton-Regler (-24 bis +24) für beliebige Verschiebungen, plus ein
  Instrumenten-Preset-Dropdown (Konzerttonhöhe, B-/Es-/F-Instrument,
  A-Klarinette) als Abkürzung, die den Regler auf den musikalisch
  korrekten Standardwert für dieses Instrument setzt.
- **Backend:** Neue Funktion `notation.transpose_score(score,
  transpositions)` ([`notation.py`](backend/app/pipeline/notation.py)) —
  nutzt music21s `Part.transpose()`, das Noten, Tonart/Vorzeichen *und*
  Akkordsymbole konsistent mitverschiebt (mit echtem Songmaterial
  verifiziert: Tonartwechsel und Akkordbeschriftung stimmten exakt
  überein). Neuer Query-Parameter `?transpose=track_id:halbtöne,...` auf
  allen vier relevanten Endpunkten
  ([`jobs.py`](backend/app/api/routes/jobs.py)) — `/musicxml`, `/pdf`,
  `/midi` und `/synth-audio` (letztere zwei bauen dafür bei Bedarf eine
  transponierte MIDI-Variante, aus der auch das Synth-Audio gerendert
  wird) — wie besprochen wirkt sich die Transponierung auf Web-Ansicht,
  MusicXML-Download, PDF-Export *und* Synth-Wiedergabe aus; die
  Original-MP3-Wiedergabe bleibt bewusst unverändert (nicht sinnvoll pro
  Instrument trennbar).
- **Frontend:** Neue Sektion "Transponieren" im Hamburger-Menü
  ([`ScoreViewer.tsx`](frontend/src/components/ScoreViewer.tsx)) mit
  einem Regler + Preset-Dropdown pro (zusammengefasster) Spur; entprellt
  wie der "Takte pro Zeile"-Regler. Piano+Bass werden dabei als eine
  Einheit behandelt (nutzt denselben `displayTracks`-Mechanismus wie die
  Instrumenten-Toggles aus der elften Runde) und immer gemeinsam
  transponiert.
- Verifiziert im Browser: Vocals-Preset "Es-Instrument" gewählt →
  Web-Ansicht zeigt sofort die neue Tonart nur bei Vocals (alle anderen
  Spuren unverändert); PDF-Export mit derselben Transposition gerendert
  und geprüft — Akkordsymbole korrekt umbenannt, Klavier-Klammer bleibt
  erhalten.

**Fünfzehnte Runde — Instrument-Umbenennung bei Preset-Transposition**
(2026-09-28):

- Wenn eine Spur per Instrumenten-Preset transponiert wird (z.B. Vocals
  → Alt-Sax), zeigt die Notenzeile jetzt auch den neuen Instrumentnamen
  statt weiterhin "Vocals" — und zwar in Web-Ansicht, PDF *und*
  MusicXML-Download, nicht nur clientseitig. Wird der Regler auf
  "Konzerttonhöhe (C)" zurückgestellt, erscheint wieder der ursprüngliche
  Name.
- Presets sind jetzt konkrete Instrumente statt Transpositions-Familien
  (Trompete, Klarinette, Tenor-/Sopran-/Alt-/Bariton-Sax, Horn,
  A-Klarinette) — vorher gruppiert (z.B. "B-Instrument (Trompete,
  Klarinette, …)"), was keinen eindeutigen Anzeigenamen hätte liefern
  können.
- **Backend:** `transpose`-Parameter um ein drittes, optionales Feld
  erweitert: `track_id:halbtöne:Name` (z.B. `vocals:9:Alt-Sax`). Neue
  Funktion `notation.rename_parts()`
  ([`notation.py`](backend/app/pipeline/notation.py)) setzt
  `Part.partName`/`-Abbreviation` und das zugehörige `Instrument`-Objekt
  neu. Ein Reihenfolge-Fallstrick dabei: Der `instruments`-PDF-Filter
  matcht Parts über ihren *ursprünglichen* Namen (z.B. "Vocals") — wird
  vorher umbenannt, findet der Filter die Spur nicht mehr und lässt sie
  fälschlich weg. Fix: `export_pdf()`
  ([`export.py`](backend/app/pipeline/export.py)) nimmt jetzt selbst
  einen `renames`-Parameter und wendet ihn *nach* dem Instrumentenfilter
  an, nicht `jobs.py` davor. Mit einem gezielten Regressionstest
  verifiziert (gefiltertes PDF nur "Vocals" + gleichzeitige Umbenennung
  zu "Alt-Sax" behält den Inhalt).
- **Frontend:** Jeder Preset-Dropdown-Eintrag liefert jetzt Halbtöne
  *und* einen Instrumentnamen zusammen; der Name wird verworfen, sobald
  der Halbton-Regler manuell verschoben wird (ein von Hand gewählter
  Wert ist kein benanntes Instrument mehr, auch wenn er zufällig auf
  denselben Halbtonwert fällt). `applyVisibility()`
  ([`ScoreViewer.tsx`](frontend/src/components/ScoreViewer.tsx)) musste
  angepasst werden: Sie matcht ausgeblendete Spuren gegen OSMDs aktuelle
  Instrumentennamen, die nach einer Umbenennung nicht mehr den
  Original-Labels aus `status.tracks` entsprechen. Verifiziert im
  Browser: Vocals → Alt-Sax (Web-Ansicht *und* Menü-Label wechseln
  sofort), Reset auf Konzerttonhöhe stellt "Vocals" wieder her,
  Ausblenden der umbenannten Spur funktioniert weiterhin korrekt.

**Sechzehnte Runde — Notenerkennungs-Qualität untersucht: Instrumentenmix
bei Jazz oft falsch erkannt; freies Umbenennen als Soforthilfe**
(2026-10-02):

- Testfall mit echten Originalnoten: "In A Sentimental Mood" (Gerry
  Mulligan, California Concerts Vol. 2 — Bariton-Sax, Tenor-Sax,
  Trompete, Ventilposaune+Piano, Bass, Drums, **keine** Gitarre, **kein**
  Gesang). Unsere Pipeline erzeugte trotzdem Spuren namens "Vocals" und
  "Gitarre" mit vollem Notenmaterial.
- **Root Cause, mit echten Messwerten belegt:** `htdemucs_6s` zwingt
  jedes Signal in 6 feste Kategorien (Vocals/Piano/Bass/Gitarre/
  Drums/Sonstige). Bei einer Bläserbesetzung ohne eigene Demucs-Klasse
  verteilen sich Bariton-Sax/Tenor-Sax/Trompete zwangsweise auf die
  nächstähnlich klingenden Kategorien (v.a. "Sonstige", aber auch
  "Vocals" und "Gitarre" als Rückstände/Übersprechen). Versucht, einen
  automatischen Lautstärke-Schwellwert zur Erkennung solcher
  Phantom-Spuren zu bauen — **verworfen, nachdem er sich als nicht
  zuverlässig erwies**: In diesem Song hat das echte (aber leise
  gespielte) Piano fast identische RMS-Lautstärke (0.0031) und
  Basic-Pitch-Velocity (65.8) wie das Phantom-"Gitarre" (RMS 0.0153,
  Velocity 65.1) und Phantom-"Vocals" (RMS 0.0150, Velocity 69.9) — kein
  Schwellwert hätte beides sauber getrennt. Auch Notenzahl ist kein
  verlässliches Signal: Basic Pitch "erkannte" 630 Noten im fast
  stummen Piano-Kanal, mehr als in der echten Bass-Spur (376) — reine
  Rauschhalluzination.
- **Stattdessen umgesetzt: freies Umbenennen einer Spur, unabhängig von
  der Transponierung.** Erweitert dieselbe Infrastruktur aus der
  fünfzehnten Runde (Instrumenten-Preset-Umbenennung) um einen
  Freitext-Eingabe pro Spur in der "Transponieren"-Sektion
  ([`ScoreViewer.tsx`](frontend/src/components/ScoreViewer.tsx)), die
  auch bei 0 Halbtönen funktioniert (reines Umlabeln ohne Tonhöhen-
  Änderung). Dabei zwei Bugs gefunden und behoben, die eine
  Umbenennung bei `semitones=0` komplett verworfen hätten, statt sie
  nur ohne Transposition anzuwenden: `_parse_transpose_param()`
  ([`jobs.py`](backend/app/api/routes/jobs.py)) verwarf den Namen
  mangels `if semitones:`-Gate, und `expandTranspositions()`/
  `transposeParam()` filterten Einträge mit 0 Halbtönen komplett
  heraus, bevor sie je beim Server ankamen. Wirkt sich wie die
  Preset-Umbenennung auf Web-Ansicht, PDF und MusicXML-Download aus.
  Verifiziert im Browser: "Gitarre" → "Tenor-Sax" umbenannt (nur Label,
  0 Halbtöne), Änderung erscheint sofort in Notenansicht und Menü.
- **Nebenbefund:** Tempo-Erkennung lag bei diesem Stück deutlich daneben
  (133 BPM "Allegro" erkannt, tatsächlich eine Ballade um die 70 BPM) —
  klassischer Tempo-Verdopplungsfehler, noch nicht untersucht/behoben.

Offen (nächste Runde, laut Nutzer-Entscheidung): bessere
Separations-Modelle für Jazz-/Bläser-Besetzungen evaluieren (z.B.
Alternative zu `htdemucs_6s`, die nicht zwingend in genau diese 6
Kategorien aufteilt); Tempo-Verdopplungsfehler beheben;
Basic-Pitch-Parameter pro Instrument tunen (siehe Runde-Beginn-Analyse,
noch nicht umgesetzt).

**Siebzehnte Runde — Tempo-Korrektur: Genre-Hinweis beim Upload +
manuelle Nachkorrektur** (2026-10-02):

- Wie in der sechzehnten Runde gemessen, ist der Tempo-Verdopplungsfehler
  bei "In A Sentimental Mood" kein klarer Bug, sondern eine echte
  Mehrdeutigkeit: Erkennungsstärke für das richtige (66,3) und das
  falsche Tempo (132,5) lagen nur 0,8% auseinander — praktisch ein
  Münzwurf. Mehrere automatische Korrekturansätze getestet (anderer
  `start_bpm`-Hint, Beat-Tracking nur auf der Bass-Spur, "bei knappem
  Vorsprung lieber langsameres Tempo nehmen") — **jeder hätte jingle.bells
  kaputt gemacht**, dessen Erkennungs-Marge mit 3,5% ähnlich knapp ist,
  dort aber das schnelle Tempo korrekt ist. Keine rein akustische
  Heuristik trennt beide Fälle zuverlässig.
- **Genre-Hinweis beim Upload** (verhindert das Problem an der Quelle):
  Neues Dropdown "Genre / Tempo-Hinweis" im Upload-Formular
  ([`UploadPanel.tsx`](frontend/src/components/UploadPanel.tsx)) —
  "Automatisch", "Ballade/langsam", "Pop/Rock", "Tanzbar/elektronisch",
  "Sehr schnell". Setzt serverseitig `start_bpm` für
  `librosa.beat.beat_track()` auf einen genre-typischen Wert
  ([`tempo.py`](backend/app/pipeline/tempo.py),
  `GENRE_START_BPM`) — dasselbe Prinzip, das in Runde 16 empirisch
  bestätigt wurde (`start_bpm=80` lenkte die Erkennung bei diesem Song
  zuverlässig auf 71,8 statt 132,5). Durchgereicht über
  `POST /api/jobs` (neues `genre`-Formularfeld) →
  `run_pipeline(job_id, genre_hint)` → `detect_tempo(y, sr, start_bpm=...)`.
- **Manuelle Nachkorrektur für bereits verarbeitete Jobs:** Neuer
  Endpunkt `POST /api/jobs/{id}/bpm?bpm=N`
  ([`jobs.py`](backend/app/api/routes/jobs.py)), UI-Sektion "Tempo
  korrigieren" im Hamburger-Menü mit ½/2×-Tempo-Buttons und freiem
  BPM-Eingabefeld
  ([`ScoreViewer.tsx`](frontend/src/components/ScoreViewer.tsx)). Anders
  als Transponieren/Takte-pro-Zeile ist das **keine** flüchtige
  Ansichts-Einstellung — BPM bestimmt direkt, wie die absolute
  Notenzeit in Takte/Notenwerte gerastert wird, die Korrektur schreibt
  also die gespeicherte MusicXML/MIDI des Jobs dauerhaft neu.
  - Erster Versuch, die bereits gebaute Partitur direkt mathematisch
    umzuskalieren (`music21.Stream.augmentOrDiminish()`), **verworfen**:
    skaliert zwar Notenpositionen/-dauern korrekt, aber nicht die
    Takt-Container drumherum — jeder Takt blieb auf 4.0 ql Sollgröße
    stehen, obwohl der Inhalt jetzt nur noch 2.0 ql füllte (leer-gefüllte,
    falsch gebarte Takte). Mit echten Messwerten verifiziert, dann
    verworfen.
  - Stattdessen umgesetzt: Neues Modul
    [`retempo.py`](backend/app/pipeline/retempo.py) rekonstruiert die
    ursprünglichen absoluten Notenzeiten aus der bereits gebauten
    Partitur (alte BPM invertiert), inklusive Perkussion
    (Notehead/Display-Position → GM-Drum-Taste zurückgerechnet), und
    schickt sie erneut durch `notation.build_score()` — dieselbe,
    bereits getestete Funktion, die auch beim ersten Pipeline-Lauf
    Takte/Notenwerte korrekt aufbaut. Damit muss weder Demucs noch
    Basic Pitch erneut laufen (wären unnötig, da tempo-unabhängig) — nur
    Akkorderkennung (neu vom Quellaudio am neuen Takt-Raster, da sich
    die Taktzahl mit dem Tempo ändert) und Liedtext-Transkription (günstig,
    unabhängig vom Tempo) laufen erneut mit. Dauer dadurch spürbar (~80s
    bei diesem 4-Minuten-Stück), im UI mit Ladehinweis sichtbar gemacht.
    Alte gecachte Varianten (Takte-pro-Zeile/Transpose/PDF/Synth-Audio)
    werden beim Neuaufbau gelöscht, da sie sonst veraltete Daten zeigen
    würden.
  - Verifiziert: kompletter Round-Trip im Browser (½ Tempo → 35 BPM
    "Grave", dann per Eingabefeld zurück auf 70 BPM "Adagio" — Takt-
    Struktur, Akkordsymbole, Klavier-Klammer, Liedtext jeweils korrekt
    neu aufgebaut, Transportleiste und Menü aktualisieren sich automatisch
    über einen neuen `onStatusChange`-Callback an `App.tsx`).

Offen (nächste Runde, laut Nutzer-Entscheidung): bessere
Separations-Modelle für Jazz-/Bläser-Besetzungen evaluieren — dafür
zuerst Git-Repo einrichten und einen Branch aufmachen, damit der
aktuelle (für Jazz unvollkommene, aber funktionierende) Stand erhalten
bleibt.

**Achtzehnte Runde — Basic-Pitch-Parameter pro Instrument getunt**
(2026-10-02, erste Runde im neu eingerichteten Git-Repo, Branch `main`):

- Bisher lief Basic Pitch (`backend/app/pipeline/transcription/pitched.py`)
  für alle Instrumente mit reinen Default-Parametern. Jetzt gibt es ein
  Profil pro `InstrumentTrackType` (Vocals/Piano/Gitarre/Bass/Sonstige/
  Strings/Winds), das drei Stellschrauben gezielt setzt:
  - `minimum_frequency`/`maximum_frequency`: schränkt den Suchraum des
    Modells von vornherein auf den plausiblen Tonumfang des Instruments
    ein (aus denselben Werten wie `local_provider._PLAUSIBLE_MIDI_RANGE`
    abgeleitet, das als Nachfilter weiterhin bestehen bleibt) — das soll
    v.a. Oktav-/Oberton-Fehler bei der schwachen Grundfrequenz einer
    Bassline reduzieren, nicht nur Ausreißer nachträglich wegfiltern.
  - `melodia_trick`: **aus** für polyphone Instrumente (Piano, Gitarre),
    **an** für monophone (Vocals, Bass, Sonstige/Strings/Winds). Direkte
    Lehre aus der siebzehnten Runde (Sax-Trennungs-Experimente, siehe
    `separation-model-experiments`-Branch): `melodia_trick` bevorzugt
    gezielt eine einzelne durchgehende Melodielinie und unterdrückt damit
    nachweislich echte Gleichzeitigkeit — für ein Klavier mit Akkorden
    ist das kontraproduktiv, für eine monophone Gesangsstimme dagegen
    hilfreich (reduziert z.B. Oktav-Verdopplung).
  - `onset_threshold`: für Vocals leicht abgesenkt (0.4 statt 0.5), da
    gesungene Töne oft weich/legato einsetzen statt mit scharfem Attack.
- Verifiziert: Vergleich alt/neu auf bereits getrennten Stems zeigt
  plausible, moderate Verschiebungen (z.B. Piano 1112→1023 Notenereignisse,
  Vocals 342→368) — keine Ausreißer, die auf einen Konfigurationsfehler
  hindeuten würden. Kompletter End-to-End-Durchlauf (neuer Upload,
  komplette Pipeline inkl. Demucs) fehlerfrei, PDF-Export optisch
  unauffällig (dichte, aber saubere Klavier-Akkorde — passend zu
  `melodia_trick=False`).
- Keine Vorher-Nachher-Genauigkeitsmessung gegen eine echte Partitur
  möglich in dieser Runde (kein Testsong mit Originalnoten griffbereit,
  bei dem sich die neuen Parameter von den alten unterscheiden sollten) —
  die Änderungen sind fundiert begründet (siehe oben), aber die tatsächliche
  Genauigkeits-Verbesserung ist noch nicht mit Ground Truth verifiziert.

**Neunzehnte Runde — Ground-Truth-Validierung des Basic-Pitch-Tunings,
plus OMR-Vergleich (oemer vs. Audiveris)** (2026-10-02):

- Testsong mit echter Ground Truth gefunden: "In ritm de Jazz" (Andrei
  Baicoianu), Alto Sax/Piano/Bass/Drums. Vom Nutzer bereitgestellt: Original-
  MP3, echte Noten-PDF (7 Seiten, nicht gemeinfrei — nur intern zur Analyse
  verwendet, nicht reproduziert) und ein Band-in-a-Box-generiertes MIDI
  (8 Spuren inkl. "Melody (BB)" = Alto-Sax-Linie) als präzise Ground Truth.
  (Ein erster Versuch mit "In A Sentimental Mood"-Noten scheiterte, da sich
  die vom Nutzer gefundene Partitur als andere, generische Bigband-Fassung
  herausstellte, nicht die tatsächlich transkribierte Aufnahme — nur
  Tempo/Tonart ließen sich damit noch sinnvoll gegenprüfen.)
- Methodik: eigene Pipeline einmal komplett durchlaufen lassen
  (`storage/jobs/3cc3cbd1-...`), pro Instrument-Stem das exportierte
  `part.mid` gegen die Ground-Truth-MIDI-Spuren verglichen. Zeitversatz
  zwischen beiden Zeitachsen (~1,85s) nicht geschätzt, sondern über
  Kreuzkorrelation der Onset-Zeitreihen exakt bestimmt (Korrelations-Peak
  355 vs. Mittelwert 16,25, >20-fach über Rauschen).
- Ergebnis:
  - **Tempo (89 BPM) und Tonart (As-Dur, 4 b) exakt korrekt erkannt.**
  - **Bass: sehr stark** — 82% exakte Tonhöhentreffer bei ±0,15s Toleranz,
    97,7% bei ±0,25s, 93,9% Tonklassentreffer (oktavfrei). Bestätigt, dass
    das gezielte Bass-Tuning aus der 18. Runde (enger Frequenzbereich,
    `melodia_trick=True`) greift.
  - **Piano: mittelmäßig** — 52–59% exakte Treffer. Geprüfte Precision
    (28% der von uns erkannten Klavier-Noten finden überhaupt eine
    Entsprechung in der Ground Truth, vs. 43% bei Bass) legt nahe, dass
    Basic Pitch bei polyphonem Klavier spürbar mehr Noten "erfindet"
    (Akkorde, Obertöne, Pedal-Resonanz) als bei monophonen Instrumenten —
    plausibel, aber noch nicht weiter diagnostiziert.
  - **Melodie (Alto Sax): schwach** — nur 12–20% Treffer gegen den
    kombinierten Vocals+Sonstige-Pool (kein eigener Bläser-Stem
    vorhanden). Das ist keine neue Erkenntnis, sondern die erste konkrete
    Zahl für das schon aus der 17. Runde bekannte strukturelle Problem:
    `htdemucs_6s` hat keine eigene Stem-Klasse für Blasinstrumente, das
    Sax-Signal verteilt sich auf andere Stems.
- Daraus resultierende Zusatzfrage (Nutzerwunsch): ob sich aus der
  mitgelieferten Noten-PDF per Optical Music Recognition (OMR) eine
  zweite, unabhängige Ground-Truth-Quelle extrahieren ließe — als
  Machbarkeitscheck für eine mögliche künftige "Noten aus PDF
  einlesen"-Funktion. Zwei OMR-Tools getestet:
  - **oemer** (Python/ONNX, `pip install --target` in isoliertem
    Ordner, kein Projekt-Dependency): lief auf diesem Mac zunächst
    dreimal nicht durch (NumPy: `np.int` entfernt; OpenCV 5 ändert das
    Rückgabe-Shape von `HoughLinesP`, bricht oemers Doppel-Indexierung;
    Zielordner für den Export wird von oemer nicht selbst angelegt) —
    alle drei Bugs in der isolierten Testkopie provisorisch gepatcht, bis
    es durchlief. Ergebnis trotzdem **unbrauchbar**: nur 2 Systeme statt
    4 erkannt (beide fälschlich "Piano" benannt), Tonhöhen musikalisch
    unsinnig (u.a. C#7 als erste Note, wilde Mischung aus Kreuz- und
    B-Vorzeichen innerhalb einer einzigen Tonart). oemer ist primär auf
    klassische Klaviernoten mit gleichförmigem Systemlayout trainiert und
    kommt mit dem gemischten 4-System-Layout (Sax/Klavier-Akkolade/
    Bass/Schlagzeug) nicht zurecht. Seit Jahren kaum gepflegtes
    Forschungsprojekt, daher auch die Versions-Inkompatibilitäten.
  - **Audiveris** (Java, aktiv gepflegt, `.dmg`-Release bringt eigene
    JRE mit — kein separates JDK nötig, kein Systeminstall: `.dmg`
    gemountet, `Audiveris.app` nach `/tmp` kopiert, direkt per
    `java -cp ".../app/*" Audiveris -batch -export"` aufgerufen): lief
    sofort stabil durch, exportiert plausible 5-Systeme-Struktur, Tonart
    exakt korrekt (4 b). Gegen dieselbe Ground-Truth-MIDI geprüft (mit
    per Brute-Force-Suche gefundenem Zeitversatz, da Audiveris' eigene
    Takt-/Tempo-Lesung leicht driftet): Bass-Stimme korrekt als separate
    Stimme erkannt (Grundton-Quinte-Muster passend zu Fm7), aber
    durchgehend eine Oktave zu hoch gelesen (46–50% Tonklassentreffer,
    nur 12% bei exakter Oktave); Melodie 25–40% Treffer (Schwankung je
    nach angenommenem Zeitversatz, bedingt durch leichte Rhythmus-
    Ungenauigkeiten bei Synkopen/Triolen — im Audiveris-Log auch selbst
    als "Measure too long"-Warnungen sichtbar).
  - **Fazit:** Audiveris liefert strukturell und musikalisch sinnvolle,
    wenn auch fehlerbehaftete Ergebnisse (typische, bekannte OMR-
    Fehlerklassen: Oktavfehler, kleine Rhythmusungenauigkeiten) — eine
    realistische Grundlage für eine künftige PDF-Noten-Funktion, aber
    kein Quick-Add (Java-Abhängigkeit, Nachbearbeitung nötig). oemer ist
    für diese Art von Mehrinstrumenten-Notenbild nicht geeignet.
  - GPU-Beschleunigung geprüft: oemer nutzt bereits automatisch
    onnxruntimes `CoreMLExecutionProvider`, aber nur für ~6% der
    Netzwerk-Knoten (102 von 1577) — der Rest läuft auf CPU, da oemers
    U-Net-Architektur Operationen enthält, die CoreML nicht unterstützt.
    Das erklärt die Laufzeit (~15-20 Min/Seite) unabhängig von Mac-GPU/
    Neural-Engine-Verfügbarkeit; eine "reine GPU-Version" gibt es dafür
    nicht, ohne das Modell neu zu exportieren.
- Piano-Precision-Nachfrage: `minimum_note_length` für Piano von Basic
  Pitchs Default (127,7ms) auf 180ms angehoben
  ([`pitched.py`](backend/app/pipeline/transcription/pitched.py)), um
  kurze Falsch-Positive (Pedal-/Resonanz-Artefakte) zu filtern. Per
  Parameter-Sweep (127,7/180/200/250/300ms) gegen dieselbe Ground-Truth-
  MIDI gemessen: Precision steigt von 28–30% auf max. ~35%, deckelt dort
  unabhängig vom Wert, während Recall kontinuierlich einbricht (54%→41%).
  180ms gewählt als Punkt mit noch vertretbarem Recall-Verlust. Die
  Deckelung selbst zeigt: kurze Spontan-Noten sind nicht die
  Haupt­ursache der Piano-Precision — vermutlich Oktav-/Oberton-
  Verdopplung, die genauso lang klingt wie die echte Note und sich
  dadurch nicht herausfiltern lässt.

**Zwanzigste Runde — Grenzen der Melodie-Transkription für Blasinstrumente
abschließend geklärt, Produkt-Scope-Entscheidung** (2026-10-03):

- Ausgangsfrage (Nutzer): sind wir mit frei verfügbaren Mitteln überhaupt
  in der Lage, aus einer MP3 ein realitätsnahes Notenblatt zu erzeugen —
  oder sollten wir das Projekt in der aktuellen Form beenden? Keine
  weiteren Vermutungen, nur Messungen.
- Drei weitere, unabhängige Trennungsansätze für die Alto-Sax-Melodie des
  Baicoianu-Testsongs quantitativ gegen die MIDI-Ground-Truth geprüft
  (exakt dieselbe Methodik wie in der 19. Runde: Noten extrahieren, per
  Kreuzkorrelation zeitlich ausrichten, Precision/Recall messen):
  - **BS-Roformer** (MVSep Mega 53 Stems, dedizierter `saxophone`-
    Checkpoint, Setup aus dem `separation-model-experiments`-Branch
    wiederverwendet; läuft über Apple-GPU/MPS, ~6,5 Min für den ganzen
    Song): 7,9% exakte Treffer, 15,6% zusätzlich exakt eine Oktave zu
    tief (korrigierbar), **76,2% ohne jede Entsprechung** — selbst mit
    Oktavkorrektur maximal 23,8% Tonklassentreffer. Precision 8,1%.
  - **UVR VR-Architektur** (`17_HP-Wind_Inst-UVR.pth`, dedizierter
    `woodwinds`-Stem, über `audio-separator`-Paket, kostenlos/lokal,
    ~49s für den ganzen Song — deutlich schneller als BS-Roformer,
    aber qualitativ schwächer): 4,3% exakte Treffer, **88,0% ohne jede
    Entsprechung**. Precision 5,6%.
  - Zusätzlich recherchiert (nicht selbst getestet, da Account-Anlage bei
    Drittdiensten nicht zulässig): kommerzielle APIs mit explizitem
    Wind-Instruments-Stem existieren (LALAL.AI, Moises) — ob sie
    tatsächlich besser abschneiden, bleibt offen, da ungeprüft.
- Damit liegen jetzt **sechs unabhängige Messpunkte** für die
  Melodiestimme vor (Demucs generisch, BS-Roformer, UVR-VR, zwei OMR-
  Tools, dazu die frühere Zwei-Saxophon-Recherche im
  `separation-model-experiments`-Branch) — keiner über ~25%, zwei
  davon mit Modellen, die speziell für genau dieses Problem gebaut
  wurden. Das ist keine Tuning-Frage mehr, sondern eine Grenze der
  heute frei verfügbaren Trennungs- und Transkriptionsmodelle für diese
  Art von Aufnahme (alte, vermutlich mono gemischte Jazz-Aufnahme,
  Blasinstrument-Obertöne überlappen stark mit Klavier/Bass).
- **Entscheidung (Nutzer):** Projekt-Scope ehrlich einschränken statt
  beenden. Die Pipeline bleibt wie sie ist — sie liefert für
  Gesang/Bass/Klavier/Gitarre/Schlagzeug (Tempo/Tonart 100%, Bass
  82–98%, Piano ~50% Recall) einen alltagstauglichen Nutzen. Für
  Blasinstrumente/Jazz-Soli als Melodieträger bleibt manuelle
  Nachkorrektur nötig — das ist eine bekannte, durch Messung belegte,
  mit heutigen Mitteln nicht lösbare Grenze, keine Qualitätslücke der
  eigenen Implementierung.
