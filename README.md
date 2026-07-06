# feedBack-plugin-musicxml-import

FeedBack plugin. Imports MusicXML (`.xml` / `.musicxml` / compressed
`.mxl`) scores and produces a `.feedpak` using the **notation format** —
ready for playback and staff rendering via the `staffview` plugin.

## Compatibility

**Requires:** a FeedBack build with notation-format support in core
(current `main`; v0.3.0-alpha or later).

Packs are written per the [feedpak spec](https://github.com/got-feedback/feedpak-spec)
(`spec/feedpak-v1.md`): `notation_<instrument>.json` (§7.6) +
`song_timeline.json` (§7.4), with `feedpak_version` stamped from the host's
own format constant.

## What gets imported

- Compressed MusicXML (`.mxl`) unwrapped transparently via
  `META-INF/container.xml`'s primary rootfile
- Pitch, duration, dots, rests, ties, tuplets (`tu`)
- Grace notes, typed per spec: `grace: "a"` (acciaccatura,
  `<grace slash="yes">`) / `grace: "p"` (appoggiatura)
- Key signature, time signature, tempo changes; pickup (anacrusis)
  measures (`implicit="yes"` → `pickup: true`)
- Song-level tempo and time-signature maps in `song_timeline.json`
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
  feedpak spec §7.6.
- **Grace notes in audio** — appear in the notation score but not in the
  FluidSynth MIDI audio; principal note timing is unaffected.
- **Pedal attribution** — pedal flags attach to the bottom staff (`lh`
  on a grand staff) and, like dynamics, to the preceding note's beat
  (post-annotation style).
- **Audio is optional by choice** — uncheck "Include synthesized piano
  audio" to skip rendering and write the pack without stems; FluidSynth
  failures are also handled gracefully (pack still created, failure shown
  on completion). Either way a stem-less pack is a local authoring
  intermediate (feedpak spec §5.3.2 carve-out): open it in the editor and
  add stems before distributing it.

## Dependencies

Declared in `requirements.txt` (installed by the plugin loader when absent):

| Dependency | Purpose |
|---|---|
| `midiutil` | MIDI file generation |
| `pyyaml` | Manifest serialisation |
| FluidSynth + GeneralUser-GS.sf2 | **Audio only** — piano rendering via core `gp2midi`; not needed when "Include synthesized piano audio" is unchecked, and its absence never fails the build (see Limitations) |

No third-party XML library required — uses stdlib `xml.etree.ElementTree`.

## Styling

The screen uses Tailwind classes not guaranteed in core's prebuilt CSS, so
the plugin ships its own compiled stylesheet (`assets/plugin.css`, declared
via the manifest `styles` key). Rebuild after changing classes:

```bash
bash build-tailwind.sh   # writes assets/plugin.css — commit it
```

## License

AGPL-3.0-only — see `LICENSE`.

Copyright (c) 2025-2026 Gionni (gionnibgud@gmail.com) and contributors.
Originally prototyped by [Gionni](https://github.com/gionnibgud); curated
into the FeedBack org for the piano/keys epic.
