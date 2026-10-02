const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export interface TrackSummary {
  track_id: string;
  instrument: string;
  label: string;
}

export type JobStatus =
  | "pending"
  | "separating"
  | "transcribing"
  | "notating"
  | "exporting"
  | "done"
  | "failed";

export interface JobStatusResponse {
  job_id: string;
  status: JobStatus;
  progress: number;
  error: string | null;
  bpm: number | null;
  tempo_italian_term: string | null;
  time_signature: string | null;
  tracks: TrackSummary[];
}

// Mirrors tempo.GENRE_START_BPM (backend) — seeds tempo detection toward
// that genre's typical range, so an ambiguous "octave error" case (e.g.
// a sparse jazz ballad whose true tempo and its exact double are nearly
// equally likely per the raw audio) resolves toward the right one
// instead of leaving it to a near coin-flip. "auto" = no hint, librosa's
// own default behavior.
export const GENRE_OPTIONS: { value: string; label: string }[] = [
  { value: "auto", label: "Automatisch (kein Hinweis)" },
  { value: "ballad", label: "Ballade / langsames Stück" },
  { value: "pop_rock", label: "Pop/Rock (mittleres Tempo)" },
  { value: "dance", label: "Tanzbar / elektronisch" },
  { value: "fast", label: "Sehr schnell (Punk, Drum&Bass, …)" },
];

export async function uploadAudio(
  file: File,
  genre?: string,
): Promise<{ job_id: string }> {
  const form = new FormData();
  form.append("file", file);
  if (genre) form.append("genre", genre);
  const res = await fetch(`${API_BASE_URL}/api/jobs`, { method: "POST", body: form });
  if (!res.ok) throw new Error(`Upload fehlgeschlagen: ${res.status}`);
  return res.json();
}

export async function fetchJobStatus(jobId: string): Promise<JobStatusResponse> {
  const res = await fetch(`${API_BASE_URL}/api/jobs/${jobId}`);
  if (!res.ok) throw new Error(`Job-Status konnte nicht geladen werden: ${res.status}`);
  return res.json();
}

// Not a transient view parameter like transpose/measures-per-system —
// this re-notates and overwrites the job's stored MusicXML/MIDI
// server-side (see retempo.rebuild_with_bpm), so it's slow (tens of
// seconds: re-detects chords and lyrics against the new beat grid) and
// the fix persists across reloads.
export async function correctBpm(
  jobId: string,
  bpm: number,
): Promise<{ bpm: number; tempo_italian_term: string }> {
  const res = await fetch(`${API_BASE_URL}/api/jobs/${jobId}/bpm?bpm=${bpm}`, {
    method: "POST",
  });
  if (!res.ok) {
    let detail = `Tempo-Korrektur fehlgeschlagen (HTTP ${res.status}).`;
    try {
      const body = await res.json();
      if (body?.detail) detail = body.detail;
    } catch {
      // not JSON — keep generic message
    }
    throw new Error(detail);
  }
  return res.json();
}

export interface JobSummary {
  job_id: string;
  status: JobStatus;
  original_filename: string;
  created_at: string;
  bpm: number | null;
  tempo_italian_term: string | null;
}

export async function fetchJobList(): Promise<JobSummary[]> {
  const res = await fetch(`${API_BASE_URL}/api/jobs`);
  if (!res.ok) throw new Error(`Bibliothek konnte nicht geladen werden: ${res.status}`);
  return res.json();
}

// track_id -> transposition. Matches the backend's
// `?transpose=track_id:semitones[:name],...` query format (see
// _parse_transpose_param in jobs.py); a track absent or mapped to 0
// semitones is left untransposed. `instrumentName`, when set (picked via
// an instrument preset, not the raw semitone slider), also relabels the
// part — e.g. "Vocals" shown/exported as "Alto-Sax" — so a track
// transposed to be played by a real instrument is also legible as that
// instrument, not still labelled by the stem it came from.
export interface TrackTranspose {
  semitones: number;
  instrumentName?: string;
}
export type Transpositions = Record<string, TrackTranspose>;

function transposeParam(transpositions?: Transpositions): string | null {
  if (!transpositions) return null;
  // A pure rename (0 semitones, name set) must still be sent — not just
  // an actual pitch shift.
  const pairs = Object.entries(transpositions).filter(
    ([, v]) => v.semitones !== 0 || v.instrumentName,
  );
  if (pairs.length === 0) return null;
  return pairs
    .map(([id, v]) => (v.instrumentName ? `${id}:${v.semitones}:${v.instrumentName}` : `${id}:${v.semitones}`))
    .join(",");
}

// Mirrors notation.TRANSPOSING_INSTRUMENT_PRESETS (backend) — kept in
// sync manually rather than fetched, since it's a small, rarely-changing
// list and this avoids a round trip just to populate a dropdown. The
// first entry (0 semitones, no name) is the "reset" option — picking it
// clears both the shift and any instrument relabeling.
export const TRANSPOSING_INSTRUMENT_PRESETS: { label: string; semitones: number }[] = [
  { label: "Konzerttonhöhe (C)", semitones: 0 },
  { label: "Trompete", semitones: 2 },
  { label: "Klarinette", semitones: 2 },
  { label: "Tenor-Sax", semitones: 2 },
  { label: "Sopran-Sax", semitones: 2 },
  { label: "Alt-Sax", semitones: 9 },
  { label: "Bariton-Sax", semitones: 9 },
  { label: "Horn", semitones: 7 },
  { label: "A-Klarinette", semitones: 3 },
];

export function musicXmlUrl(
  jobId: string,
  measuresPerSystem?: number,
  transpositions?: Transpositions,
): string {
  const params = new URLSearchParams();
  if (measuresPerSystem) params.set("measures_per_system", String(measuresPerSystem));
  const tp = transposeParam(transpositions);
  if (tp) params.set("transpose", tp);
  const q = params.toString();
  return `${API_BASE_URL}/api/jobs/${jobId}/musicxml${q ? `?${q}` : ""}`;
}

// For the "MusicXML herunterladen" link — that file is meant to be opened
// directly in the MuseScore desktop app, not fed to OSMD, so it gets the
// same forced-break-free, scaled layout as our own PDF export instead of
// the fixed-measures-per-line variant musicXmlUrl() uses for the web view.
export function musicXmlForMuseScoreUrl(jobId: string, transpositions?: Transpositions): string {
  const params = new URLSearchParams({ for_musescore: "true" });
  const tp = transposeParam(transpositions);
  if (tp) params.set("transpose", tp);
  return `${API_BASE_URL}/api/jobs/${jobId}/musicxml?${params.toString()}`;
}

export function midiUrl(jobId: string, transpositions?: Transpositions): string {
  const tp = transposeParam(transpositions);
  const q = tp ? `?transpose=${encodeURIComponent(tp)}` : "";
  return `${API_BASE_URL}/api/jobs/${jobId}/midi${q}`;
}

// A playable rendering of the MIDI — browsers can't play a raw .mid file
// via <audio src=...>, so "Synth. Wiedergabe" uses this, not midiUrl().
export function synthAudioUrl(jobId: string, transpositions?: Transpositions): string {
  const tp = transposeParam(transpositions);
  const q = tp ? `?transpose=${encodeURIComponent(tp)}` : "";
  return `${API_BASE_URL}/api/jobs/${jobId}/synth-audio${q}`;
}

export function pdfUrl(
  jobId: string,
  instruments?: string[],
  measuresPerSystem?: number,
  transpositions?: Transpositions,
): string {
  const params = new URLSearchParams();
  if (instruments && instruments.length > 0) params.set("instruments", instruments.join(","));
  if (measuresPerSystem) params.set("measures_per_system", String(measuresPerSystem));
  const tp = transposeParam(transpositions);
  if (tp) params.set("transpose", tp);
  const q = params.toString();
  return `${API_BASE_URL}/api/jobs/${jobId}/pdf${q ? `?${q}` : ""}`;
}

export function sourceAudioUrl(jobId: string): string {
  return `${API_BASE_URL}/api/jobs/${jobId}/source-audio`;
}

export function trackAudioUrl(jobId: string, trackId: string): string {
  return `${API_BASE_URL}/api/jobs/${jobId}/tracks/${trackId}/audio`;
}
