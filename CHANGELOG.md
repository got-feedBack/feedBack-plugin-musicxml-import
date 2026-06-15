# Changelog

All notable changes to `slopsmith-plugin-musicxml-import` are documented here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

### Changed

- **Curated into the slopsmith org** (slopsmith#825 WS4a, epic
  slopsmith#828): `private: false` and a one-line `description` added to
  `plugin.json`; relicensed MIT → AGPL-3.0-only per the curated-plugin
  licensing policy (CONTRIBUTING.md), attribution to the original author
  (Gionni) kept in the README.
- **Aligned with the frozen notation schema v1** (sloppak-spec §5.3,
  slopsmith#838):
  - Grace notes are now a typed string — `grace: "a"` (acciaccatura,
    `<grace slash="yes">`) / `grace: "p"` (appoggiatura) — replacing the
    `grace: true` + `grace_slash: true` boolean pair.
  - Sustain pedal extraction: `<pedal type="start|change|stop">` →
    `spd` / `spu` (`change` = `spu` + `spd` on the same beat), with
    `sph` on beats inside an active span (bottom staff, spans cross
    barlines, rests included).
  - Fermata is the typed `ferm: true` beat flag instead of a
    `txt: "fermata"` annotation; arpeggiated chords emit `arp: true`.
  - Note stem overrides: `<stem>up|down</stem>` → `stem` (other values
    omitted — renderer decides).
  - Measure `pickup: true` from `implicit="yes"` (anacrusis).
  - Credits: `rights`, `lyricist`, `arranger` extracted from
    `<identification>` onto the notation payload; `<creator
    type="composer">` is now a fallback for the composer heuristic.
  - v1 non-features — ottava (octave-shift), tremolo, notated glissando
    lines, and mid-measure key/time/clef changes — are dropped with a
    logged warning, never approximated.
- **Upload handling hardened** (`routes.py`): the build WebSocket now
  takes an opaque server-issued `upload_id` instead of a raw filesystem
  `tmp_path` (the old parameter let a client point the build step — which
  reads the file and deletes its parent directory — at an arbitrary
  path); uploads are bounded to 20 MB (decoded) and stale upload temp
  dirs are purged after 1 h.

### Added

- pytest suite (`tests/test_mxml2notation.py`, 12 tests) covering the
  schema-v1 alignment: typed grace, pedal start/hold/change/stop across
  barlines, credits, pickup, arp/ferm/stem, and drop-with-warning for
  every v1 non-feature.

### Fixed

- Temp filename sanitised in `upload_mxml` — spaces in filenames were
  encoded as `+` in the WS query parameter, causing the build step to
  report "file expired" because the path did not exist.
- `beat_pos` denominator now correctly uses `ts_beat_type` (the
  time-signature denominator) as required by the spec. In 6/8, beat 2
  is `[3, 8]` not `[3, 32]`. Unused `gcd` import removed.
- `upload_mxml` parse-failure path now cleans up the temp directory
  before returning the error response.
- `ws://` in `screen.js` replaced with a protocol-relative conditional
  (`wss` on HTTPS deployments).
- Compound and irregular meter beat emission. Beat ticks in
  `song_timeline.json` now use the primary beat unit (dotted quarter
  for 6/8, 9/8, 12/8) rather than the quarter note. `beat_groups` is
  written onto compound/irregular measure dicts; `beat_pos` is written
  onto every non-downbeat notation beat.
- Output filename no longer doubles the `_mxml` suffix when the title
  extracted from the MusicXML already ends with the word "mxml".
- `<direction>` elements appearing after their target note
  (post-annotation style, common in Sibelius/MuseScore exports) are now
  attributed to the preceding note's beat. `_collect_measure_directions`
  captures `last_note_start` before advancing the cursor.
  `_active_dynamic` switched from "at or before" to exact matching —
  `dyn` marks symbol position, not a persistent dynamic level.
- `<wavy-line>` vibrato now tracked as a span. Previously `vib: True`
  was emitted only on the note carrying `<wavy-line type="start">`. Now
  a `vibrato_open` tracker applies `vib: True` to every beat from start
  through stop inclusive, including across measure boundaries.
- `song_timeline` sections now carry a `number` field (1-based per-name
  counter). Previously all sections defaulted to repeat #0.
- `alter` now uses `round()` instead of `int(float())` to avoid
  truncation of near-integer MusicXML alter values from imprecise
  exporters.
- `load_sibling('mxml2notation')` resolved once at `setup()` time
  instead of per-request.
- `gp2midi` imported at module level with an `ImportError` fallback
  instead of inside the build thread.
- `asyncio.get_running_loop()` replaces deprecated `get_event_loop()`.
- `traceback.print_exc()` replaced with `_log.exception()`.
- Metadata indexing failure now logs a warning instead of silently
  passing.
- MIDI and upload temp dirs cleaned up via `shutil.rmtree` in a
  `finally` block.

### Added

- `requirements` field in `plugin.json` (`midiutil`, `pyyaml`) for
  self-installation outside the slopsmith container environment.
- Instrument inference from MusicXML part name (`_infer_instrument()`
  with a synonym table covering piano, organ, strings, woodwinds, brass,
  plucked, and voice families; longest-key-wins substring fallback;
  returns `'unknown'` for unrecognised names). Arrangement `id`, `name`,
  `type`, and notation filename are now derived from the inferred
  instrument. Single-staff non-piano instruments use the instrument name
  as the staff label.

---

## [0.1.0]

### Added

- `mxml2notation.py` — MusicXML to notation wire format conversion
  library. Produces `notation_<id>.json` (notation format v1) and
  `song_timeline.json` per the sloppak spec §5.3 (requires
  `feat/notation-format` branch or later).
- `routes.py` — FastAPI backend:
  `POST /api/plugins/musicxml_import/upload` and
  `WS /ws/plugins/musicxml_import/build`.
- `screen.html` / `screen.js` — drag-and-drop import UI with progress
  reporting.
- `plugin.json` — plugin manifest (`private: true`, nav entry).
- `CONTEXT.md` — pipeline description, limitations, dependency map.
- `LICENSE` — MIT.
- `README.md` — install instructions and compatibility note.

### Known limitations

- First part only — multi-part scores import only part 1.
- Grace notes appear in the notation score but are absent from MIDI
  audio.
- `grace_slash` field recorded but not yet acted on by any renderer.
- No repeat / da capo / segno expansion.
- No `.mxl` (compressed MusicXML) support.
