import { useEffect, useState } from "react";
import { fetchJobList, type JobSummary } from "../lib/api";

interface Props {
  onSelect: (jobId: string) => void;
}

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleString("de-DE", {
      day: "2-digit",
      month: "2-digit",
      year: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

export default function JobLibrary({ onSelect }: Props) {
  const [jobs, setJobs] = useState<JobSummary[] | null>(null);

  useEffect(() => {
    fetchJobList()
      .then(setJobs)
      .catch((err) => {
        console.error(err);
        setJobs([]);
      });
  }, []);

  if (jobs === null || jobs.length === 0) return null;

  return (
    <div className="job-library">
      <h2>Bisherige Transkriptionen</h2>
      <ul className="job-library__list">
        {jobs.map((j) => (
          <li key={j.job_id}>
            <button className="job-library__item" onClick={() => onSelect(j.job_id)}>
              <span className="job-library__filename">{j.original_filename}</span>
              <span className="job-library__meta">
                {j.status === "done"
                  ? [j.tempo_italian_term, j.bpm ? `♩ = ${j.bpm}` : null].filter(Boolean).join(" ")
                  : j.status}
                {" · "}
                {formatDate(j.created_at)}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
