import type { JobStatusResponse } from "../lib/api";

const STEP_LABELS: Record<string, string> = {
  pending: "Warten",
  separating: "Stimmen trennen",
  transcribing: "Noten erkennen",
  notating: "Notensatz erstellen",
  exporting: "Exportieren",
  done: "Fertig",
  failed: "Fehlgeschlagen",
};

export default function JobProgress({ status }: { status: JobStatusResponse }) {
  return (
    <div className="job-progress">
      <p className="job-progress__label">{STEP_LABELS[status.status] ?? status.status}</p>
      <div className="job-progress__bar">
        <div
          className="job-progress__bar-fill"
          style={{ width: `${Math.round(status.progress * 100)}%` }}
        />
      </div>
      {status.status === "failed" && status.error && (
        <p className="job-progress__error">{status.error}</p>
      )}
    </div>
  );
}
