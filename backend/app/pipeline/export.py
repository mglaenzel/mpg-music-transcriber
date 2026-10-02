"""Export a music21 Score to MusicXML, MIDI and (filtered) PDF."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from music21 import layout, stream

from app.core.config import settings

# Matches self-closing <tie .../> and <tied .../> MusicXML elements. We
# strip these post-write rather than relying on clearing Note/Chord.tie
# beforehand: music21's own musicxml writer runs its notation pass again at
# write() time and can reintroduce ties for same-pitch notes it still
# considers overlapping/adjacent, even after they were cleared on the
# in-memory Score. A stray tie here isn't a musically meaningful one (we
# never intend ties in this pipeline) but crashes OpenSheetMusicDisplay
# 1.9's tie-graphics code when the "start"/"stop" pair ends up in different
# rendered systems — see notation.py's comments for the full story.
_TIE_ELEMENT_RE = re.compile(r"\s*<tie(?:d)? type=\"(?:start|stop)\"\s*/>")


def _strip_ties(musicxml_path: Path) -> None:
    text = musicxml_path.read_text(encoding="utf-8")
    stripped = _TIE_ELEMENT_RE.sub("", text)
    if stripped != text:
        musicxml_path.write_text(stripped, encoding="utf-8")


def export_musicxml(score: stream.Score, out_path: Path) -> Path:
    score.write("musicxml", fp=str(out_path))
    _strip_ties(out_path)
    return out_path


def export_midi(score: stream.Score, out_path: Path) -> Path:
    score.write("midi", fp=str(out_path))
    return out_path


# music21 writes <defaults><scaling><millimeters>7</millimeters>... (40
# "tenths" = 7mm) by default. Shrinking this makes MuseScore fit more
# measures per system/line, same idea as OSMD's `zoom` on the frontend —
# but only for the PDF path, applied to a throwaway copy of the MusicXML,
# so the "main" combined.musicxml served to OSMD/for download is untouched
# (OSMD already gets its own, separately-tuned zoom in ScoreViewer.tsx;
# shrinking the source file too would double up and make it too small).
#
# We tried this via MuseScore's own `-S <style-file>` flag first (setting
# <Spatium>, page width, etc.) — it had *zero* effect at any value we
# tried, from a modest reduction down to an absurd 0.3, including via an
# intermediate .mscz conversion step. Whatever `-S` does on MusicXML/mscz
# import in this MuseScore 3 build, it isn't applying style overrides to
# layout. Scaling the *source* MusicXML directly (a value MuseScore reads
# as part of parsing the file, not a post-import override) does work.
DEFAULT_PDF_SCALE_MILLIMETERS = "3.5"
_SCALING_MM_RE = re.compile(r"<millimeters>[^<]*</millimeters>")


def apply_scale_mm(musicxml_path: Path, scale_mm: str | None = None) -> None:
    """Override the <defaults><scaling><millimeters> value in-place — the
    lever that actually controls how many measures MuseScore (CLI or
    desktop) fits per line; see DEFAULT_PDF_SCALE_MILLIMETERS above for
    why this, and not a `-S` style file, is what works."""
    text = musicxml_path.read_text(encoding="utf-8")
    mm = scale_mm or DEFAULT_PDF_SCALE_MILLIMETERS
    musicxml_path.write_text(
        _SCALING_MM_RE.sub(f"<millimeters>{mm}</millimeters>", text),
        encoding="utf-8",
    )


def export_pdf(
    score: stream.Score,
    out_path: Path,
    part_names: list[str] | None = None,
    scale_mm: str | None = None,
    renames: dict[str, str] | None = None,
) -> Path:
    """Render to PDF via MuseScore CLI. If `part_names` is given, only those
    parts are kept, matched by Part.partName (e.g. "Piano", "Bass" — the
    stem label set in notation.py). Part.id is *not* usable here: it's
    music21's own generated id (e.g. "P05a8dd43...") once the score has been
    round-tripped through a MusicXML write/parse, unrelated to our track_id
    values — same pitfall as OSMD's Instrument.Id on the frontend.

    `renames` (see notation.rename_parts) is applied *after* the
    part_names filter above, deliberately — part_names is matched against
    each part's original label (e.g. "Vocals"), so renaming first would
    make a still-original `part_names=["Vocals"]` filter miss a part
    already renamed to "Alto-Sax" and silently drop it from the PDF."""
    render_score = score
    if part_names:
        render_score = stream.Score()
        render_score.metadata = score.metadata
        kept_parts = [part for part in score.parts if part.partName in part_names]
        for part in kept_parts:
            render_score.insert(0, part)
        # StaffGroup (the Piano+Bass grand-staff brace, see notation.py)
        # is a Spanner attached at the Score level, not on either Part —
        # copying parts alone silently drops it, so the filtered PDF loses
        # the brace even when both its parts are kept. Only re-add a group
        # whose *every* spanned part survived the filter; a group missing
        # one of its parts wouldn't make sense to draw anyway.
        kept_part_names = {p.partName for p in kept_parts}
        for group in score.getElementsByClass(layout.StaffGroup):
            if {p.partName for p in group.getSpannedElements()} <= kept_part_names:
                render_score.insert(0, group)

    if renames:
        from app.pipeline import notation

        notation.rename_parts(render_score, renames)

    tmp_musicxml = out_path.with_suffix(".tmp.musicxml")
    export_musicxml(render_score, tmp_musicxml)
    apply_scale_mm(tmp_musicxml, scale_mm)

    cmd = [settings.musescore_binary, "-o", str(out_path), str(tmp_musicxml)]
    # MuseScore 4's CLI crashes intermittently on macOS (documented in
    # README, "Bekannte Einschränkung — MuseScore 4 CLI") — observed in
    # real usage as roughly every other invocation, not a rare fluke.
    # Investigated further: the crash happens *after* the PDF is already
    # written correctly — every crashed run we captured still left a
    # valid, complete PDF on disk (checked via `file`: "PDF document,
    # ... N pages" every time, never truncated/corrupt). It's a
    # crash-on-exit during MuseScore's own teardown/cleanup, unrelated to
    # whether the conversion itself succeeded. So: ignore the exit code
    # and only treat this as a real failure if `out_path` wasn't actually
    # produced — a bare retry (previously the only mitigation here) would
    # otherwise re-run the whole ~cost of a MuseScore invocation for
    # nothing on every crash, successful or not.
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0 and not out_path.exists():
        raise subprocess.CalledProcessError(result.returncode, cmd)
    tmp_musicxml.unlink(missing_ok=True)
    return out_path
