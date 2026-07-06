# musicxml_import — Context

FeedBack plugin. Imports MusicXML (`.xml` / `.musicxml`) files and produces
a `.feedpak` with notation format data (`notation_<instrument>.json` +
`song_timeline.json`) and synthesized piano audio via FluidSynth.

**Requires:** a FeedBack build with notation-format support in core
(current `main`; v0.3.0-alpha or later).

---

## Files

| File | Purpose |
|---|---|
| `plugin.json` | Plugin manifest (`private: false`, nav entry, `styles`) |
| `screen.html` | Drag-and-drop import UI |
| `screen.js` | Frontend: file upload → `/upload`, build progress via WebSocket |
| `routes.py` | Backend: `/upload` POST + `/build` WebSocket |
| `mxml2notation.py` | Conversion library (MusicXML parse → notation wire format + MIDI) |
| `requirements.txt` | Python deps installed by the plugin loader |
| `assets/plugin.css` | Compiled Tailwind utilities (rebuild: `bash build-tailwind.sh`) |
| `tests/` | pytest suite (`python3 -m pytest tests/`) |

---

## Pipeline

```
.xml file
  → mxml2notation.parse_musicxml()
      → tempo map from <sound tempo> / <metronome>
      → beats: one per primary beat unit, downbeat measure≥1 / inner beats -1
      → notation: measure-structured (measure → staff → voice → beat → note)
          → staves: rh (G2 treble), lh (F4 bass), from <clef> elements
          → MIDI pitch from <pitch><step><alter><octave>
          → duration from <type> element → {1,2,4,8,16,32}
          → dots from <dot/> children
          → tuplets: tu [actual, normal] from <time-modification>
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
      → song_timeline: tempos + time_signatures maps (spec §7.4, 1.2.0)
        + beats (primary beat unit) + sections
  → gp2midi.render_midi_to_audio() via bundled FluidSynth + GeneralUser-GS.sf2
  → mxml2notation.build_feedpak_zip()
  → dlc/musicxml/<title>_mxml.feedpak
```

---

## Feedpak output

```
<title>_mxml.feedpak/
├── manifest.yaml               feedpak_version stamped from core (§4.1)
│                               arrangements[0]: id=<instrument>, notation-only
│                               song_timeline: song_timeline.json
│                               stems: [full.ogg] when audio succeeds (omitted otherwise)
├── notation_<instrument>.json  notation wire format (version=1)
├── song_timeline.json          tempos + time_signatures + beats + sections (version=1)
└── stems/
    └── full.ogg                FluidSynth GM program 0 (Grand Piano)
```

`file:` is intentionally absent from the arrangement entry — spec §5.2
allows notation-only arrangements when `notation:` is present. The
arrangement id and notation filename derive from the instrument inferred
from the part name (`notation_piano.json`, `notation_violin.json`, …);
`type` is omitted when inference fails.

A pack built with "Include synthesized piano audio" unchecked — or whose
FluidSynth render failed (never fatal, reported on completion) — has no
`stems` key: a local authoring intermediate under the spec §5.3.2
carve-out. Add stems in the editor before distributing.

---

## Known limitations

| Limitation | Notes |
|---|---|
| **First part only** | Multi-part scores (piano + violin) import only part 1. A future version may produce one notation file per part. |
| **score-partwise only** | `score-timewise` not supported. |
| **No repeats** | Da capo, segno, repeat barlines not expanded — each measure plays once. |
| **Grace notes in audio** | Grace notes appear in the notation score (typed `grace: "a"`/`"p"` beat) but are absent from FluidSynth MIDI. Principal note timing is unaffected. |
| **Schema v1 non-features** | Ottava (octave-shift), tremolo, notated glissando lines, and mid-measure key/time/clef changes are dropped with a logged warning, never approximated (spec §7.6). |
| **No .mxl** | Compressed MusicXML not supported — unzip before importing. |

---

## Dependencies

- `midiutil`, `pyyaml` — declared in `requirements.txt`, installed by the
  plugin loader (both also ship with core)
- FluidSynth binary + `GeneralUser-GS.sf2` — available in the FeedBack
  Docker/OrbStack container environment via core `gp2midi.render_midi_to_audio`
- stdlib `xml.etree.ElementTree` — XML parsing (no third-party XML dependency)

---

## Relationship to staffview

`staffview` reads the notation wire format delivered via the `notation_info` /
`notation_measures` WebSocket messages. Feedpaks produced by this plugin feed
directly into staffview's rendering pipeline.

---

## Changelog

See `CHANGELOG.md`.
