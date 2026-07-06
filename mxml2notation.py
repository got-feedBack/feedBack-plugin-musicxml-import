"""MusicXML → feedpak notation format conversion library.

Parses a MusicXML score-partwise file using stdlib xml.etree and produces:
  - notation_<id>.json payload (feedpak spec §7.6, schema version 1)
  - song_timeline.json payload (beats + sections + tempo/time-signature
    maps, feedpak spec §7.4)
  - a standard MIDI file (bytes) for FluidSynth audio rendering

Only the first part is imported. Multi-part scores are not yet supported.

Known limitations
-----------------
- score-partwise only (score-timewise not supported).
- First part only — multi-part scores (e.g. piano + violin) import only
  part 1. A future version may produce one notation file per part.
- Grace notes are emitted in the notation schema as a typed string
  (grace: "a" = acciaccatura / MusicXML <grace slash="yes">, grace: "p" =
  appoggiatura / plain <grace>) but are absent from the MIDI audio output.
- No repeats / da capo / segno expansion — each measure plays once.
- No .mxl (compressed MusicXML — unzip before importing).
- Schema v1 non-features (ottava / octave-shift, tremolo, notated
  glissando lines, mid-measure key/time/clef changes) are dropped with a
  logged warning, never approximated (feedpak spec §7.6).
- Sustain pedal directions are attributed to the bottom staff (lh for a
  grand staff) and, like dynamics, to the preceding note's beat
  (post-annotation style).
- Compound (6/8, 9/8, 12/8) and common irregular (5/8, 7/8) meters emit
  primary beats at the dotted-quarter / group unit. More exotic irregular
  meters fall back to quarter-note resolution.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import zipfile

from xml.etree import ElementTree as ET

import yaml

log = logging.getLogger("feedBack.plugin.musicxml_import")

# Format version stamped into manifest.yaml (feedpak spec §4.1). Prefer the
# host's own constant so packs always declare what the running FeedBack
# writes; the fallback covers CLI / test runs outside the app.
try:
    from sloppak import FEEDPAK_VERSION as _FEEDPAK_VERSION
except ImportError:
    _FEEDPAK_VERSION = "1.2.0"

# ---------------------------------------------------------------------------
# Instrument inference
# ---------------------------------------------------------------------------

_INSTRUMENT_SYNONYMS: dict[str, str] = {
    # Piano family
    'piano': 'piano', 'pianoforte': 'piano', 'grand piano': 'piano',
    'upright piano': 'piano', 'keyboard': 'piano', 'keys': 'piano',
    'electric piano': 'piano', 'digital piano': 'piano',
    'harpsichord': 'piano', 'clavichord': 'piano', 'celesta': 'piano',
    'organ': 'organ', 'pipe organ': 'organ', 'hammond': 'organ',
    # Strings
    'violin': 'violin', 'viola': 'viola',
    'cello': 'cello', 'violoncello': 'cello',
    'double bass': 'double_bass', 'contrabass': 'double_bass',
    'bass': 'double_bass',
    'harp': 'harp',
    # Woodwinds
    'flute': 'flute', 'piccolo': 'flute',
    'oboe': 'oboe', 'cor anglais': 'oboe', 'english horn': 'oboe',
    'clarinet': 'clarinet', 'bass clarinet': 'clarinet',
    'bassoon': 'bassoon', 'contrabassoon': 'bassoon',
    'saxophone': 'saxophone', 'alto sax': 'saxophone',
    'tenor sax': 'saxophone', 'soprano sax': 'saxophone',
    # Brass
    'trumpet': 'trumpet', 'cornet': 'trumpet',
    'french horn': 'horn', 'horn': 'horn',
    'trombone': 'trombone', 'bass trombone': 'trombone',
    'tuba': 'tuba',
    # Plucked
    'guitar': 'guitar', 'electric guitar': 'guitar',
    'acoustic guitar': 'guitar', 'classical guitar': 'guitar',
    'bass guitar': 'bass_guitar',
    'ukulele': 'ukulele', 'banjo': 'banjo', 'mandolin': 'mandolin',
    # Percussion / voice
    'drums': 'drums', 'drum kit': 'drums', 'percussion': 'percussion',
    'voice': 'voice', 'vocals': 'voice', 'soprano': 'voice',
    'mezzo': 'voice', 'alto': 'voice', 'tenor': 'voice',
    'baritone': 'voice', 'bass voice': 'voice',
    'choir': 'voice', 'chorus': 'voice',
}

_PIANO_FAMILY: set[str] = {'piano', 'organ', 'harpsichord', 'celesta', 'clavichord'}


def _infer_instrument(part_name: str) -> str:
    """Infer instrument identifier from a MusicXML part name.

    Matches the part name (case-insensitive, stripped) against known
    synonyms. Returns a canonical instrument string (e.g. 'piano',
    'violin') or 'unknown' when no match is found.
    """
    normalised = part_name.strip().lower()
    if normalised in _INSTRUMENT_SYNONYMS:
        return _INSTRUMENT_SYNONYMS[normalised]
    matches = [
        (key, val) for key, val in _INSTRUMENT_SYNONYMS.items()
        if key in normalised
    ]
    if matches:
        return max(matches, key=lambda kv: len(kv[0]))[1]
    return 'unknown'


# ---------------------------------------------------------------------------
# Pitch helpers
# ---------------------------------------------------------------------------

_STEP_TO_SEMITONE: dict[str, int] = {
    'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11,
}


def _pitch_to_midi(step: str, alter: int, octave: int) -> int:
    """Convert MusicXML pitch components to MIDI note number."""
    return (octave + 1) * 12 + _STEP_TO_SEMITONE[step] + alter


# ---------------------------------------------------------------------------
# Duration helpers
# ---------------------------------------------------------------------------

_TYPE_TO_DUR: dict[str, int] = {
    'whole': 1, 'half': 2, 'quarter': 4,
    'eighth': 8, '16th': 16, '32nd': 32,
}


def _type_to_dur(note_type: str | None) -> int:
    """Map MusicXML <type> text to notation dur integer. Default: 8."""
    return _TYPE_TO_DUR.get(note_type or '', 8)


def _parse_time_modification(note_elem: ET.Element) -> list[int] | None:
    """MusicXML <time-modification> → notation ``tu`` field.

    Returns ``[actual, normal]`` (feedpak spec §7.6 — e.g. ``[3, 2]`` for a
    triplet: 3 notes in the written time of 2), or None for non-tuplet
    notes or degenerate values. Onset/timing already comes from <duration>
    divisions, which MusicXML pre-scales for tuplets — ``tu`` only informs
    notated-length math and staff rendering (bracket/number).
    """
    tm = note_elem.find('time-modification')
    if tm is None:
        return None
    try:
        actual = int(tm.findtext('actual-notes') or 0)
        normal = int(tm.findtext('normal-notes') or 0)
    except ValueError:
        return None
    if actual <= 0 or normal <= 0 or actual == normal:
        return None
    return [actual, normal]


def _count_dots(note_elem: ET.Element) -> int:
    """Count <dot/> children. Returns 1 or 2; 0 → omit field."""
    return min(2, len(note_elem.findall('dot')))


# ---------------------------------------------------------------------------
# Tempo map (reused logic from the old prototype importer)
# ---------------------------------------------------------------------------

def _build_tempo_map(root: ET.Element) -> list[tuple[int, float]]:
    """Return (divisions_elapsed, bpm) events from the first part."""
    events: list[tuple[int, float]] = [(0, 120.0)]
    parts = root.findall('part')
    if not parts:
        return events

    part = parts[0]
    divisions = 1
    abs_div = 0

    for measure in part.findall('measure'):
        for attr in measure.findall('attributes'):
            d = attr.findtext('divisions')
            if d:
                divisions = int(d)

        for direction in measure.findall('direction'):
            sound = direction.find('sound')
            if sound is not None:
                t = sound.get('tempo')
                if t:
                    try:
                        events.append((abs_div, float(t)))
                    except ValueError:
                        pass
                    continue
            for dt in direction.findall('direction-type'):
                metro = dt.find('metronome')
                if metro is None:
                    continue
                beat_unit = metro.findtext('beat-unit') or 'quarter'
                per_min = metro.findtext('per-minute')
                if per_min is None:
                    continue
                try:
                    bpm = float(per_min)
                except ValueError:
                    continue
                unit_map = {
                    'whole': 0.25, 'half': 0.5, 'quarter': 1.0,
                    'eighth': 2.0, '16th': 4.0, '32nd': 8.0,
                }
                factor = unit_map.get(beat_unit, 1.0)
                events.append((abs_div, bpm * factor))

        measure_dur = 0
        for elem in measure:
            if elem.tag in ('note', 'forward', 'backup'):
                dur = elem.findtext('duration')
                if dur is None:
                    continue
                dur_i = int(dur)
                if elem.tag == 'forward':
                    measure_dur += dur_i
                elif elem.tag == 'backup':
                    measure_dur -= dur_i
                elif elem.tag == 'note':
                    if elem.find('chord') is None and elem.find('grace') is None:
                        measure_dur += dur_i
        abs_div += max(0, measure_dur)

    events.sort(key=lambda e: e[0])
    return events


def _div_to_seconds(
    abs_div: int,
    divisions: int,
    tempo_map: list[tuple[int, float]],
) -> float:
    """Convert absolute divisions to seconds."""
    bpm = 120.0
    event_div = 0
    seconds = 0.0

    for i, (ev_div, ev_bpm) in enumerate(tempo_map):
        if ev_div > abs_div:
            break
        if i > 0:
            prev_div, prev_bpm = tempo_map[i - 1]
            span = ev_div - prev_div
            seconds += (span / divisions) * (60.0 / prev_bpm)
        event_div = ev_div
        bpm = ev_bpm

    span = abs_div - event_div
    seconds += (span / divisions) * (60.0 / bpm)
    return seconds


def _bpm_at(abs_div: int, tempo_map: list[tuple[int, float]]) -> float:
    """Return the BPM active at abs_div."""
    bpm = 120.0
    for ev_div, ev_bpm in tempo_map:
        if ev_div <= abs_div:
            bpm = ev_bpm
        else:
            break
    return bpm


# ---------------------------------------------------------------------------
# Notation effect parsers
# ---------------------------------------------------------------------------

def _parse_accidental(note_elem: ET.Element, alter: int) -> int | None:
    """Return acc override value or None (no override).

    None  → omit acc field (renderer derives from key signature)
    0     → force natural sign (♮), overrides key signature
    -1/-2 → force flat / double-flat
    1/2   → force sharp / double-sharp

    MusicXML <accidental> carries the *displayed* accidental, which may
    differ from the pitch alter when courtesy / cautionary accidentals are
    printed. We trust <accidental> when present; fall back to alter only
    when <accidental> is absent and the alter is non-zero (meaning the pitch
    is inflected but no explicit override is requested).
    """
    acc_elem = note_elem.find('accidental')
    if acc_elem is not None:
        text = (acc_elem.text or '').strip().lower()
        mapping = {
            'double-flat': -2,
            'flat-flat': -2,
            'flat': -1,
            'natural': 0,
            'sharp': 1,
            'double-sharp': 2,
            'sharp-sharp': 2,
        }
        if text in mapping:
            return mapping[text]
        # Unknown courtesy / editorial text — fall through to alter
    # No <accidental> element: no explicit display override needed
    return None


def _parse_articulations(note_elem: ET.Element) -> dict:
    """Extract note-level articulation flags from <notations><articulations>.

    Returns a dict of fields to merge onto the note object. Only non-default
    (truthy) fields are included.
    """
    out: dict = {}
    notations = note_elem.find('notations')
    if notations is None:
        return out
    arts = notations.find('articulations')
    if arts is None:
        return out
    if arts.find('staccato') is not None:
        out['stc'] = True
    if arts.find('tenuto') is not None:
        out['ten'] = True
    if arts.find('accent') is not None:
        out['ac'] = True
    if arts.find('strong-accent') is not None:
        out['hac'] = True
    return out


def _parse_technical(note_elem: ET.Element) -> dict:
    """Extract note-level technical annotations from <notations><technical>.

    Returns a dict of fields to merge onto the note object.
    """
    out: dict = {}
    notations = note_elem.find('notations')
    if notations is None:
        return out
    tech = notations.find('technical')
    if tech is None:
        return out
    if tech.find('hammer-on') is not None:
        out['ho'] = True
    if tech.find('pull-off') is not None:
        out['po'] = True
    if tech.find('harmonic') is not None:
        harm_elem = tech.find('harmonic')
        if harm_elem is not None:
            if harm_elem.find('artificial') is not None:
                out['harm'] = 'artificial'
            else:
                out['harm'] = 'natural'
    fng = tech.findtext('fingering')
    if fng is not None:
        try:
            out['fng'] = int(fng)
        except ValueError:
            pass
    return out


def _parse_ornaments(note_elem: ET.Element) -> dict:
    """Extract beat-level ornament flags from <notations><ornaments>.

    Returns a dict of fields to merge onto the *beat* object (not the note).
    Trill marks map to txt annotation only — alphaTab has no alphaTex trill
    beat property in the current API.
    """
    out: dict = {}
    notations = note_elem.find('notations')
    if notations is None:
        return out
    orn = notations.find('ornaments')
    if orn is None:
        return out
    if orn.find('trill-mark') is not None:
        # Record as a text annotation; renderers that support trill rendering
        # can upgrade this field when alphaTab exposes the property.
        out['txt'] = 'tr'
    return out


def _parse_slur(note_elem: ET.Element) -> tuple[bool, bool]:
    """Return (slur_start, slur_end) from <notations><slur>."""
    notations = note_elem.find('notations')
    if notations is None:
        return False, False
    slur_start = False
    slur_end = False
    for slur in notations.findall('slur'):
        t = slur.get('type', '')
        if t == 'start':
            slur_start = True
        elif t == 'stop':
            slur_end = True
    return slur_start, slur_end


def _parse_fermata(note_elem: ET.Element) -> bool:
    """Return True if <notations><fermata> is present."""
    notations = note_elem.find('notations')
    if notations is None:
        return False
    return notations.find('fermata') is not None


def _parse_note_dynamics(note_elem: ET.Element) -> str | None:
    """Return dynamic string from <notations><dynamics> (note-level form).

    This is the less common form. Option C: note-level wins over
    direction-level when both are present for the same beat.
    """
    notations = note_elem.find('notations')
    if notations is None:
        return None
    dyn_elem = notations.find('dynamics')
    if dyn_elem is None:
        return None
    _DYNAMICS_TAGS = {'ppp', 'pp', 'p', 'mp', 'mf', 'f', 'ff', 'fff'}
    for child in dyn_elem:
        if child.tag in _DYNAMICS_TAGS:
            return child.tag
    return None


# ---------------------------------------------------------------------------
# Direction-level dynamics (measure pre-pass)
# ---------------------------------------------------------------------------

_DYNAMICS_TAGS: set[str] = {'ppp', 'pp', 'p', 'mp', 'mf', 'f', 'ff', 'fff'}


def _collect_measure_directions(
    measure: ET.Element,
    abs_div_start: int,
    divisions: int,
    tempo_map: list[tuple[int, float]],
) -> dict:
    """Walk <direction> elements in a measure and collect:

    - dynamics: list of (abs_div, dyn_string) — direction-level dynamics
    - wedge_starts: list of (abs_div, 'cre'|'dec') — hairpin starts
    - wedge_end_divs: set of abs_div where a wedge ends
    - words: list of (abs_div, text) — <words> text directions
    - pedals: list of (abs_div, 'start'|'stop'|'change') — sustain pedal
      events (<pedal type=...>; 'continue' line segments are ignored)
    - octave_shift: True when an <octave-shift> direction is present
      (schema v1 non-feature — caller logs and drops)

    abs_div_start is the division position at the start of the measure.
    All positions are absolute divisions within the song.
    """
    result: dict = {
        'dynamics': [],
        'wedge_starts': [],
        'wedge_end_divs': set(),
        'words': [],
        'pedals': [],
        'octave_shift': False,
    }
    # Track a running cursor for directions within the measure.
    # last_note_start captures the cursor before it advances on each note so
    # that a <direction> appearing after its target note (post-annotation style,
    # common in Sibelius/MuseScore exports) is attributed to that note's beat
    # rather than the one that follows.
    cursor = abs_div_start
    last_note_start = cursor

    for elem in measure:
        if elem.tag == 'note':
            dur_text = elem.findtext('duration')
            if dur_text and elem.find('chord') is None and elem.find('grace') is None:
                last_note_start = cursor
                cursor += int(dur_text)
        elif elem.tag == 'backup':
            dur_text = elem.findtext('duration')
            if dur_text:
                cursor -= int(dur_text)
                # After a backup the cursor points to the start of the next
                # staff's content. Reset last_note_start so that any
                # <direction> that appears before the first note on the lower
                # staff (common in piano grand-staff MusicXML) is attributed to
                # the correct beat rather than the last beat of the upper staff.
                last_note_start = cursor
        elif elem.tag == 'forward':
            dur_text = elem.findtext('duration')
            if dur_text:
                cursor += int(dur_text)
                # A <forward> skips over rests. Update last_note_start so
                # that a <direction> after the skip is attributed to the
                # post-forward position, not the pre-backup position.
                last_note_start = cursor
        elif elem.tag == 'direction':
            dir_div = last_note_start  # position at this direction
            for dt in elem.findall('direction-type'):
                # Dynamics
                dyn_elem = dt.find('dynamics')
                if dyn_elem is not None:
                    for child in dyn_elem:
                        if child.tag in _DYNAMICS_TAGS:
                            result['dynamics'].append((dir_div, child.tag))
                            break
                # Wedge (crescendo / diminuendo)
                wedge = dt.find('wedge')
                if wedge is not None:
                    wtype = wedge.get('type', '')
                    if wtype == 'crescendo':
                        result['wedge_starts'].append((dir_div, 'cre'))
                    elif wtype == 'diminuendo':
                        result['wedge_starts'].append((dir_div, 'dec'))
                    elif wtype == 'stop':
                        result['wedge_end_divs'].add(dir_div)
                # Words
                words = dt.find('words')
                if words is not None and words.text:
                    result['words'].append((dir_div, words.text.strip()))
                # Sustain pedal (spd / sph / spu per feedpak spec §7.6)
                pedal = dt.find('pedal')
                if pedal is not None:
                    ptype = pedal.get('type', '')
                    if ptype in ('start', 'stop', 'change'):
                        result['pedals'].append((dir_div, ptype))
                # Ottava — schema v1 non-feature, dropped by the caller
                if dt.find('octave-shift') is not None:
                    result['octave_shift'] = True

    # In multi-staff measures, <backup> can produce directions with an earlier
    # div than directions already collected from the upper staff. Sort all
    # positional lists so callers that break on div > beat_div work correctly.
    result['dynamics'].sort(key=lambda e: e[0])
    result['wedge_starts'].sort(key=lambda e: e[0])
    result['words'].sort(key=lambda e: e[0])
    result['pedals'].sort(key=lambda e: e[0])

    return result


def _active_dynamic(
    beat_div: int,
    dynamics: list[tuple[int, str]],
) -> str | None:
    """Return the dynamic whose position exactly matches beat_div, or None.

    The notation dyn field is a per-beat annotation recording where the symbol appears in
    the score, not a persistent level — exact matching is intentional.
    """
    for div, dyn in dynamics:
        if div == beat_div:
            return dyn
        if div > beat_div:
            break
    return None


def _active_wedge(
    beat_div: int,
    wedge_starts: list[tuple[int, str]],
    wedge_end_divs: set[int],
) -> tuple[bool, bool]:
    """Return (cre, dec) for the beat at beat_div."""
    cre = False
    dec = False
    for div, kind in wedge_starts:
        if div <= beat_div:
            # Is there a stop after this start but at or before beat_div?
            ended = any(ed > div and ed <= beat_div for ed in wedge_end_divs)
            if not ended:
                if kind == 'cre':
                    cre = True
                else:
                    dec = True
        else:
            break
    return cre, dec


# ---------------------------------------------------------------------------
# Sustain pedal (spd / sph / spu — feedpak spec §7.6)
# ---------------------------------------------------------------------------

def _pedal_staff(staves_def: dict[str, str]) -> str:
    """Return the staff id pedal marks attach to (the bottom staff).

    Pedal directions apply to the instrument; renderers draw the pedal
    line below the bottom staff, so beats on that staff carry the flags.
    """
    # Staff numbering (see _staff_id): 1→rh (top), 2→lh, N≥3→staff_N.
    # The bottom staff is the HIGHEST-numbered one, so staff_N (max N) must
    # be considered before lh — otherwise a 3-staff organ part (rh/lh/staff_3)
    # mis-tags the pedal on the middle staff instead of the actual bottom.
    extra = [s for s in staves_def if s.startswith('staff_')]
    if extra:
        return max(extra, key=lambda s: int(s.split('_')[-1]))
    if 'lh' in staves_def:
        return 'lh'
    if 'rh' in staves_def:
        return 'rh'
    return next(iter(staves_def), 'rh')


def _pedal_flags(
    beat_div: int,
    pedal_events: list[tuple[int, str]],
) -> tuple[bool, bool, bool]:
    """Return (spd, sph, spu) for the beat at beat_div.

    MusicXML mapping (feedpak spec §7.6): <pedal type="start"> → spd,
    <pedal type="stop"> → spu, <pedal type="change"> → spu + spd on the
    same beat (re-pedal). Beats strictly inside an active span carry sph.
    pedal_events must be sorted by division.
    """
    spd = spu = False
    active_before = False
    for div, kind in pedal_events:
        if div < beat_div:
            active_before = kind in ('start', 'change')
        elif div == beat_div:
            if kind == 'start':
                spd = True
            elif kind == 'stop':
                spu = True
            else:  # change — re-pedal
                spd = True
                spu = True
        else:
            break
    sph = active_before and not (spd or spu)
    return spd, sph, spu


# ---------------------------------------------------------------------------
# Clef helpers
# ---------------------------------------------------------------------------

_CLEF_MAP: dict[tuple[str, str], str] = {
    ('G', '2'): 'G2',
    ('G', '1'): 'G2',  # French violin clef — treat as treble
    ('F', '4'): 'F4',
    ('F', '3'): 'F4',  # baritone — approximate as bass
    ('C', '3'): 'C3',
    ('C', '4'): 'C4',
    ('percussion', ''): 'neutral',
    ('PERCUSSION', ''): 'neutral',
}


def _clef_string(sign: str, line: str) -> str:
    """Map MusicXML clef sign + line to notation clef string."""
    key = (sign.strip(), line.strip())
    if key in _CLEF_MAP:
        return _CLEF_MAP[key]
    # Fallback: G → treble, F → bass, C → alto, else neutral
    s = sign.strip().upper()
    if s == 'G':
        return 'G2'
    if s == 'F':
        return 'F4'
    if s == 'C':
        return 'C3'
    return 'neutral'


def _beat_groups(ts_beats: int, ts_beat_type: int) -> list[int] | None:
    """Return primary beat groups for compound/irregular meters, or None for simple.

    Simple (denominator ≤ 4, or denom ≥ 8 with numerator not divisible by 3): None.
    Compound (denom ≥ 8, numerator divisible by 3, numerator ≥ 6): list of 3s.
    Irregular (denom ≥ 8, numerator not divisible by 3, numerator ≥ 5): 2s then 3.
    """
    if ts_beat_type <= 4:
        return None
    if ts_beats % 3 == 0 and ts_beats >= 6:
        return [3] * (ts_beats // 3)
    if ts_beats >= 5:
        groups: list[int] = []
        remaining = ts_beats
        while remaining > 3:
            groups.append(2)
            remaining -= 2
        groups.append(remaining)
        return groups
    return None


def _staff_id(staff_number: int) -> str:
    """Map MusicXML 1-based staff number to notation staff id."""
    # Convention: staff 1 = right hand / treble = 'rh'
    #             staff 2 = left hand / bass    = 'lh'
    #             staff N≥3                     = 'staff_N'
    if staff_number == 1:
        return 'rh'
    if staff_number == 2:
        return 'lh'
    return f'staff_{staff_number}'


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------

def parse_musicxml(xml_bytes: bytes) -> dict:
    """Parse MusicXML bytes and return a conversion result dict.

    Returns:
        {
          'title': str,
          'composer': str,
          'duration': float,
          'notation': dict,          # notation_<id>.json payload
          'song_timeline': dict,     # song_timeline.json payload
          'midi_bytes': bytes,
          'part_names': list[str],
          'measure_count': int,
        }
    """
    root = ET.fromstring(xml_bytes)

    if root.tag != 'score-partwise':
        raise ValueError(
            f"Expected score-partwise root element, got <{root.tag}>. "
            "Only score-partwise MusicXML is supported."
        )

    # ── Metadata ────────────────────────────────────────────────────────────
    title = ''
    composer = ''
    work = root.find('work')
    if work is not None:
        title = work.findtext('work-title') or ''
    if not title:
        title = root.findtext('movement-title') or ''

    for credit in root.findall('credit'):
        for cw in credit.findall('credit-words'):
            text = (cw.text or '').strip()
            if not text:
                continue
            if not title:
                title = text
            elif not composer and text != title:
                composer = text
            break

    # <identification> credits (feedpak spec §7.6: rights / lyricist /
    # arranger ride on the notation payload; composer is a fallback for
    # the credit-words heuristic above).
    rights = ''
    lyricist = ''
    arranger = ''
    identification = root.find('identification')
    if identification is not None:
        rights = (identification.findtext('rights') or '').strip()
        for creator in identification.findall('creator'):
            text = (creator.text or '').strip()
            if not text:
                continue
            ctype = creator.get('type', '')
            if ctype == 'lyricist' and not lyricist:
                lyricist = text
            elif ctype == 'arranger' and not arranger:
                arranger = text
            elif ctype == 'composer' and not composer:
                composer = text

    part_names: list[str] = []
    part_list = root.find('part-list')
    if part_list is not None:
        for sp in part_list.findall('score-part'):
            name = sp.findtext('part-name') or sp.get('id') or 'Part'
            part_names.append(name)

    instrument = _infer_instrument(part_names[0]) if part_names else 'unknown'

    # ── Tempo map ───────────────────────────────────────────────────────────
    tempo_map = _build_tempo_map(root)

    # ── Parse first part only ───────────────────────────────────────────────
    parts = root.findall('part')
    if not parts:
        raise ValueError("No <part> elements found in score.")

    part = parts[0]

    # State that persists across measures
    divisions = 1
    abs_div = 0           # running absolute position in divisions
    ts_beats = 4
    ts_beat_type = 4
    # Current key signature (fifths, -7..+7)
    current_ks: int | None = None
    # Current tempo
    current_tempo: float | None = None
    # Per-staff clef: staff_number -> clef_string
    current_clef: dict[int, str] = {}

    # Change-tracking (for "omit if unchanged" fields on measures)
    prev_ts: list[int] | None = None
    prev_ks: int | None = None
    prev_tempo: float | None = None
    prev_clef: dict[str, str] = {}  # staff_id -> clef_string

    # Notation staves: staff_id -> initial clef
    # Built from the first <attributes><clef> we encounter.
    staves_def: dict[str, str] = {}   # staff_id -> clef_string (for staves[] list)
    staves_seen: set[str] = set()

    # Output accumulators
    measures_out: list[dict] = []
    beats_out: list[dict] = []
    sections_out: list[dict] = []
    # song_timeline tempo / time-signature maps (feedpak spec §7.4, 1.2.0).
    # Tempo events are consumed from tempo_map measure by measure so each
    # event's div→seconds conversion uses the divisions value active in its
    # own measure (divisions can change mid-score).
    tempos_out: list[dict] = []
    ts_changes_out: list[dict] = []
    tempo_map_idx = 0
    midi_notes: list[tuple[float, float, int, int]] = []
    max_time = 0.0
    section_counters: dict[str, int] = {}

    # Tie tracking: (staff_id, voice_id, midi) -> True (open tie)
    tie_open: dict[tuple[str, str, int], bool] = {}
    # Wavy-line (vibrato) span tracking: (staff_id, voice) -> True when open
    vibrato_open: dict[tuple[str, str], bool] = {}
    # Whether a time signature has been explicitly declared yet (needed to
    # tell an initial declaration from a mid-measure change).
    ts_declared = False
    # Sustain pedal events accumulated across the whole part, sorted by div.
    pedal_events_all: list[tuple[int, str]] = []
    # Schema v1 non-features dropped from this score (warn once per feature).
    dropped_features: set[str] = set()

    def _warn_dropped(feature: str, measure_number: int) -> None:
        if feature in dropped_features:
            return
        dropped_features.add(feature)
        log.warning(
            "%s (first seen in measure %s) is not representable in notation "
            "schema v1 — dropped, not approximated (feedpak spec §7.6)",
            feature, measure_number,
        )

    for measure_elem in part.findall('measure'):
        measure_number = 1
        try:
            measure_number = int(measure_elem.get('number') or 1)
        except ValueError:
            pass

        measure_abs_div_start = abs_div

        # ── Pre-pass: collect attributes, in document order ────────────────
        # MusicXML allows <attributes> after notes (as in our test file).
        # An <attributes> that *first declares* a value (or restates the
        # current one) is applied measure-granularly wherever it appears;
        # an <attributes> that *changes* time / key / clef after notes have
        # sounded in the measure is a mid-measure change — a schema v1
        # non-feature that MUST be dropped with a warning, never
        # approximated (feedpak spec §7.6).
        notes_seen_in_measure = False
        # Use a division cursor to detect genuinely mid-measure <attributes>
        # changes. Heuristics based on "notes seen" are unreliable in multi-
        # voice/multi-staff scores where <backup> rewinds the cursor to an
        # earlier position before the next voice/staff's content. An
        # <attributes> block is mid-measure only when the cursor is strictly
        # past the measure start at the point it appears.
        pre_pass_cursor = measure_abs_div_start
        # Clefs valid for THIS measure's rendering: boundary changes apply
        # here; a mid-measure change only persists into current_clef for
        # the following measures (dropped for this one, with the warning).
        measure_clef = dict(current_clef)
        for elem in measure_elem:
            if elem.tag == 'note':
                notes_seen_in_measure = True
                dur_text = elem.findtext('duration')
                if dur_text and elem.find('chord') is None and elem.find('grace') is None:
                    pre_pass_cursor += int(dur_text)
                continue
            if elem.tag == 'backup':
                dur_text = elem.findtext('duration')
                if dur_text:
                    pre_pass_cursor -= int(dur_text)
                continue
            if elem.tag == 'forward':
                dur_text = elem.findtext('duration')
                if dur_text:
                    pre_pass_cursor += int(dur_text)
                continue
            if elem.tag != 'attributes':
                continue
            # Cursor at the time this <attributes> block is encountered.
            # If it equals the measure start, the block is at the boundary
            # (start or after a full-measure backup) — treat as non-mid-measure.
            attr_cursor = pre_pass_cursor
            attr = elem
            d = attr.findtext('divisions')
            if d:
                divisions = int(d)
            time_elem = attr.find('time')
            if time_elem is not None:
                b = time_elem.findtext('beats')
                bt = time_elem.findtext('beat-type')
                new_beats = int(b) if b else ts_beats
                new_beat_type = int(bt) if bt else ts_beat_type
                if (attr_cursor > measure_abs_div_start and ts_declared
                        and (new_beats, new_beat_type) != (ts_beats, ts_beat_type)):
                    _warn_dropped('mid-measure time-signature change', measure_number)
                else:
                    ts_beats = new_beats
                    ts_beat_type = new_beat_type
                    ts_declared = True
            key_elem = attr.find('key')
            if key_elem is not None:
                fifths_text = key_elem.findtext('fifths')
                if fifths_text is not None:
                    new_ks = max(-7, min(7, int(fifths_text)))
                    if (attr_cursor > measure_abs_div_start and current_ks is not None
                            and new_ks != current_ks):
                        _warn_dropped('mid-measure key-signature change', measure_number)
                    else:
                        current_ks = new_ks
            for clef_elem in attr.findall('clef'):
                staff_num = int(clef_elem.get('number') or '1')
                sign = clef_elem.findtext('sign') or 'G'
                line = clef_elem.findtext('line') or ''
                cs = _clef_string(sign, line)
                if (attr_cursor > measure_abs_div_start
                        and current_clef.get(staff_num) is not None
                        and cs != current_clef[staff_num]):
                    # Mid-measure clef change: the current measure cannot
                    # represent it (schema v1 non-feature), but the new clef
                    # persists in MusicXML until another change, so update
                    # current_clef now. Without this, every following measure
                    # renders with the old clef even when the change was
                    # correctly placed at the next measure boundary.
                    # measure_clef is deliberately NOT updated — this
                    # measure keeps the clef it opened with.
                    _warn_dropped('mid-measure clef change', measure_number)
                else:
                    measure_clef[staff_num] = cs
                current_clef[staff_num] = cs
                sid = _staff_id(staff_num)
                staves_seen.add(sid)
                if sid not in staves_def:
                    staves_def[sid] = cs

        # ── Collect direction-level dynamics/wedge/words for this measure ──
        directions = _collect_measure_directions(
            measure_elem, measure_abs_div_start, divisions, tempo_map
        )

        # Sustain pedal events accumulate across the part (a span may cross
        # measure boundaries). Measures are processed in order, but within a
        # multi-staff measure a <backup> can produce events with an abs_div
        # earlier than events already collected from the upper staff — so sort
        # by division after each extend. _pedal_flags relies on sorted order.
        pedal_events_all.extend(directions['pedals'])
        pedal_events_all.sort(key=lambda e: e[0])

        # Ottava — schema v1 non-feature: drop with a warning.
        if directions['octave_shift']:
            _warn_dropped('ottava (octave-shift)', measure_number)

        # Tempo from sound elements was handled in _build_tempo_map;
        # read it back here for the measure's tempo field.
        current_tempo = _bpm_at(measure_abs_div_start, tempo_map)

        # ── Emit beats for song_timeline ───────────────────────────────────
        _bg = _beat_groups(ts_beats, ts_beat_type)
        if _bg is None:
            quarter_note_dur = divisions
            quarters_per_measure = ts_beats * 4 / ts_beat_type
            n_quarter_beats = max(1, round(quarters_per_measure))
            for qi in range(n_quarter_beats):
                beat_div = measure_abs_div_start + qi * quarter_note_dur
                beat_time = _div_to_seconds(beat_div, divisions, tempo_map)
                beats_out.append({
                    'time': round(beat_time, 4),
                    'measure': measure_number if qi == 0 else -1,
                })
        else:
            denom_unit_in_divisions = divisions * 4 // ts_beat_type
            offset = 0
            for i, group in enumerate(_bg):
                beat_div = measure_abs_div_start + offset * denom_unit_in_divisions
                beat_time = _div_to_seconds(beat_div, divisions, tempo_map)
                beats_out.append({
                    'time': round(beat_time, 4),
                    'measure': measure_number if i == 0 else -1,
                })
                offset += group

        # ── Rehearsal marks → sections ─────────────────────────────────────
        for direction in measure_elem.findall('direction'):
            for dt in direction.findall('direction-type'):
                rehearsal = dt.find('rehearsal')
                if rehearsal is not None and rehearsal.text:
                    r_time = _div_to_seconds(measure_abs_div_start, divisions, tempo_map)
                    name = rehearsal.text.strip()
                    section_counters[name] = section_counters.get(name, 0) + 1
                    sections_out.append({
                        'time': round(r_time, 4),
                        'name': name,
                        'number': section_counters[name],
                    })

        # ── Walk notes ─────────────────────────────────────────────────────
        # Per-staff, per-voice beat accumulator for this measure.
        # staff_id -> voice_id -> list[beat_dict]
        measure_beats: dict[str, dict[str, list[dict]]] = {}

        # Voice cursor: voice_id -> abs_div
        voice_cursor: dict[str, int] = {}

        for elem in measure_elem:
            if elem.tag == 'backup':
                dur = elem.findtext('duration')
                if dur:
                    abs_div -= int(dur)
                continue

            if elem.tag == 'forward':
                dur = elem.findtext('duration')
                if dur:
                    abs_div += int(dur)
                continue

            if elem.tag != 'note':
                continue

            is_rest = elem.find('rest') is not None
            is_chord = elem.find('chord') is not None
            is_grace = elem.find('grace') is not None

            voice = elem.findtext('voice') or '1'
            staff_num = int(elem.findtext('staff') or '1')
            sid = _staff_id(staff_num)
            staves_seen.add(sid)

            note_type = elem.findtext('type')
            dur_val = _type_to_dur(note_type)
            dots = _count_dots(elem)
            tuplet = _parse_time_modification(elem)

            # Advance cursor for non-chord, non-grace notes
            if not is_chord and not is_grace:
                voice_cursor[voice] = abs_div
                dur_text = elem.findtext('duration')
                if dur_text:
                    abs_div += int(dur_text)

            note_div = voice_cursor.get(voice, abs_div)
            note_time = round(_div_to_seconds(note_div, divisions, tempo_map), 4)

            # Sustain for MIDI only (non-grace, non-rest)
            midi_dur_divs = int(elem.findtext('duration') or 0) if not is_grace else 0
            sustain = round(
                (midi_dur_divs / divisions) * (60.0 / _bpm_at(note_div, tempo_map)), 4
            ) if midi_dur_divs > 0 else 0.0

            if note_time + sustain > max_time:
                max_time = note_time + sustain

            # Ensure staff/voice slot exists
            if sid not in measure_beats:
                measure_beats[sid] = {}
            if voice not in measure_beats[sid]:
                measure_beats[sid][voice] = []

            if is_rest:
                # Emit a rest beat (no notes array)
                beat: dict = {'t': note_time, 'dur': dur_val, 'rest': True}
                if dots:
                    beat['dot'] = dots
                if tuplet:
                    beat['tu'] = tuplet
                # Rests on the pedal staff still sit inside a pedal span.
                if pedal_events_all and sid == _pedal_staff(staves_def):
                    spd, sph, spu = _pedal_flags(note_div, pedal_events_all)
                    if spd:
                        beat['spd'] = True
                    if sph:
                        beat['sph'] = True
                    if spu:
                        beat['spu'] = True
                measure_beats[sid][voice].append(beat)
                continue

            # ── Pitch ──────────────────────────────────────────────────────
            pitch_elem = elem.find('pitch')
            if pitch_elem is None:
                continue  # unpitched — skip

            step = pitch_elem.findtext('step') or 'C'
            alter_text = pitch_elem.findtext('alter')
            alter = round(float(alter_text)) if alter_text else 0
            octave = int(pitch_elem.findtext('octave') or '4')
            midi = max(0, min(127, _pitch_to_midi(step, alter, octave)))

            # ── Tie handling ───────────────────────────────────────────────
            tie_types = [t.get('type') for t in elem.findall('tie')]
            tie_key = (sid, voice, midi)
            is_tied_continuation = 'stop' in tie_types and tie_key in tie_open

            # ── Build note dict ────────────────────────────────────────────
            note_dict: dict = {'midi': midi}

            if is_tied_continuation:
                note_dict['tied'] = True
                # Remove from open ties (unless this note also starts a new tie)
                if 'start' not in tie_types:
                    del tie_open[tie_key]

            if 'start' in tie_types:
                tie_open[tie_key] = True

            # Accidental
            acc = _parse_accidental(elem, alter)
            if acc is not None:
                note_dict['acc'] = acc

            # Stem direction override (<stem>up|down</stem>); other values
            # ('none', 'double') are renderer-decided in schema v1 → omit.
            stem_text = (elem.findtext('stem') or '').strip().lower()
            if stem_text in ('up', 'down'):
                note_dict['stem'] = stem_text

            # Articulations
            note_dict.update(_parse_articulations(elem))

            # Technical
            note_dict.update(_parse_technical(elem))

            # ── Beat-level effects from this note ──────────────────────────
            beat_effects: dict = {}

            # Ornaments (trill, vibrato) → beat level
            beat_effects.update(_parse_ornaments(elem))

            # Slur → beat level
            slur_start, slur_end = _parse_slur(elem)
            if slur_start:
                beat_effects['slr'] = True
            if slur_end:
                beat_effects['slre'] = True

            # Fermata → typed ferm flag at beat level (feedpak spec §7.6)
            if _parse_fermata(elem):
                beat_effects['ferm'] = True

            # Arpeggiated (rolled) chord → beat-level arp flag
            if elem.find('notations/arpeggiate') is not None:
                beat_effects['arp'] = True

            # Schema v1 non-features on notes: drop with a warning.
            if elem.find('notations/ornaments/tremolo') is not None:
                _warn_dropped('tremolo', measure_number)
            if elem.find('notations/glissando') is not None:
                _warn_dropped('notated glissando line', measure_number)

            # Direction-level dynamic (Option C: note-level wins below)
            dir_dyn = _active_dynamic(note_div, directions['dynamics'])
            if dir_dyn:
                beat_effects['dyn'] = dir_dyn

            # Note-level dynamic (wins over direction-level)
            note_dyn = _parse_note_dynamics(elem)
            if note_dyn:
                beat_effects['dyn'] = note_dyn

            # Wedge (hairpin)
            cre, dec = _active_wedge(
                note_div, directions['wedge_starts'], directions['wedge_end_divs']
            )
            if cre:
                beat_effects['cre'] = True
            if dec:
                beat_effects['dec'] = True

            # Wavy-line span detection — collect start/stop before applying
            notations_elem = elem.find('notations')
            wl_start = wl_stop = False
            if notations_elem is not None:
                orn_elem = notations_elem.find('ornaments')
                if orn_elem is not None:
                    for wl in orn_elem.findall('wavy-line'):
                        wl_type = wl.get('type', '')
                        if wl_type == 'start':
                            wl_start = True
                        elif wl_type == 'stop':
                            wl_stop = True
            if wl_start:
                vibrato_open[(sid, voice)] = True
            # Apply vib before closing so the stop note is included in the span
            if vibrato_open.get((sid, voice)):
                beat_effects['vib'] = True
            if wl_stop:
                vibrato_open.pop((sid, voice), None)

            # Sustain pedal flags (spd / sph / spu) — bottom staff only;
            # grace beats share the principal note's division, so the flags
            # go on the principal beat alone.
            if pedal_events_all and not is_grace and sid == _pedal_staff(staves_def):
                spd, sph, spu = _pedal_flags(note_div, pedal_events_all)
                if spd:
                    beat_effects['spd'] = True
                if sph:
                    beat_effects['sph'] = True
                if spu:
                    beat_effects['spu'] = True

            # ── Chord vs new beat ──────────────────────────────────────────
            voice_beats = measure_beats[sid][voice]

            if is_chord and voice_beats and not is_grace:
                # Append note to the last open beat in this voice
                last_beat = voice_beats[-1]
                if 'notes' not in last_beat:
                    last_beat['notes'] = []
                last_beat['notes'].append(note_dict)
                # Merge beat-level effects onto the existing beat
                # (note-level dynamic wins if already set)
                if 'dyn' in beat_effects and 'dyn' not in last_beat:
                    last_beat['dyn'] = beat_effects['dyn']
                # Boolean beat flags carried by chord notes (e.g. an
                # <arpeggiate>/<fermata> on a non-first chord note) merge up.
                for flag in ('arp', 'ferm'):
                    if beat_effects.get(flag):
                        last_beat[flag] = True
            else:
                # Open a new beat
                beat = {'t': note_time, 'dur': dur_val}
                if dots:
                    beat['dot'] = dots
                if tuplet:
                    beat['tu'] = tuplet
                if is_grace:
                    # Typed grace string (feedpak spec §7.6, GRACE_TYPES):
                    # "a" = acciaccatura (<grace slash="yes">),
                    # "p" = appoggiatura (plain <grace>).
                    grace_elem = elem.find('grace')
                    slashed = (grace_elem is not None
                               and grace_elem.get('slash') == 'yes')
                    beat['grace'] = 'a' if slashed else 'p'
                denom_unit = divisions * 4 // ts_beat_type  # divisions per ts-denominator note
                if denom_unit > 0:
                    pos_num = (note_div - measure_abs_div_start) // denom_unit
                    if pos_num > 0:
                        beat['beat_pos'] = [pos_num, ts_beat_type]
                beat.update(beat_effects)
                beat['notes'] = [note_dict]
                voice_beats.append(beat)

            # MIDI accumulation (skip grace notes)
            if not is_grace and sustain > 0:
                velocity = 80
                midi_notes.append((note_time, sustain, midi, velocity))

        # ── Flush measure ──────────────────────────────────────────────────
        ts = [ts_beats, ts_beat_type]
        measure_dict: dict = {'idx': measure_number, 't': round(
            _div_to_seconds(measure_abs_div_start, divisions, tempo_map), 4
        )}

        # Anacrusis (pickup / upbeat) measure — MusicXML implicit="yes".
        if measure_elem.get('implicit') == 'yes':
            measure_dict['pickup'] = True

        # Omit ts/ks/tempo when unchanged from previous measure
        if ts != prev_ts:
            measure_dict['ts'] = ts
            prev_ts = ts
            ts_changes_out.append({'time': measure_dict['t'], 'ts': ts})

        groups = _beat_groups(ts_beats, ts_beat_type)
        if groups is not None:
            measure_dict['beat_groups'] = groups

        if current_ks is not None and current_ks != prev_ks:
            measure_dict['ks'] = current_ks
            prev_ks = current_ks

        if current_tempo is not None and round(current_tempo, 3) != (
            round(prev_tempo, 3) if prev_tempo is not None else None
        ):
            measure_dict['tempo'] = round(current_tempo, 3)
            prev_tempo = current_tempo

        # Build per-staff staves dict for this measure
        measure_staves: dict = {}
        for sid in sorted(measure_beats):
            voices_list = []
            for vid in sorted(measure_beats[sid]):
                b_list = measure_beats[sid][vid]
                if b_list:
                    voices_list.append({'v': int(vid), 'beats': b_list})
            if voices_list:
                staff_dict: dict = {'voices': voices_list}
                # Per-staff clef — omit if unchanged
                staff_num_for_id = (
                    1 if sid == 'rh' else 2 if sid == 'lh'
                    else int(sid.split('_')[-1]) if sid.startswith('staff_') else 1
                )
                clef_now = measure_clef.get(staff_num_for_id)
                if clef_now and prev_clef.get(sid) != clef_now:
                    staff_dict['clef'] = clef_now
                    prev_clef[sid] = clef_now
                measure_staves[sid] = staff_dict

        if measure_staves:
            measure_dict['staves'] = measure_staves

        measures_out.append(measure_dict)

        # Consume tempo-map events that fall inside this measure, converting
        # with the divisions value active here. pre_pass_cursor ended the
        # attribute pre-pass at the measure's net end division.
        measure_end_div = max(pre_pass_cursor, abs_div)
        while (tempo_map_idx < len(tempo_map)
               and tempo_map[tempo_map_idx][0] < measure_end_div):
            ev_div, ev_bpm = tempo_map[tempo_map_idx]
            tempos_out.append({
                'time': round(_div_to_seconds(ev_div, divisions, tempo_map), 4),
                'bpm': round(ev_bpm, 3),
            })
            tempo_map_idx += 1

    # ── Finalize tempo map for song_timeline ────────────────────────────────
    # Leftover events at/after the final measure's end (rare) convert with
    # the last-seen divisions.
    while tempo_map_idx < len(tempo_map):
        ev_div, ev_bpm = tempo_map[tempo_map_idx]
        tempos_out.append({
            'time': round(_div_to_seconds(ev_div, divisions, tempo_map), 4),
            'bpm': round(ev_bpm, 3),
        })
        tempo_map_idx += 1
    # Same-time events collapse to the last one (the builder's default
    # 120 BPM seed at div 0 loses to a score-declared initial tempo);
    # consecutive equal BPMs are redundant per §7.4's until-next-entry rule.
    tempos_deduped: list[dict] = []
    for ev in tempos_out:
        if tempos_deduped and tempos_deduped[-1]['time'] == ev['time']:
            tempos_deduped[-1] = ev
        elif not tempos_deduped or tempos_deduped[-1]['bpm'] != ev['bpm']:
            tempos_deduped.append(ev)

    # ── Deduplicate beats ───────────────────────────────────────────────────
    seen_beat_times: set[float] = set()
    beats_deduped: list[dict] = []
    for b in sorted(beats_out, key=lambda x: x['time']):
        if b['time'] not in seen_beat_times:
            seen_beat_times.add(b['time'])
            beats_deduped.append(b)

    # ── Build staves[] list (static staff definitions) ──────────────────────
    _STAFF_ORDER = ['rh', 'lh']
    _STAFF_LABELS = {'rh': 'Right Hand', 'lh': 'Left Hand'}
    ordered_staves = [s for s in _STAFF_ORDER if s in staves_def]
    ordered_staves += sorted(s for s in staves_def if s not in _STAFF_ORDER)
    arr_name = re.sub(r'[^a-z0-9]', '_', instrument).replace('_', ' ').title()
    if len(ordered_staves) == 1 and instrument not in _PIANO_FAMILY:
        staves_list = [
            {'id': ordered_staves[0], 'clef': staves_def[ordered_staves[0]],
             'label': arr_name}
        ]
    else:
        staves_list = [
            {'id': sid, 'clef': staves_def[sid],
             'label': _STAFF_LABELS.get(sid, sid)}
            for sid in ordered_staves
        ]

    duration = round(max_time + 0.5, 2) if max_time > 0 else 30.0

    # ── Notation payload ────────────────────────────────────────────────────
    notation = {
        'version': 1,
        'instrument': instrument,
        'staves': staves_list,
        'measures': measures_out,
    }
    # Optional credits (feedpak spec §7.6) — omit when absent.
    if rights:
        notation['rights'] = rights
    if lyricist:
        notation['lyricist'] = lyricist
    if arranger:
        notation['arranger'] = arranger

    # ── Song timeline payload (feedpak spec §7.4) ───────────────────────────
    song_timeline = {
        'version': 1,
        'tempos': tempos_deduped,
        'time_signatures': ts_changes_out,
        'beats': [{'time': b['time'], 'measure': b['measure']} for b in beats_deduped],
        'sections': [
            {'time': s['time'], 'name': s['name'], 'number': s.get('number', 1)}
            for s in sorted(sections_out, key=lambda x: x['time'])
        ],
    }

    midi_bytes = _build_midi(midi_notes, tempo_map)

    return {
        'title': (title.strip() or 'Untitled'),
        'composer': composer.strip(),
        'duration': duration,
        'instrument': instrument,
        'notation': notation,
        'song_timeline': song_timeline,
        'midi_bytes': midi_bytes,
        'part_names': part_names,
        'measure_count': len(measures_out),
    }


# ---------------------------------------------------------------------------
# MIDI builder (audio only — grace notes excluded)
# ---------------------------------------------------------------------------

def _build_midi(
    midi_notes: list[tuple[float, float, int, int]],
    tempo_map: list[tuple[int, float]],
) -> bytes:
    """Build a type-0 MIDI file from (start_sec, dur_sec, pitch, velocity)."""
    try:
        from midiutil import MIDIFile
    except ImportError as e:
        raise RuntimeError("midiutil not available — cannot generate MIDI") from e

    REFERENCE_BPM = 120.0
    TICKS_PER_BEAT = 480

    midi = MIDIFile(1, ticks_per_quarternote=TICKS_PER_BEAT)
    midi.addTrackName(0, 0, 'Piano')
    midi.addTempo(0, 0, REFERENCE_BPM)
    midi.addProgramChange(0, 0, 0, 0)   # GM 0 = Grand Piano

    def secs_to_beats(s: float) -> float:
        return s * (REFERENCE_BPM / 60.0)

    for start_sec, dur_sec, pitch, velocity in midi_notes:
        if pitch < 0 or pitch > 127:
            continue
        if dur_sec <= 0:
            dur_sec = 0.05
        beat = secs_to_beats(start_sec)
        dur_beats = secs_to_beats(dur_sec)
        velocity = max(1, min(127, velocity))
        midi.addNote(0, 0, pitch, beat, dur_beats, velocity)

    buf = io.BytesIO()
    midi.writeFile(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Feedpak zip builder
# ---------------------------------------------------------------------------

def build_feedpak_zip(
    result: dict,
    audio_path: str | None,
    title: str,
    composer: str,
) -> bytes:
    """Assemble a .feedpak zip from parse result + rendered audio.

    Manifest shape (feedpak spec §5):
      - feedpak_version stamped from the host's constant (§4.1)
      - arrangements[0]: notation-only (notation=<file>, no file: — §5.2)
      - song_timeline: song_timeline.json (§7.4)
      - stems: [full.ogg] when audio rendered successfully; omitted
        otherwise — such a pack is a local authoring intermediate under
        the §5.3.2 carve-out, not portable until stems are added

    Returns zip bytes.
    """
    buf = io.BytesIO()
    duration = result['duration']

    instrument = result.get('instrument', 'unknown')
    arr_id = re.sub(r'[^a-z0-9]', '_', instrument)
    notation_filename = f'notation_{arr_id}.json'
    arr_name = arr_id.replace('_', ' ').title()

    arrangement: dict = {
        'id': arr_id,
        'name': arr_name,
        # file: intentionally omitted — spec §5.2 allows notation-only
        # arrangements when notation: is present.
        'notation': notation_filename,
    }
    if instrument != 'unknown':
        # `type` is an optional instrument hint with a known vocabulary
        # (§5.2); omit it rather than emit a non-vocabulary placeholder.
        arrangement['type'] = instrument

    manifest: dict = {
        'feedpak_version': _FEEDPAK_VERSION,
        'title': title,
        'artist': composer or 'Unknown',
        'duration': duration,
        'arrangements': [arrangement],
        'song_timeline': 'song_timeline.json',
    }

    if audio_path:
        manifest['stems'] = [
            {'id': 'full', 'file': 'stems/full.ogg', 'default': True}
        ]

    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            'manifest.yaml',
            yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False),
        )
        zf.writestr(
            notation_filename,
            json.dumps(result['notation'], separators=(',', ':')),
        )
        zf.writestr(
            'song_timeline.json',
            json.dumps(result['song_timeline'], separators=(',', ':')),
        )
        if audio_path and os.path.exists(audio_path):
            zf.write(audio_path, 'stems/full.ogg')
        elif audio_path:
            log.warning(
                "Audio path %r does not exist — feedpak created without audio",
                audio_path,
            )

    return buf.getvalue()
