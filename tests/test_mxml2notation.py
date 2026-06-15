"""Schema-alignment tests for mxml2notation (sloppak-spec §5.3, slopsmith#838).

Covers the frozen notation schema v1 fields added during curation:
typed grace strings, sustain pedal (spd/sph/spu), credits
(rights/lyricist/arranger), measure pickup, beat arp/ferm, note stem,
and the drop-with-warning policy for v1 non-features.
"""

import logging

import pytest

import mxml2notation


def _score(measures_xml: str, identification: str = '') -> bytes:
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="3.1">
  <work><work-title>Test Piece</work-title></work>
  {identification}
  <part-list>
    <score-part id="P1"><part-name>Piano</part-name></score-part>
  </part-list>
  <part id="P1">
    {measures_xml}
  </part>
</score-partwise>'''.encode()


_ATTRS = '''<attributes>
  <divisions>1</divisions>
  <key><fifths>0</fifths></key>
  <time><beats>4</beats><beat-type>4</beat-type></time>
  <clef number="1"><sign>G</sign><line>2</line></clef>
</attributes>'''


def _note(step='C', octave='4', extra='', dur='1', ntype='quarter'):
    return f'''<note>
      <pitch><step>{step}</step><octave>{octave}</octave></pitch>
      <duration>{dur}</duration><voice>1</voice><type>{ntype}</type>
      {extra}
    </note>'''


def _beats_of(result, measure_index=0, staff='rh'):
    measure = result['notation']['measures'][measure_index]
    return measure['staves'][staff]['voices'][0]['beats']


# ── Typed grace strings ─────────────────────────────────────────────────────

def test_grace_typed_string_replaces_boolean_pair():
    xml = _score(f'''<measure number="1">
      {_ATTRS}
      <note>
        <grace slash="yes"/>
        <pitch><step>D</step><octave>5</octave></pitch>
        <voice>1</voice><type>eighth</type>
      </note>
      {_note('C', '5')}
      <note>
        <grace/>
        <pitch><step>E</step><octave>5</octave></pitch>
        <voice>1</voice><type>eighth</type>
      </note>
      {_note('D', '5')}
      {_note('E', '5')}
      {_note('F', '5')}
    </measure>''')
    beats = _beats_of(mxml2notation.parse_musicxml(xml))

    graces = [b for b in beats if 'grace' in b]
    assert [b['grace'] for b in graces] == ['a', 'p']
    # The old boolean pair must be gone everywhere.
    assert all('grace_slash' not in b for b in beats)
    assert all(not isinstance(b.get('grace'), bool) for b in beats)


# ── Sustain pedal ───────────────────────────────────────────────────────────

def test_pedal_start_hold_change_stop():
    # Pedal directions follow their target note (post-annotation style,
    # matching the dynamics attribution already used by the importer).
    xml = _score(f'''<measure number="1">
      {_ATTRS}
      {_note('C')}
      <direction><direction-type><pedal type="start"/></direction-type></direction>
      {_note('D')}
      {_note('E')}
      <direction><direction-type><pedal type="change"/></direction-type></direction>
      {_note('F')}
    </measure>
    <measure number="2">
      {_note('G')}
      <direction><direction-type><pedal type="stop"/></direction-type></direction>
      {_note('A')}
      {_note('B')}
      <note><rest/><duration>1</duration><voice>1</voice><type>quarter</type></note>
    </measure>''')
    result = mxml2notation.parse_musicxml(xml)

    m1 = _beats_of(result, 0)
    assert m1[0].get('spd') is True and 'sph' not in m1[0] and 'spu' not in m1[0]
    assert m1[1].get('sph') is True
    # change = re-pedal: spu + spd on the same beat
    assert m1[2].get('spu') is True and m1[2].get('spd') is True
    assert m1[3].get('sph') is True

    m2 = _beats_of(result, 1)
    # span crosses the barline
    assert m2[0].get('spu') is True
    assert all(k not in m2[1] for k in ('spd', 'sph', 'spu'))
    # no stray flags after release, including on the rest beat
    assert all(k not in m2[3] for k in ('spd', 'sph', 'spu'))


def test_no_legacy_ped_field():
    xml = _score(f'''<measure number="1">
      {_ATTRS}
      {_note('C')}
      <direction><direction-type><pedal type="start"/></direction-type></direction>
      {_note('D')}
    </measure>''')
    beats = _beats_of(mxml2notation.parse_musicxml(xml))
    assert all('ped' not in b for b in beats)


# ── Credits ─────────────────────────────────────────────────────────────────

def test_credits_rights_lyricist_arranger():
    ident = '''<identification>
      <creator type="composer">A. Composer</creator>
      <creator type="lyricist">L. Lyricist</creator>
      <creator type="arranger">B. Arranger</creator>
      <rights>© 2026 Test Rights</rights>
    </identification>'''
    xml = _score(f'<measure number="1">{_ATTRS}{_note("C")}</measure>',
                 identification=ident)
    result = mxml2notation.parse_musicxml(xml)

    notation = result['notation']
    assert notation['rights'] == '© 2026 Test Rights'
    assert notation['lyricist'] == 'L. Lyricist'
    assert notation['arranger'] == 'B. Arranger'
    # composer fallback from <identification> when credit-words are absent
    assert result['composer'] == 'A. Composer'


def test_credits_omitted_when_absent():
    xml = _score(f'<measure number="1">{_ATTRS}{_note("C")}</measure>')
    notation = mxml2notation.parse_musicxml(xml)['notation']
    assert 'rights' not in notation
    assert 'lyricist' not in notation
    assert 'arranger' not in notation


# ── Pickup measure ──────────────────────────────────────────────────────────

def test_pickup_measure_flag():
    xml = _score(f'''<measure number="0" implicit="yes">
      {_ATTRS}
      {_note('G')}
    </measure>
    <measure number="1">
      {_note('C')}{_note('D')}{_note('E')}{_note('F')}
    </measure>''')
    measures = mxml2notation.parse_musicxml(xml)['notation']['measures']
    assert measures[0].get('pickup') is True
    assert 'pickup' not in measures[1]


# ── arp / ferm / stem ───────────────────────────────────────────────────────

def test_arp_ferm_stem():
    xml = _score(f'''<measure number="1">
      {_ATTRS}
      {_note('C', extra='<stem>up</stem><notations><arpeggiate/></notations>')}
      <note>
        <chord/>
        <pitch><step>E</step><octave>4</octave></pitch>
        <duration>1</duration><voice>1</voice><type>quarter</type>
      </note>
      {_note('D', extra='<notations><fermata/></notations>')}
      {_note('E', extra='<stem>down</stem>')}
      {_note('F', extra='<stem>none</stem>')}
    </measure>''')
    beats = _beats_of(mxml2notation.parse_musicxml(xml))

    assert beats[0].get('arp') is True
    assert beats[0]['notes'][0]['stem'] == 'up'
    # fermata is the typed ferm flag, not a txt annotation
    assert beats[1].get('ferm') is True
    assert beats[1].get('txt') != 'fermata'
    assert beats[2]['notes'][0]['stem'] == 'down'
    # 'none' is not in the v1 stem vocabulary → omitted
    assert 'stem' not in beats[3]['notes'][0]


# ── v1 non-features: dropped with a warning, never approximated ─────────────

def test_tremolo_dropped_with_warning(caplog):
    xml = _score(f'''<measure number="1">
      {_ATTRS}
      {_note('C', extra='<notations><ornaments><tremolo>3</tremolo></ornaments></notations>')}
    </measure>''')
    with caplog.at_level(logging.WARNING):
        result = mxml2notation.parse_musicxml(xml)
    assert any('tremolo' in r.message for r in caplog.records)
    assert all('trem' not in b for b in _beats_of(result))


def test_ottava_dropped_with_warning(caplog):
    xml = _score(f'''<measure number="1">
      {_ATTRS}
      <direction><direction-type><octave-shift type="down" size="8"/></direction-type></direction>
      {_note('C')}
    </measure>''')
    with caplog.at_level(logging.WARNING):
        mxml2notation.parse_musicxml(xml)
    assert any('ottava' in r.message for r in caplog.records)


def test_glissando_dropped_with_warning(caplog):
    xml = _score(f'''<measure number="1">
      {_ATTRS}
      {_note('C', extra='<notations><glissando type="start"/></notations>')}
      {_note('G')}
    </measure>''')
    with caplog.at_level(logging.WARNING):
        mxml2notation.parse_musicxml(xml)
    assert any('glissando' in r.message for r in caplog.records)


def test_mid_measure_clef_change_dropped_with_warning(caplog):
    xml = _score(f'''<measure number="1">
      {_ATTRS}
      {_note('C')}{_note('D')}
      <attributes><clef number="1"><sign>F</sign><line>4</line></clef></attributes>
      {_note('E', '3')}{_note('F', '3')}
    </measure>''')
    with caplog.at_level(logging.WARNING):
        result = mxml2notation.parse_musicxml(xml)
    assert any('mid-measure clef change' in r.message for r in caplog.records)
    # the change is dropped — the measure keeps its opening treble clef
    measure = result['notation']['measures'][0]
    assert measure['staves']['rh'].get('clef', 'G2') == 'G2'
    assert result['notation']['staves'][0]['clef'] == 'G2'


def test_measure_boundary_clef_change_still_applies():
    xml = _score(f'''<measure number="1">
      {_ATTRS}
      {_note('C')}
    </measure>
    <measure number="2">
      <attributes><clef number="1"><sign>F</sign><line>4</line></clef></attributes>
      {_note('E', '3')}
    </measure>''')
    measures = mxml2notation.parse_musicxml(xml)['notation']['measures']
    assert measures[1]['staves']['rh']['clef'] == 'F4'


def test_pedal_staff_picks_bottom_staff_not_middle():
    """_pedal_staff returns the actual bottom staff. For a 3+ staff (organ)
    part the highest-numbered staff_N wins over the middle 'lh'; a normal
    grand staff still resolves to 'lh', and a single staff to 'rh'."""
    _pedal_staff = mxml2notation._pedal_staff
    # 3-staff organ: rh (top) / lh (middle) / staff_3 (bottom pedalboard).
    assert _pedal_staff({"rh": "G2", "lh": "F4", "staff_3": "F4"}) == "staff_3"
    # Highest staff_N wins among several.
    assert _pedal_staff({"rh": "G2", "staff_3": "F4", "staff_4": "F4"}) == "staff_4"
    # Ordinary grand staff: bottom is lh.
    assert _pedal_staff({"rh": "G2", "lh": "F4"}) == "lh"
    # Single staff.
    assert _pedal_staff({"rh": "G2"}) == "rh"
