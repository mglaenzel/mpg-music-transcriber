import { useEffect, useState } from "react";
import JobLibrary from "./components/JobLibrary";
import JobProgress from "./components/JobProgress";
import ScoreViewer from "./components/ScoreViewer";
import UploadPanel from "./components/UploadPanel";
import { fetchJobStatus, type JobStatusResponse } from "./lib/api";

function jobIdFromUrl(): string | null {
  return new URLSearchParams(window.location.search).get("job");
}

export default function App() {
  const [jobId, setJobIdState] = useState<string | null>(() => jobIdFromUrl());
  const [status, setStatus] = useState<JobStatusResponse | null>(null);

  // Keep the URL in sync so a reload (or a shared/bookmarked link) resumes
  // the same job instead of dropping back to the empty upload screen.
  function setJobId(id: string | null) {
    setJobIdState(id);
    setStatus(null);
    const url = new URL(window.location.href);
    if (id) url.searchParams.set("job", id);
    else url.searchParams.delete("job");
    window.history.pushState({}, "", url);
  }

  useEffect(() => {
    function onPopState() {
      setJobIdState(jobIdFromUrl());
      setStatus(null);
    }
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;

    async function poll() {
      try {
        const s = await fetchJobStatus(jobId!);
        if (cancelled) return;
        setStatus(s);
        if (s.status !== "done" && s.status !== "failed") {
          setTimeout(poll, 1500);
        }
      } catch (err) {
        console.error(err);
        if (!cancelled) setTimeout(poll, 3000);
      }
    }
    void poll();

    return () => {
      cancelled = true;
    };
  }, [jobId]);

  // After a BPM correction (which rebuilds the job's notation server-side,
  // see correctBpm/retempo.rebuild_with_bpm) the job's bpm/tempo_italian_term
  // and tracks have changed — re-fetch so ScoreViewer's `status` prop (and
  // the tempo shown in the transport bar) reflects the corrected value
  // instead of the stale one from the initial poll.
  async function refreshStatus() {
    if (!jobId) return;
    try {
      setStatus(await fetchJobStatus(jobId));
    } catch (err) {
      console.error(err);
    }
  }

  return (
    <div className="app">
      <header className="app__header">
        <h1>Music Transcriber</h1>
        <p>Audio hochladen → automatische Notentranskription → MusicXML / MIDI / PDF</p>
        {jobId && (
          <button className="app__back" onClick={() => setJobId(null)}>
            ← Zur Bibliothek
          </button>
        )}
      </header>

      {!jobId && (
        <>
          <UploadPanel onJobCreated={setJobId} />
          <JobLibrary onSelect={setJobId} />
        </>
      )}

      {jobId && status && status.status !== "done" && <JobProgress status={status} />}

      {jobId && status && status.status === "done" && (
        <ScoreViewer jobId={jobId} status={status} onStatusChange={refreshStatus} />
      )}

      {jobId && status?.status === "failed" && (
        <button onClick={() => setJobId(null)}>Neuer Versuch</button>
      )}
    </div>
  );
}
