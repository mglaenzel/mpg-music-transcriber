import { OpenSheetMusicDisplay } from "opensheetmusicdisplay";
import { useEffect, useRef, useState } from "react";
import {
  correctBpm,
  midiUrl,
  musicXmlForMuseScoreUrl,
  musicXmlUrl,
  pdfUrl,
  sourceAudioUrl,
  synthAudioUrl,
  TRANSPOSING_INSTRUMENT_PRESETS,
  type JobStatusResponse,
  type TrackSummary,
  type Transpositions,
} from "../lib/api";

interface Props {
  jobId: string;
  status: JobStatusResponse;
  // Called after a successful BPM correction so the parent re-fetches
  // job status (bpm/tempo_italian_term/tracks all change server-side —
  // see correctBpm/retempo.rebuild_with_bpm) instead of showing the
  // stale pre-correction tempo in the transport bar.
  onStatusChange: () => void;
}

interface DisplayTrack {
  // The track_id used as this toggle's own identity/key. For a merged
  // group this is the first member's id (e.g. "piano").
  displayId: string;
  label: string;
  // The real track_ids this single toggle controls. Piano+Bass are
  // notated as one braced grand staff (see notation.py's StaffGroup) —
  // toggling them independently would let you hide the bass clef while
  // the brace and the piano clef stay, which doesn't correspond to
  // anything meaningful in the score. So they're offered as one "Piano"
  // toggle that shows/hides both staff lines together.
  memberIds: string[];
}

function getDisplayTracks(tracks: TrackSummary[]): DisplayTrack[] {
  const mergePianoBass =
    tracks.some((t) => t.track_id === "piano") && tracks.some((t) => t.track_id === "bass");
  const result: DisplayTrack[] = [];
  for (const t of tracks) {
    if (mergePianoBass && t.track_id === "bass") continue; // folded into the piano entry below
    if (mergePianoBass && t.track_id === "piano") {
      result.push({ displayId: "piano", label: t.label, memberIds: ["piano", "bass"] });
      continue;
    }
    result.push({ displayId: t.track_id, label: t.label, memberIds: [t.track_id] });
  }
  return result;
}

interface TrackTransposeState {
  semitones: number;
  instrumentName?: string;
}

// UI state keeps transpositions keyed by DisplayTrack.displayId (one
// slider+preset per visible toggle, e.g. one for the merged Piano+Bass
// grand staff) — the backend/URLs need real track_ids, so expand each
// display entry onto all of its memberIds before building a URL.
function expandTranspositions(
  displayTracks: DisplayTrack[],
  byDisplayId: Record<string, TrackTransposeState>,
): Transpositions {
  const result: Transpositions = {};
  for (const t of displayTracks) {
    const state = byDisplayId[t.displayId];
    // A pure rename (0 semitones, name set — e.g. correcting a
    // misidentified "Gitarre" stem to "Tenor-Sax" with no pitch change)
    // must still be included, not just an actual pitch shift.
    if (state && (state.semitones !== 0 || state.instrumentName)) {
      for (const id of t.memberIds) result[id] = state;
    }
  }
  return result;
}

// The display name currently shown for a track — its instrument-preset
// rename if one is active for the DisplayTrack it belongs to, otherwise
// its original label. Needed because renaming a track (see
// expandTranspositions) changes what OSMD's Instrument.Name actually is
// once the score reloads, so anything matching against that (see
// applyVisibility below) has to look here too, not just at
// TrackSummary.label.
function currentTrackLabel(
  trackId: string,
  originalLabel: string,
  displayTracks: DisplayTrack[],
  byDisplayId: Record<string, TrackTransposeState>,
): string {
  const display = displayTracks.find((t) => t.memberIds.includes(trackId));
  const state = display && byDisplayId[display.displayId];
  return state?.instrumentName || originalLabel;
}

// The pipeline's default density (see notation.DEFAULT_MEASURES_PER_SYSTEM)
// and the OSMD zoom that was tuned to make it fit the container without
// overflow. The "Takte pro Zeile" slider scales zoom proportionally from
// this baseline — more measures/line -> smaller notes, same idea the PDF
// export uses server-side for its own page-fit scaling.
const BASE_MEASURES_PER_SYSTEM = 4;
const BASE_ZOOM = 0.7;

function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds)) return "0:00";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export default function ScoreViewer({ jobId, status, onStatusChange }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const osmdRef = useRef<OpenSheetMusicDisplay | null>(null);
  const audioRef = useRef<HTMLAudioElement>(null);
  const rafRef = useRef<number | null>(null);
  const lastCursorSyncRef = useRef<number>(0);

  const [hiddenInstruments, setHiddenInstruments] = useState<Set<string>>(new Set());
  const [pdfInstruments, setPdfInstruments] = useState<Set<string>>(new Set());
  const [audioSource, setAudioSource] = useState<"original" | "synth">("original");
  const [menuOpen, setMenuOpen] = useState(false);
  const [showLyrics, setShowLyrics] = useState(true);
  const [measuresPerSystem, setMeasuresPerSystem] = useState(BASE_MEASURES_PER_SYSTEM);
  const mpsDebounceRef = useRef<number | null>(null);
  const [transpositionsByDisplay, setTranspositionsByDisplay] = useState<
    Record<string, TrackTransposeState>
  >({});
  const tpDebounceRef = useRef<number | null>(null);
  const [pdfState, setPdfState] = useState<{ loading: boolean; error: string | null }>({
    loading: false,
    error: null,
  });
  const [bpmInput, setBpmInput] = useState("");
  const [bpmState, setBpmState] = useState<{ loading: boolean; error: string | null }>({
    loading: false,
    error: null,
  });

  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);

  const bpm = status.bpm ?? 120;
  const displayTracks = getDisplayTracks(status.tracks);

  useEffect(() => {
    if (!containerRef.current) return;
    const osmd = new OpenSheetMusicDisplay(containerRef.current, {
      autoResize: true,
      followCursor: true,
      drawTitle: true,
      // Honor the explicit system breaks the backend inserts (every
      // BASE_MEASURES_PER_SYSTEM measures by default, or whatever the
      // "Takte pro Zeile" slider requested), instead of OSMD auto-wrapping
      // by container width. Note this is still width-dependent: with 6
      // dense staves, a high measures/line count can still overflow the
      // container regardless of this flag — the zoom scaling below is what
      // actually keeps it fitting in practice.
      newSystemFromXML: true,
    });
    osmdRef.current = osmd;

    loadScore(BASE_MEASURES_PER_SYSTEM);

    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      if (mpsDebounceRef.current) window.clearTimeout(mpsDebounceRef.current);
      if (tpDebounceRef.current) window.clearTimeout(tpDebounceRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId]);

  function loadScore(mps: number, tpByDisplayOverride?: Record<string, TrackTransposeState>) {
    const osmd = osmdRef.current;
    if (!osmd) return;
    const tpByDisplay = tpByDisplayOverride ?? transpositionsByDisplay;
    const tp = expandTranspositions(displayTracks, tpByDisplay);
    osmd.zoom = BASE_ZOOM * (BASE_MEASURES_PER_SYSTEM / mps);
    osmd
      .load(musicXmlUrl(jobId, mps === BASE_MEASURES_PER_SYSTEM ? undefined : mps, tp))
      .then(() => {
        // A fresh load resets OSMD's own Instrument.Visible/RenderLyrics
        // state, so re-apply whatever the user had already toggled.
        if (osmd.EngravingRules) osmd.EngravingRules.RenderLyrics = showLyrics;
        applyVisibility(hiddenInstruments, tpByDisplay);
        osmd.render();
        osmd.cursor.show();
      })
      .catch((err) => console.error("OSMD load failed", err));
  }

  function handleMeasuresPerSystemChange(e: React.ChangeEvent<HTMLInputElement>) {
    const mps = Number(e.target.value);
    setMeasuresPerSystem(mps);
    // Debounced: re-fetching/re-rendering the whole score on every slider
    // tick (up to ~60/s while dragging) would be wasteful and janky —
    // only actually reload once the user pauses on a value.
    if (mpsDebounceRef.current) window.clearTimeout(mpsDebounceRef.current);
    mpsDebounceRef.current = window.setTimeout(() => loadScore(mps), 400);
  }

  // Unlike every other control in this menu, this isn't a view parameter
  // — it rebuilds the job's stored notation server-side at the new tempo
  // (see correctBpm/retempo.rebuild_with_bpm), so it's slow (tens of
  // seconds) and not debounced; the buttons/input stay disabled for the
  // duration via bpmState.loading instead.
  async function applyBpmCorrection(newBpm: number) {
    if (!Number.isFinite(newBpm) || newBpm < 20 || newBpm > 400) {
      setBpmState({ loading: false, error: "BPM muss zwischen 20 und 400 liegen." });
      return;
    }
    setBpmState({ loading: true, error: null });
    try {
      await correctBpm(jobId, Math.round(newBpm));
      setBpmInput("");
      onStatusChange();
      // The whole notation changed (different measure count/bar
      // structure at the new tempo) — reload the OSMD score, not just
      // re-render it. Current view settings (slider, transpositions)
      // carry over since loadScore reads them from state as usual.
      loadScore(measuresPerSystem);
      setBpmState({ loading: false, error: null });
    } catch (err) {
      setBpmState({
        loading: false,
        error: err instanceof Error ? err.message : "Tempo-Korrektur fehlgeschlagen.",
      });
    }
  }

  // Raw slider drag: shift by semitones, but drop any instrument-preset
  // rename that was active — a manually-dragged value is a custom shift
  // again, not necessarily "the" named instrument anymore even if it
  // happens to land on the same semitone count.
  function handleTransposeChange(displayId: string, semitones: number) {
    // Compute the new map directly instead of reading state back inside
    // the debounced callback below — setState is async, so a closure
    // that reads transpositionsByDisplay from state would still see the
    // pre-change value when the timeout fires (same reasoning as
    // handleMeasuresPerSystemChange's local `mps` above).
    const next = { ...transpositionsByDisplay, [displayId]: { semitones } };
    setTranspositionsByDisplay(next);
    if (tpDebounceRef.current) window.clearTimeout(tpDebounceRef.current);
    tpDebounceRef.current = window.setTimeout(() => loadScore(measuresPerSystem, next), 400);
  }

  // Instrument-preset dropdown: sets semitones *and* the display name
  // together. "Konzerttonhöhe (C)" (0 semitones, see
  // TRANSPOSING_INSTRUMENT_PRESETS) is the reset option — 0 semitones
  // means expandTranspositions() drops the entry entirely, so the track
  // reverts to untransposed and un-renamed automatically.
  function handleTransposePreset(displayId: string, semitones: number, instrumentName: string) {
    const next = {
      ...transpositionsByDisplay,
      [displayId]: semitones === 0 ? { semitones: 0 } : { semitones, instrumentName },
    };
    setTranspositionsByDisplay(next);
    if (tpDebounceRef.current) window.clearTimeout(tpDebounceRef.current);
    tpDebounceRef.current = window.setTimeout(() => loadScore(measuresPerSystem, next), 400);
  }

  // Free-text relabel, independent of any pitch shift — e.g. correcting
  // a Demucs stem that the separation mislabelled (a bled-through
  // saxophone that ended up as the "guitar" stem, with no instrument in
  // the piece Demucs actually has a class for). Keeps whatever semitone
  // shift, if any, is already set; an empty name clears back to the
  // track's original label.
  function handleRenameChange(displayId: string, name: string) {
    const current = transpositionsByDisplay[displayId] ?? { semitones: 0 };
    const next = {
      ...transpositionsByDisplay,
      [displayId]: { ...current, instrumentName: name || undefined },
    };
    setTranspositionsByDisplay(next);
    if (tpDebounceRef.current) window.clearTimeout(tpDebounceRef.current);
    tpDebounceRef.current = window.setTimeout(() => loadScore(measuresPerSystem, next), 400);
  }

  // memberIds covers a whole DisplayTrack group (e.g. ["piano", "bass"] for
  // the merged grand-staff toggle) — all members are shown or hidden
  // together, never independently.
  function toggleInstrument(memberIds: string[]) {
    setHiddenInstruments((prev) => {
      const next = new Set(prev);
      const allHidden = memberIds.every((id) => next.has(id));
      for (const id of memberIds) {
        if (allHidden) next.delete(id);
        else next.add(id);
      }
      applyVisibility(next);
      return next;
    });
  }

  function applyVisibility(
    hidden: Set<string>,
    tpByDisplayOverride?: Record<string, TrackTransposeState>,
  ) {
    const osmd = osmdRef.current;
    if (!osmd?.Sheet) return;
    const tpByDisplay = tpByDisplayOverride ?? transpositionsByDisplay;
    // music21's MusicXML writer assigns its own generated `id` to each
    // <score-part> (e.g. "P05a8dd43...") regardless of the Part.id we set
    // when building the score — so OSMD's Instrument.Id never matches our
    // track_id values (e.g. "vocals"). Instrument.Name *does* reliably
    // reflect the label we set (inst.instrumentName in notation.py) —
    // or, once a track has an active instrument-preset rename (e.g.
    // "Alto-Sax"), *that* name instead, since that's what the reloaded
    // score now actually calls it. So match hidden track_ids to OSMD
    // instruments via their current label instead.
    const hiddenLabels = new Set(
      status.tracks
        .filter((t) => hidden.has(t.track_id))
        .map((t) => currentTrackLabel(t.track_id, t.label, displayTracks, tpByDisplay)),
    );
    for (const instrument of osmd.Sheet.Instruments) {
      instrument.Visible = !hiddenLabels.has(instrument.Name);
    }
    osmd.render();
  }

  function toggleLyrics() {
    setShowLyrics((prev) => {
      const next = !prev;
      const osmd = osmdRef.current;
      if (osmd?.EngravingRules) {
        osmd.EngravingRules.RenderLyrics = next;
        osmd.render();
      }
      return next;
    });
  }

  function togglePdfInstrument(memberIds: string[]) {
    setPdfInstruments((prev) => {
      const next = new Set(prev);
      const allSelected = memberIds.every((id) => next.has(id));
      for (const id of memberIds) {
        if (allSelected) next.delete(id);
        else next.add(id);
      }
      return next;
    });
  }

  // A plain <a href download> can't surface a server error — a failed
  // request there just looks like a silently-broken/empty download, which
  // is exactly what confused testing when native PDF export was disabled
  // (502 with an explanation the user never saw). Fetch it instead so a
  // non-OK response's JSON `detail` can be shown directly in the menu.
  async function downloadPdf() {
    const url = pdfUrl(
      jobId,
      Array.from(pdfInstruments),
      measuresPerSystem,
      expandTranspositions(displayTracks, transpositionsByDisplay),
    );
    setPdfState({ loading: true, error: null });
    try {
      const res = await fetch(url, { cache: "no-store" });
      if (!res.ok) {
        let detail = `PDF-Export fehlgeschlagen (HTTP ${res.status}).`;
        try {
          const body = await res.json();
          if (body?.detail) detail = body.detail;
        } catch {
          // response wasn't JSON — keep the generic message
        }
        setPdfState({ loading: false, error: detail });
        return;
      }
      const blob = await res.blob();
      const objectUrl = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = objectUrl;
      a.download = "transkription.pdf"; // only used if the header below is missing/unparsable
      const disposition = res.headers.get("content-disposition");
      // FastAPI's FileResponse uses RFC 5987 encoding (filename*=UTF-8''...,
      // percent-escaped) whenever the name has non-ASCII characters — which
      // it always does here, e.g. "ü" in a German song title — not the
      // plain filename="..." form. Try that first and fall back to plain.
      const encodedMatch = disposition?.match(/filename\*=UTF-8''([^;]+)/i);
      const plainMatch = disposition?.match(/filename="?([^";]+)"?/i);
      if (encodedMatch) a.download = decodeURIComponent(encodedMatch[1]);
      else if (plainMatch) a.download = plainMatch[1];
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(objectUrl);
      setPdfState({ loading: false, error: null });
    } catch (err) {
      setPdfState({
        loading: false,
        error: err instanceof Error ? err.message : "PDF-Export fehlgeschlagen.",
      });
    }
  }

  // Cursor sync: map audio currentTime -> whole-note position (via BPM,
  // assuming 4/4 as set by the notation pipeline) -> advance OSMD cursor.
  // The cursor can only move *forward* via .next() — after a seek
  // backward (including restarting the song from 0), the cursor is ahead
  // of the target and .next() alone can never get it back there, so it
  // just sits still until playback catches up to where it used to be.
  // Detect that case and .reset() the cursor to the start first.
  function syncCursorToTime(currentTimeSec: number) {
    const osmd = osmdRef.current;
    if (!osmd?.cursor) return;

    const secondsPerWholeNote = (60 / bpm) * 4;
    const targetWholeNotes = currentTimeSec / secondsPerWholeNote;

    if (osmd.cursor.iterator.currentTimeStamp.RealValue > targetWholeNotes) {
      osmd.cursor.reset();
    }

    let guard = 0;
    while (
      !osmd.cursor.iterator.EndReached &&
      osmd.cursor.iterator.currentTimeStamp.RealValue < targetWholeNotes &&
      guard++ < 10000
    ) {
      osmd.cursor.next();
    }
  }

  function advanceCursor() {
    const audio = audioRef.current;
    if (!audio) return;
    const now = performance.now();
    // Throttled, not per-frame: OSMD's followCursor auto-scrolls the page
    // to keep the cursor in view, and calling that on every animation
    // frame (~60/s) — especially across a system/line change, where the
    // scroll distance is largest — kept restarting the browser's smooth-
    // scroll animation before it could finish, which is what showed up as
    // several seconds of visible shaking. Nudging the cursor a few times a
    // second instead lets each scroll settle.
    if (now - lastCursorSyncRef.current >= 200) {
      lastCursorSyncRef.current = now;
      syncCursorToTime(audio.currentTime);
    }
    rafRef.current = requestAnimationFrame(advanceCursor);
  }

  function handlePlay() {
    setIsPlaying(true);
    rafRef.current = requestAnimationFrame(advanceCursor);
  }

  function handlePause() {
    setIsPlaying(false);
    if (rafRef.current) cancelAnimationFrame(rafRef.current);
  }

  function togglePlay() {
    const audio = audioRef.current;
    if (!audio) return;
    if (audio.paused) void audio.play();
    else audio.pause();
  }

  function handleSeek(e: React.ChangeEvent<HTMLInputElement>) {
    const audio = audioRef.current;
    if (!audio) return;
    audio.currentTime = Number(e.target.value);
    setCurrentTime(audio.currentTime);
    syncCursorToTime(audio.currentTime);
  }

  const activeTranspositions = expandTranspositions(displayTracks, transpositionsByDisplay);
  const audioUrl =
    audioSource === "original"
      ? sourceAudioUrl(jobId)
      : synthAudioUrl(jobId, activeTranspositions);

  return (
    <div className="score-viewer">
      <audio
        ref={audioRef}
        key={audioUrl}
        src={audioUrl}
        onPlay={handlePlay}
        onPause={handlePause}
        onTimeUpdate={(e) => setCurrentTime(e.currentTarget.currentTime)}
        onLoadedMetadata={(e) => setDuration(e.currentTarget.duration)}
        hidden
      />

      {/* Fixed transport bar: always reachable, independent of the
          auto-scrolling cursor-follow behavior in the sheet below. */}
      <div className="transport-bar">
        <button
          className="transport-bar__play"
          onClick={togglePlay}
          aria-label={isPlaying ? "Pause" : "Abspielen"}
        >
          {isPlaying ? "⏸" : "▶"}
        </button>
        <span className="transport-bar__time">{formatTime(currentTime)}</span>
        <input
          className="transport-bar__seek"
          type="range"
          min={0}
          max={duration || 0}
          step={0.1}
          value={currentTime}
          onChange={handleSeek}
        />
        <span className="transport-bar__time">{formatTime(duration)}</span>

        <div className="tempo-display tempo-display--compact">
          <span className="tempo-display__term">{status.tempo_italian_term ?? ""}</span>
          <span className="tempo-display__value">♩ = {status.bpm ?? "–"}</span>
        </div>

        <button
          className="transport-bar__menu-toggle"
          onClick={() => setMenuOpen((v) => !v)}
          aria-label="Menü"
        >
          ☰
        </button>
      </div>

      {menuOpen && (
        <div className="options-menu">
          <section>
            <h3>
              Tempo korrigieren: {bpm} BPM ({status.tempo_italian_term ?? "–"})
            </h3>
            <div className="bpm-correction">
              <div className="bpm-correction__buttons">
                <button
                  type="button"
                  disabled={bpmState.loading}
                  onClick={() => applyBpmCorrection(bpm / 2)}
                >
                  ½ Tempo
                </button>
                <button
                  type="button"
                  disabled={bpmState.loading}
                  onClick={() => applyBpmCorrection(bpm * 2)}
                >
                  2× Tempo
                </button>
                <input
                  type="number"
                  min={20}
                  max={400}
                  placeholder="eigener BPM-Wert"
                  value={bpmInput}
                  onChange={(e) => setBpmInput(e.target.value)}
                  disabled={bpmState.loading}
                />
                <button
                  type="button"
                  disabled={bpmState.loading || !bpmInput}
                  onClick={() => applyBpmCorrection(Number(bpmInput))}
                >
                  Anwenden
                </button>
              </div>
              {bpmState.loading && (
                <p className="bpm-correction__status">
                  Partitur wird mit korrigiertem Tempo neu aufgebaut — das dauert eine
                  Weile (Akkord- und Liedtext-Erkennung laufen erneut) …
                </p>
              )}
              {bpmState.error && <p className="bpm-correction__error">{bpmState.error}</p>}
            </div>
          </section>

          <section>
            <h3>Instrumente anzeigen</h3>
            <div className="instrument-toggles">
              {displayTracks.map((t) => (
                <label key={t.displayId} className="instrument-toggle">
                  <input
                    type="checkbox"
                    checked={!hiddenInstruments.has(t.displayId)}
                    onChange={() => toggleInstrument(t.memberIds)}
                  />
                  {t.label}
                </label>
              ))}
              <label className="instrument-toggle">
                <input type="checkbox" checked={showLyrics} onChange={toggleLyrics} />
                Liedtext
              </label>
            </div>
          </section>

          <section>
            <h3>Takte pro Zeile: {measuresPerSystem}</h3>
            <input
              className="measures-per-system-slider"
              type="range"
              min={2}
              max={8}
              step={1}
              value={measuresPerSystem}
              onChange={handleMeasuresPerSystemChange}
            />
          </section>

          <section>
            <h3>Transponieren</h3>
            <div className="transpose-tracks">
              {displayTracks.map((t) => {
                const state = transpositionsByDisplay[t.displayId];
                const semitones = state?.semitones ?? 0;
                const displayName = state?.instrumentName || t.label;
                return (
                  <div key={t.displayId} className="transpose-track">
                    <span className="transpose-track__label">
                      {displayName}
                      {semitones !== 0 && (
                        <span className="transpose-track__value">
                          {" "}
                          ({semitones > 0 ? "+" : ""}
                          {semitones})
                        </span>
                      )}
                    </span>
                    <input
                      type="range"
                      min={-24}
                      max={24}
                      step={1}
                      value={semitones}
                      onChange={(e) =>
                        handleTransposeChange(t.displayId, Number(e.target.value))
                      }
                    />
                    <select
                      value=""
                      onChange={(e) => {
                        if (e.target.value === "") return;
                        const preset = TRANSPOSING_INSTRUMENT_PRESETS[Number(e.target.value)];
                        handleTransposePreset(t.displayId, preset.semitones, preset.label);
                      }}
                    >
                      <option value="">Instrumenten-Preset …</option>
                      {TRANSPOSING_INSTRUMENT_PRESETS.map((p, i) => (
                        <option key={p.label} value={i}>
                          {p.label}
                        </option>
                      ))}
                    </select>
                    <input
                      type="text"
                      className="transpose-track__rename"
                      placeholder={`Eigener Name (z.B. falsch erkanntes Instrument korrigieren) …`}
                      value={state?.instrumentName ?? ""}
                      onChange={(e) => handleRenameChange(t.displayId, e.target.value)}
                    />
                  </div>
                );
              })}
            </div>
          </section>

          <section>
            <h3>Wiedergabe-Quelle</h3>
            <div className="audio-source-toggle">
              <label>
                <input
                  type="radio"
                  name="audio-source"
                  checked={audioSource === "original"}
                  onChange={() => setAudioSource("original")}
                />
                Original-MP3
              </label>
              <label>
                <input
                  type="radio"
                  name="audio-source"
                  checked={audioSource === "synth"}
                  onChange={() => setAudioSource("synth")}
                />
                Synth. Wiedergabe
              </label>
            </div>
          </section>

          <section>
            <h3>Downloads</h3>
            <div className="options-menu__downloads">
              <a href={musicXmlForMuseScoreUrl(jobId, activeTranspositions)} download>
                MusicXML herunterladen
              </a>
              <a href={midiUrl(jobId, activeTranspositions)} download>
                MIDI herunterladen
              </a>
            </div>
          </section>

          <section>
            <h3>PDF-Export – Instrumente wählen (leer = alle)</h3>
            <div className="instrument-toggles">
              {displayTracks.map((t) => (
                <label key={t.displayId} className="instrument-toggle">
                  <input
                    type="checkbox"
                    checked={t.memberIds.every((id) => pdfInstruments.has(id))}
                    onChange={() => togglePdfInstrument(t.memberIds)}
                  />
                  {t.label}
                </label>
              ))}
            </div>
            <button
              className="pdf-download-button"
              onClick={downloadPdf}
              disabled={pdfState.loading}
            >
              {pdfState.loading ? "PDF wird erzeugt …" : "PDF herunterladen"}
            </button>
            {pdfState.error && <p className="pdf-download-error">{pdfState.error}</p>}
          </section>
        </div>
      )}

      <div ref={containerRef} className="score-viewer__sheet" />
    </div>
  );
}
