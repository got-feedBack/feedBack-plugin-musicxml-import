# musicxml_import — Context

Slopsmith plugin. Imports MusicXML (`.xml` / `.musicxml`) files and produces
a `.sloppak` with notation format data (`notation_keys.json` +
`song_timeline.json`) and synthesized piano audio via FluidSynth.

**Requires:** Slopsmith `feat/notation-format` branch or later.

---

## Files

| File | Purpose |
|---|---|
| `plugin.json` | Plugin manifest (`private: false`, nav entry) |
| `screen.html` | Drag-and-drop import UI |
| `screen.js` | Frontend: file upload → `/upload`, build progress via WebSocket |
| `routes.py` | Backend: `/upload` POST + `/build` WebSocket |
| `mxml2notation.py` | Conversion library (MusicXML parse → notation wire format + MIDI) |
| `tests/` | pytest suite for the schema-v1 alignment (`python3 -m pytest tests/`) |

---

## Pipeline

```
.xml file
  → mxml2notation.parse_musicxml()
      → tempo map from <sound tempo> / <metronome>
      → beats: one per quarter note, downbeat measure≥1 / inner beats -1
      → notation: measure-structured (measure → staff → voice → beat → note)
          → staves: rh (G2 treble), lh (F4 bass), from <clef> elements
          → MIDI pitch from <pitch><step><alter><octave>
          → duration from <type> element → {1,2,4,8,16,32}
          → dots from <dot/> children
          → grace notes: typed grace:"a" (slash="yes") / grace:"p" beat
          → sustain pedal: spd/sph/spu on bottom-staff beats from <pedal>
          → pickup: true on implicit="yes" measures
          → arp from <arpeggiate>, ferm from <fermata>, stem from <stem>
          → ties: tied:true on continuation note
          → dynamics: direction-level + note-level (Option C, note wins)
          → articulations: stc, ten, ac, hac from <notations><articulations>
          → slurs: slr/slre from <notations><slur type="start/stop">
          → technical: ho, po, harm, fng from <notations><technical>
          → ornaments: txt="tr" from <trill-mark>, vib from <wavy-line>
          → accidentals: acc from <accidental> element
          → credits: rights/lyricist/arranger from <identification>
          → hairpins: cre/dec from <wedge type="crescendo/diminuendo">
          → key sig: ks from <key><fifths>
          → time sig: ts from <time><beats><beat-type>
          → beat_groups: [3,3] for 6/8, [3,3,3] for 9/8, [2,3] for 5/8, etc.
          → beat_pos: rational position within measure [num, denom] on non-downbeats
      → song_timeline: beats (primary beat unit, not quarter-note) + sections
  → gp2midi.render_midi_to_audio() via bundled FluidSynth + GeneralUser-GS.sf2
  → mxml2notation.build_sloppak_zip()
  → dlc/sloppack/<title>_mxml.sloppak
```

---

## Sloppak output

```
<title>_mxml.sloppak/
├── manifest.yaml          arrangements[0]: id=keys, type=piano, notation=notation_keys.json
│                          song_timeline: song_timeline.json
│                          stems: [full.ogg] when audio succeeds
├── notation_keys.json     notation wire format (version=1, instrument=piano)
├── song_timeline.json     beats + sections (version=1)
└── stems/
    └── full.ogg           FluidSynth GM program 0 (Grand Piano)
```

`file:` is intentionally absent from the arrangement entry — the loader
on `feat/notation-format` supports this when `notation:` is present.

---

## Known limitations

| Limitation | Notes |
|---|---|
| **First part only** | Multi-part scores (piano + violin) import only part 1. A future version may produce one notation file per part. |
| **score-partwise only** | `score-timewise` not supported. |
| **No repeats** | Da capo, segno, repeat barlines not expanded — each measure plays once. |
| **Grace notes in audio** | Grace notes appear in the notation score (typed `grace: "a"`/`"p"` beat) but are absent from FluidSynth MIDI. Principal note timing is unaffected. |
| **Schema v1 non-features** | Ottava (octave-shift), tremolo, notated glissando lines, and mid-measure key/time/clef changes are dropped with a logged warning, never approximated (sloppak-spec §5.3). |
| **No .mxl** | Compressed MusicXML not supported — unzip before importing. |

---

## Dependencies

- `midiutil` — MIDI file generation (bundled in slopsmith environment)
- `pyyaml` — manifest serialisation (bundled in slopsmith environment)
- FluidSynth binary + `GeneralUser-GS.sf2` — available in the slopsmith-src
  Docker/OrbStack container environment via `gp2midi.render_midi_to_audio`
- `gp2midi.render_midi_to_audio` — imported from slopsmith core at build time
- stdlib `xml.etree.ElementTree` — XML parsing (no third-party XML dependency)

---

## Relationship to staffview

`staffview` reads the notation wire format delivered via the `notation_info` /
`notation_measures` WebSocket messages. Sloppaks produced by this plugin feed
directly into staffview's rendering pipeline without the `stf` wire key or
`staff_compat` shim used by the old musicxml_import prototype.

---

## Changelog

See `CHANGELOG.md`.
