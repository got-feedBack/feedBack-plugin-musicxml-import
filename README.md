# slopsmith-plugin-musicxml-import

Slopsmith plugin. Imports MusicXML (`.xml` / `.musicxml`) scores and produces
a `.sloppak` using the **notation format** — ready for playback and staff
rendering via the `staffview` plugin.

## Compatibility

**Requires:** a Slopsmith build with the frozen sloppak notation schema
(`docs/sloppak-spec.md` §5.3, slopsmith#838) — currently the
`feat/notation-schema-v1` branch.

The sloppak produced by this plugin uses `notation_<id>.json` and
`song_timeline.json` per sloppak-spec §5.3. It will not load on older
Slopsmith builds that predate the notation format.

## What gets imported

- Pitch, duration, dots, rests, ties
- Grace notes, typed per spec: `grace: "a"` (acciaccatura,
  `<grace slash="yes">`) / `grace: "p"` (appoggiatura)
- Key signature, time signature, tempo changes; pickup (anacrusis)
  measures (`implicit="yes"` → `pickup: true`)
- Sustain pedal → `spd` / `sph` / `spu` (`<pedal type="change">` =
  `spu` + `spd` on the same beat)
- Dynamics (direction-level and note-level)
- Articulations: staccato, tenuto, accent, strong accent
- Slurs, hairpins (crescendo / diminuendo)
- Arpeggiated chords (`arp`), fermata (`ferm`), stem direction (`stem`)
- Hammer-on, pull-off, harmonics (natural/artificial), fingering
- Ornaments: trill mark (as text annotation), vibrato (wavy-line)
- Accidental overrides (force natural, flat, sharp, double-flat, double-sharp)
- Rehearsal marks → sections
- Credits: `rights`, `lyricist`, `arranger` from `<identification>`

## Limitations

- **First part only** — multi-part scores (e.g. piano + violin) import only
  part 1.
- **No repeats** — da capo, segno, repeat barlines are not expanded.
- **Schema v1 non-features dropped, never approximated** — ottava
  (octave-shift), tremolo, notated glissando lines, and mid-measure
  key/time/clef changes are dropped with a logged warning per
  sloppak-spec §5.3.
- **Grace notes in audio** — appear in the notation score but not in the
  FluidSynth MIDI audio; principal note timing is unaffected.
- **Pedal attribution** — pedal flags attach to the bottom staff (`lh`
  on a grand staff) and, like dynamics, to the preceding note's beat
  (post-annotation style).
- **No .mxl** — compressed MusicXML not supported; unzip before importing.

## Dependencies

All available in the slopsmith-src container environment:

| Dependency | Purpose |
|---|---|
| `midiutil` | MIDI file generation |
| `pyyaml` | Manifest serialisation |
| FluidSynth + GeneralUser-GS.sf2 | Piano audio rendering (via `gp2midi`) |

No third-party XML library required — uses stdlib `xml.etree.ElementTree`.

## License

AGPL-3.0-only — see `LICENSE`.

Copyright (c) 2025-2026 Gionni (gionnibgud@gmail.com) and contributors.
Originally prototyped by [Gionni](https://github.com/gionnibgud); curated
into the Slopsmith org for the piano/keys epic (slopsmith#828).
