# Separations-Modell-Experimente (Branch: `separation-model-experiments`)

Ausgangspunkt: Testsong "In A Sentimental Mood" (Gerry Mulligan, *California
Concerts Volume 2*) — Bariton-Sax (Mulligan), Tenor-Sax (Zoot Sims), Trompete
(Jon Eardley), Ventilposaune + Piano (Bob Brookmeyer), Bass (Red Mitchell),
Drums (Larry Bunker). Kein Gesang, keine Gitarre. Unsere Produktions-Pipeline
nutzt `htdemucs_6s`, das nur Vocals/Piano/Bass/Gitarre/Drums/Sonstige kennt —
für die vier Blasinstrumente (2× Sax, Trompete, Posaune) gibt es dort gar
keine eigene Kategorie, sie landen zwangsweise verteilt auf "Sonstige",
teils auch fälschlich auf "Vocals"/"Gitarre" (siehe README.md im
Projekt-Root, 16. Runde).

Ziel dieser Experimente: speziell die **zwei gleichzeitig spielenden
Saxophone** (Bariton + Tenor) sauber voneinander trennen. Ergebnis: **nicht
erreicht** mit aktuell frei verfügbaren Mitteln — siehe unten, warum.

## 1. BS-Roformer Einzelinstrument-Modelle (MVSep Mega 53 Stems)

[ZFTurbo/Music-Source-Separation-Training](https://github.com/ZFTurbo/Music-Source-Separation-Training)
(MIT-Lizenz) stellt über Hugging Face
([noblebarkrr/BS-Roformer-MVSep-Mega-53-stems](https://huggingface.co/noblebarkrr/BS-Roformer-MVSep-Mega-53-stems))
53 einzelne Checkpoints bereit (je ~78 MB), darunter explizit `saxophone`,
`trumpet`, `trombone`, `brass`, `french-horn`, `clarinet` — genau die
Kategorien, die Demucs nicht hat.

**Reproduzieren:**
```bash
cd experiments/separation-models
git clone https://github.com/ZFTurbo/Music-Source-Separation-Training.git msst
uv venv --python 3.11 msst/.venv
uv pip install -p msst/.venv/bin/python torch torchaudio numpy soundfile \
  librosa tqdm ml_collections omegaconf einops rotary_embedding_torch \
  beartype pyyaml matplotlib

mkdir -p checkpoints
BASE="https://huggingface.co/noblebarkrr/BS-Roformer-MVSep-Mega-53-stems/resolve/main/v1"
for stem in saxophone trumpet trombone; do
  curl -sL -o "checkpoints/bs_mega_53stem_${stem}_mvsep.ckpt" "${BASE}/bs_mega_53stem_${stem}_mvsep.ckpt"
  curl -sL -o "checkpoints/bs_mega_53stem_${stem}_mvsep_config.yaml" "${BASE}/bs_mega_53stem_${stem}_mvsep_config.yaml"
done

cd msst && source .venv/bin/activate
python3 inference.py \
  --model_type bs_roformer \
  --config_path ../checkpoints/bs_mega_53stem_saxophone_mvsep_config.yaml \
  --start_check_point ../checkpoints/bs_mega_53stem_saxophone_mvsep.ckpt \
  --input_folder ../test_input --store_dir ../test_output
```

**Ergebnisse:**
- Funktioniert, MIT-lizenziert, läuft auch ohne Nvidia-GPU.
- **Apple-GPU (MPS) funktioniert entgegen erster Vermutung** — ZFTurbos
  eigenes `inference.py` erkennt MPS automatisch (`torch.backends.mps`),
  nur das separate `bs-roformer-infer`-Wrapper-Paket hat MPS bewusst
  deaktiviert. Gemessen an einem 25s-Clip: CPU 143s, MPS 85s (~1,7× schneller
  — deutlich bescheidener als Demucs' Speedup, vermutlich weil Teile der
  Transformer-Attention auf MPS nicht beschleunigt laufen).
- Hochgerechnet auf einen ganzen 4-Minuten-Song: ~12-25 Min **pro
  Instrument** (mit/ohne GPU) — bei 5-6 relevanten Spuren ist das ein
  Vielfaches der Demucs-Laufzeit.
- **Kernproblem:** Das Modell trennt nach *Klangfarbe* ("klingt wie ein
  Saxophon"), nicht nach *Musiker*. Im Hörtest waren in der
  "Saxophone"-Spur eindeutig **beide** Saxophonisten zu hören, nicht
  getrennt. Das ist erwartbar — das Modell hat keine Vorstellung von
  "Spieler A vs. Spieler B", nur von "Instrumentenklasse".

## 2. Stereo-Panning

Idee: alte Jazz-Aufnahmen sind manchmal hart links/rechts gemischt, dann
reicht simple Kanaltrennung.

**Befund:** Aufnahme ist praktisch mono. L/R-Korrelation 0,9977,
RMS(L-R)/RMS(Mitte) = 0,067 — kein brauchbares Stereo-Signal zum Ausnutzen.

## 3. Tonhöhen-Streaming (Multi-Pitch Streaming)

Idee: Statt nach Klangfarbe zu trennen, die von Basic Pitch bereits
transkribierten Noten nachträglich nach Tonhöhen-Nähe + zeitlicher
Kontinuität in getrennte Stimmen aufteilen (Forschungsprinzip aus Duan,
Han & Pardo, *"Multi-pitch Streaming of Harmonic Sound Mixtures"*,
IEEE TASLP). Kein Zusatz-Modell nötig, reine Nachbearbeitung der ohnehin
vorhandenen Transkription.

**Wichtiger Zwischenbefund:** Basic Pitch lief bisher mit
`melodia_trick=True` (Standard) und erkannte auf der *unseparierten*
Demucs-"Sonstige"-Spur **null** überlappende Noten (217 Noten, 0 Paare
gleichzeitig) — die Polyphonie geht beim verrauschten Mehrinstrumenten-Signal
schon vor dem Streaming-Schritt verloren. Auf der *sauberen*
BS-Roformer-Sax-Spur (Abschnitt 1) fand Basic Pitch dagegen **74 von 114
Notenpaaren überlappend** — die Trennung aus Abschnitt 1 hilft also messbar,
auch wenn sie für menschliches Hören nicht ausreicht.

Algorithmus: [`voice_streaming.py`](voice_streaming.py), zweistufig —
(1) straffes Greedy-Streaming (enger erlaubter Tonsprung verhindert
Fehlzuordnungen), (2) Zusammenführen kompatibler Fragmente
(zeitlich nicht überlappend + plausibler Tonhöhen-/Zeitabstand) bis zur
Zielanzahl Stimmen.

**Ergebnis:** Reiner Greedy-Durchlauf (Schwelle 12 Halbtöne) ergab 7
Fragmente statt 2 — zu grob. Straffere Schwelle (5 Halbtöne) plus Merge-Schritt
blieb bei 10 Fragmenten hängen, weil die meisten Fragmente fast die gesamte
Clip-Länge überspannten (keine Zeitlücken zum Zusammenführen). Lockerung der
Schwelle (9/14/**20** Halbtöne) reduzierte die Fragmentzahl kaum unter
**4 durchgehende, gleichzeitig aktive Stimmen** — selbst bei einer fast
beliebig lockeren Schwelle. Das ist kein Algorithmus-/Parameter-Problem mehr,
sondern zeigt: Die Eingangsdaten selbst enthalten mehr scheinbar-gleichzeitige
Tonhöhen als die 2 echten Saxophonisten — vermutlich Rest-Durchsprechen von
Trompete/Posaune (deren Obertöne sich mit Sax überlappen) und/oder von Basic
Pitch fälschlich als mehrere Töne interpretiertes Vibrato.

## Fazit

Zwei gleichzeitig spielende, klanglich sehr ähnliche Instrumente (2×
Saxophon) in einer Mono-Aufnahme sauber zu trennen, ist mit den hier
geprüften frei verfügbaren Mitteln **nicht praktikabel erreichbar** — jeder
Ansatz scheiterte an einer grundlegenden, nicht durch Parameter-Tuning
behebbaren Hürde. Das ist vermutlich auch kommerziellen Tools wie
Moises.ai/klang.io nicht zuverlässig möglich (gleiche physikalische
Grundproblematik: nahezu identisches Timbre, keine Stereo-Information).

**Für die Produktions-Pipeline (main-Branch) umgesetzt stattdessen:**
Manuelles Umbenennen einzelner Spuren (15. Runde, siehe Projekt-README) als
pragmatischer Workaround — der Nutzer kann z.B. "Gitarre" (das fälschlich
zugeordnete Sax-Signal) von Hand in "Sax (gemischt)" umbenennen, auch wenn
beide Saxophonisten in einer Spur bleiben.

**Falls das Thema später nochmal aufgegriffen wird:** Der nächste
sinnvolle Ansatzpunkt wäre eine bessere *Trennqualität* (Abschnitt 1) zu
erreichen, bevor Streaming (Abschnitt 3) nochmal versucht wird — z.B. ein
Modell, das gezielt zwischen Sax/Trompete/Posaune trennt (nicht nur
Sax-vs-Rest), um Obertöne benachbarter Blechbläser aus der Sax-Spur
fernzuhalten.

## 4. Nachtrag: zweiter Testsong mit nur *einer* Melodiestimme (kein
Zwei-Spieler-Problem) — Ergebnis trotzdem schlecht

Der obige Befund (Abschnitt 1) betrifft explizit das **Zwei-Spieler-
Problem** (zwei gleichzeitige Saxophone trennen). Im Projekt-README
(Hauptbranch, 19./20. Runde) wurde mit einem zweiten Testsong — "In
ritm de Jazz" (Andrei Baicoianu), nur **eine** Alto-Sax-Melodiestimme,
dazu eine echte Band-in-a-Box-MIDI als Ground Truth — genau dieser
"nächste Ansatzpunkt" geprüft: reicht ein dedizierter Bläser-Stem (statt
Demucs' "Sonstige"-Topf), wenn das Zwei-Spieler-Problem gar nicht
vorliegt?

**Ergebnis: nein, nicht wesentlich besser.**

- **BS-Roformer** (derselbe `saxophone`-Checkpoint wie oben, jetzt auf
  dem Baicoianu-Song): nur 7,9% exakte Notentreffer gegen die MIDI-
  Ground-Truth, 76,2% ganz ohne Entsprechung (selbst mit Oktavkorrektur
  nur 23,8%). Precision 8,1% — schlechter als die 11,6% der *generischen*
  Demucs-"Sonstige"-Spur im selben Vergleich.
- **UVR VR-Architektur** (`17_HP-Wind_Inst-UVR.pth`, dedizierter
  `woodwinds`-Stem, über das `audio-separator`-Paket — kostenlos, lokal,
  ~49s für den ganzen Song, deutlich schneller als BS-Roformer):
  **noch schlechter** — 4,3% exakte Treffer, 88,0% ganz ohne
  Entsprechung, Precision 5,6%.

**Reproduzieren (UVR, zusätzlich zum Setup aus Abschnitt 1):**
```bash
cd experiments/separation-models
uv venv --python 3.11 uvr_venv
uv pip install -p uvr_venv/bin/python "audio-separator[cpu]" imageio-ffmpeg
ln -sf "$(pwd)/uvr_venv/lib/python3.11/site-packages/imageio_ffmpeg/binaries/ffmpeg-macos-aarch64-v7.1" uvr_venv/bin/ffmpeg
chmod +x uvr_venv/bin/ffmpeg  # kein Homebrew/ffmpeg nötig, Binary kommt aus dem Python-Paket
export PATH="$(pwd)/uvr_venv/bin:$PATH"
source uvr_venv/bin/activate
audio-separator <song>.mp3 -m "17_HP-Wind_Inst-UVR.pth" --single_stem woodwinds --output_dir uvr_output
```

**Fazit des Nachtrags:** Selbst wenn das ursprüngliche Zwei-Spieler-
Problem gar nicht vorliegt (nur eine Melodiestimme), bringt ein
dedizierter Bläser-Stem — egal ob Transformer-basiert (BS-Roformer) oder
klassisches VR-Spektrogramm-Modell (UVR) — **keine** verwertbare
Verbesserung gegenüber der generischen Demucs-Trennung. Beide
spezialisierten Modelle lagen sogar unter der Baseline. Das spricht
dafür, dass das Grundproblem nicht (nur) "falscher Stem-Typ" ist,
sondern eher in der Aufnahmequalität selbst liegt (alte, vermutlich
mono gemischte Jazz-Aufnahme, Obertöne von Sax/Klavier/Bass überlappen
stark) — unabhängig vom eingesetzten Trennungsmodell. Damit ist auch
der "nächste Ansatzpunkt" aus dem ursprünglichen Fazit oben als Sackgasse
belegt, nicht nur vermutet. Volle Messmethodik und weitere Zahlen:
Projekt-README, 19./20. Runde.
