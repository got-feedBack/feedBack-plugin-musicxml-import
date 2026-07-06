"""Timing conversion under a mid-score <divisions> change (issue #5).

`<divisions>` (divisions-per-quarter) may change at a measure boundary — this
is legal MusicXML (only *mid-measure* attribute changes are dropped). The
absolute-division position `abs_div` accumulates across measures, each counted
in its own measure's divisions, so converting an abs_div that spans a
divisions change requires per-span divisions, not a single scalar.

These scores use a constant 120 BPM (default), so every quarter note is
exactly 0.5 s and the expected wall-clock time of each beat is trivially
known regardless of how many divisions encode it.
"""

import mxml2notation


def _score(measures_xml: str) -> bytes:
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="3.1">
  <work><work-title>Divisions Change</work-title></work>
  <part-list>
    <score-part id="P1"><part-name>Piano</part-name></score-part>
  </part-list>
  <part id="P1">
    {measures_xml}
  </part>
</score-partwise>'''.encode()


def _quarters(step, count, dur):
    return ''.join(
        f'<note><pitch><step>{step}</step><octave>4</octave></pitch>'
        f'<duration>{dur}</duration><voice>1</voice><type>quarter</type></note>'
        for _ in range(count)
    )


# Measure 1 at divisions=1 (quarter = 1 div); measure 2 changes to divisions=4
# (quarter = 4 divs). Both are 4/4 at 120 BPM, so measure 2's downbeat is at
# exactly 2.0 s (four quarters of 0.5 s elapsed in measure 1).
_M1 = f'''<measure number="1">
  <attributes>
    <divisions>1</divisions>
    <key><fifths>0</fifths></key>
    <time><beats>4</beats><beat-type>4</beat-type></time>
    <clef number="1"><sign>G</sign><line>2</line></clef>
  </attributes>
  {_quarters('C', 4, 1)}
</measure>'''

_M2 = f'''<measure number="2">
  <attributes><divisions>4</divisions></attributes>
  {_quarters('D', 4, 4)}
</measure>'''

_M3 = f'''<measure number="3">
  {_quarters('E', 4, 4)}
</measure>'''


def _downbeat_time(timeline, measure_number):
    # song_timeline beats carry the real measure number only on downbeats
    # (inner beats use -1), so a beat tagged `measure_number` is that bar's
    # downbeat.
    times = [b['time'] for b in timeline['beats'] if b['measure'] == measure_number]
    assert times, f"no downbeat found for measure {measure_number}"
    return min(times)


def test_measure_downbeats_correct_across_divisions_change():
    result = mxml2notation.parse_musicxml(_score(_M1 + _M2 + _M3))
    tl = result['song_timeline']
    # M1 downbeat at 0.0, M2 at 2.0 (4 quarters * 0.5s), M3 at 4.0.
    assert _downbeat_time(tl, 1) == 0.0
    assert abs(_downbeat_time(tl, 2) - 2.0) < 1e-6, \
        f"measure 2 downbeat should be 2.0 s, got {_downbeat_time(tl, 2)}"
    assert abs(_downbeat_time(tl, 3) - 4.0) < 1e-6, \
        f"measure 3 downbeat should be 4.0 s, got {_downbeat_time(tl, 3)}"


def test_tempo_map_time_correct_after_divisions_change():
    # A tempo change declared in measure 2 (after the divisions change) must be
    # stamped at the bar's true onset (2.0 s), not a divisions-mismatched value.
    m2_with_tempo = f'''<measure number="2">
      <attributes><divisions>4</divisions></attributes>
      <direction><sound tempo="90"/></direction>
      {_quarters('D', 4, 4)}
    </measure>'''
    result = mxml2notation.parse_musicxml(_score(_M1 + m2_with_tempo))
    tempos = result['song_timeline']['tempos']
    # The 90 BPM event sits at the measure-2 downbeat = 2.0 s.
    at_90 = [t for t in tempos if abs(t['bpm'] - 90.0) < 1e-6]
    assert at_90, f"no 90 BPM tempo event emitted; tempos={tempos}"
    assert abs(at_90[0]['time'] - 2.0) < 1e-6, \
        f"90 BPM event should be at 2.0 s, got {at_90[0]['time']}"


def test_constant_divisions_unaffected():
    # Regression guard: with divisions constant, times are unchanged.
    m1 = _M1
    m2 = f'''<measure number="2">
      {_quarters('D', 4, 1)}
    </measure>'''
    result = mxml2notation.parse_musicxml(_score(m1 + m2))
    tl = result['song_timeline']
    assert _downbeat_time(tl, 1) == 0.0
    assert abs(_downbeat_time(tl, 2) - 2.0) < 1e-6
