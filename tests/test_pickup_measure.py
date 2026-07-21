"""Pickup (anacrusis) measure: downbeat labels must survive beat dedup.

A pickup measure emits a full declared-TS bar of song_timeline beats, so its
spillover -1 beats land exactly on the next measure's grid. The dedup must
let that measure's labeled downbeat win the timestamp collision — before the
fix, the label was shadowed and the measure numbering jumped (0 -> 2).

Constant 120 BPM (default): every quarter note is exactly 0.5 s.
"""

import mxml2notation


def _score(measures_xml: str) -> bytes:
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="3.1">
  <work><work-title>Pickup</work-title></work>
  <part-list>
    <score-part id="P1"><part-name>Piano</part-name></score-part>
  </part-list>
  <part id="P1">
    {measures_xml}
  </part>
</score-partwise>'''.encode()


def _quarters(step, count):
    return ''.join(
        f'<note><pitch><step>{step}</step><octave>4</octave></pitch>'
        f'<duration>1</duration><voice>1</voice><type>quarter</type></note>'
        for _ in range(count)
    )


# Measure 0: 4/4 pickup holding a single quarter (one beat). Measures 1-2 full.
_PICKUP = f'''<measure number="0" implicit="yes">
  <attributes>
    <divisions>1</divisions>
    <key><fifths>0</fifths></key>
    <time><beats>4</beats><beat-type>4</beat-type></time>
    <clef number="1"><sign>G</sign><line>2</line></clef>
  </attributes>
  {_quarters('C', 1)}
</measure>'''

_M1 = f'<measure number="1">{_quarters("D", 4)}</measure>'
_M2 = f'<measure number="2">{_quarters("E", 4)}</measure>'


def _downbeat_time(timeline, measure_number):
    times = [b['time'] for b in timeline['beats'] if b['measure'] == measure_number]
    assert times, f"no downbeat found for measure {measure_number}"
    return min(times)


def test_downbeat_labels_survive_pickup_spillover():
    tl = mxml2notation.parse_musicxml(_score(_PICKUP + _M1 + _M2))['song_timeline']
    # Pickup is one beat: measure 1's downbeat at 0.5 s, measure 2's at 2.5 s.
    assert _downbeat_time(tl, 0) == 0.0
    assert abs(_downbeat_time(tl, 1) - 0.5) < 1e-6, \
        f"measure 1 downbeat should be 0.5 s, got {_downbeat_time(tl, 1)}"
    assert abs(_downbeat_time(tl, 2) - 2.5) < 1e-6, \
        f"measure 2 downbeat should be 2.5 s, got {_downbeat_time(tl, 2)}"


def test_no_duplicate_beat_timestamps():
    tl = mxml2notation.parse_musicxml(_score(_PICKUP + _M1 + _M2))['song_timeline']
    times = [b['time'] for b in tl['beats']]
    assert len(times) == len(set(times))
