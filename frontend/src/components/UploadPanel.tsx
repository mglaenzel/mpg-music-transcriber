import { useRef, useState } from "react";
import { GENRE_OPTIONS, uploadAudio } from "../lib/api";

interface Props {
  onJobCreated: (jobId: string) => void;
}

export default function UploadPanel({ onJobCreated }: Props) {
  const [isDragging, setIsDragging] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [genre, setGenre] = useState("auto");
  const inputRef = useRef<HTMLInputElement>(null);

  async function handleFile(file: File) {
    setError(null);
    setIsUploading(true);
    try {
      const { job_id } = await uploadAudio(file, genre);
      onJobCreated(job_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unbekannter Fehler beim Upload");
    } finally {
      setIsUploading(false);
    }
  }

  return (
    <div className="upload-panel">
      <label className="upload-panel__genre">
        Genre / Tempo-Hinweis
        <select value={genre} onChange={(e) => setGenre(e.target.value)}>
          {GENRE_OPTIONS.map((g) => (
            <option key={g.value} value={g.value}>
              {g.label}
            </option>
          ))}
        </select>
      </label>
      <div
        className={`dropzone ${isDragging ? "dropzone--active" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setIsDragging(true);
        }}
        onDragLeave={() => setIsDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setIsDragging(false);
          const file = e.dataTransfer.files?.[0];
          if (file) void handleFile(file);
        }}
        onClick={() => inputRef.current?.click()}
      >
        <p className="dropzone__title">Audiodatei hierher ziehen</p>
        <p className="dropzone__subtitle">oder Datei auswählen</p>
        <p className="dropzone__hint">MP3, WAV, MP4, FLAC</p>
        <input
          ref={inputRef}
          type="file"
          accept=".mp3,.wav,.mp4,.m4a,.flac"
          hidden
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void handleFile(file);
          }}
        />
      </div>
      {isUploading && <p className="upload-panel__status">Wird hochgeladen …</p>}
      {error && <p className="upload-panel__error">{error}</p>}
    </div>
  );
}
