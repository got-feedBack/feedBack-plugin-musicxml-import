"""The parse-arrangement surface: flat_notes (tie folding, grace skipping,
staff provenance), editor_arrangement (packed pitch encoding, per-note hand,
keys-recognizable naming, notation passthrough), and the stateless endpoint.

The per-note hand vocabulary is 'rh'/'lh' (matching the notation staff ids);
staves beyond the grand staff (e.g. an organ pedal staff) stay unassigned —
the editor's heuristic keeps owning those.
"""

import base64
import json
import logging

import mxml2notation


def _score(measures_xml: str, part_name: str = 'Piano',
           staves: int = 2) -> bytes:
    staves_attr = (f'<staves>{staves}</staves>' if staves > 1 else '')
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="3.1">
  <work><work-title>Hand Test</work-title></work>
  <part-list>
    <score-part id="P1"><part-name>{part_name}</part-name></score-part>
  </part-list>
  <part id="P1">
    <measure number="1">
      <attributes>
        <divisions>2</divisions>
        <key><fifths>0</fifths></key>
        <time><beats>4</beats><beat-type>4</beat-type></time>
        {staves_attr}
        <clef number="1"><sign>G</sign><line>2</line></clef>
        {'<clef number="2"><sign>F</sign><line>4</line></clef>' if staves > 1 else ''}
      </attributes>
      {measures_xml}
    </measure>
  </part>
</score-partwise>'''.encode()


def _note(step='C', octave='4', staff='1', dur='2', ntype='quarter',
          voice='1', extra=''):
    return f'''<note>
      <pitch><step>{step}</step><octave>{octave}</octave></pitch>
      <duration>{dur}</duration>
      <voice>{voice}</voice>
      <type>{ntype}</type>
      <staff>{staff}</staff>
      {extra}
    </note>'''


# ── flat_notes semantics ────────────────────────────────────────────────────

def test_flat_notes_carry_staff_provenance():
    # RH C4 then (after a backup) LH C3 at the same onset.
    xml = _score(
        _note('C', '4', staff='1')
        + '<backup><duration>2</duration></backup>'
        + _note('C', '3', staff='2', voice='2')
    )
    result = mxml2notation.parse_musicxml(xml)
    flat = result['flat_notes']
    assert [(n['midi'], n['staff']) for n in flat] == [(48, 'lh'), (60, 'rh')]
    # Both at the same onset (sorted by (t, midi) — LH's lower pitch first).
    assert flat[0]['t'] == flat[1]['t'] == 0.0


def test_flat_notes_fold_ties_into_one_note():
    # Quarter C4 tied to quarter C4: ONE flat note, sustain = both.
    xml = _score(
        _note('C', '4', extra='<tie type="start"/>')
        + _note('C', '4', extra='<tie type="stop"/>')
    )
    result = mxml2notation.parse_musicxml(xml)
    flat = result['flat_notes']
    assert len(flat) == 1
    # 120 BPM default: quarter = 0.5 s, tied pair = 1.0 s.
    assert abs(flat[0]['sus'] - 1.0) < 1e-6
    # The MIDI pass still re-strikes (unchanged upstream behavior).
    assert result['midi_bytes']


def test_flat_notes_tie_chain_keeps_extending():
    # start -> stop+start -> stop across three quarters = one 1.5 s note.
    xml = _score(
        _note('C', '4', extra='<tie type="start"/>')
        + _note('C', '4', extra='<tie type="stop"/><tie type="start"/>')
        + _note('C', '4', extra='<tie type="stop"/>')
    )
    flat = mxml2notation.parse_musicxml(xml)['flat_notes']
    assert len(flat) == 1
    assert abs(flat[0]['sus'] - 1.5) < 1e-6


def test_flat_notes_skip_grace_notes():
    xml = _score(
        '''<note>
          <grace slash="yes"/>
          <pitch><step>B</step><octave>3</octave></pitch>
          <voice>1</voice>
          <type>eighth</type>
          <staff>1</staff>
        </note>'''
        + _note('C', '4')
    )
    flat = mxml2notation.parse_musicxml(xml)['flat_notes']
    assert [n['midi'] for n in flat] == [60]


# ── editor_arrangement ──────────────────────────────────────────────────────

def _arrangement(xml: bytes) -> dict:
    return mxml2notation.editor_arrangement(mxml2notation.parse_musicxml(xml))


def test_editor_notes_pack_pitch_and_carry_hand():
    xml = _score(
        _note('C', '4', staff='1')
        + '<backup><duration>2</duration></backup>'
        + _note('C', '3', staff='2', voice='2')
    )
    arr = _arrangement(xml)
    notes = arr['notes']
    assert len(notes) == 2
    lh, rh = notes  # sorted by (t, midi)
    # Packed pitch encoding: midi = string*24 + fret.
    assert lh['string'] * 24 + lh['fret'] == 48
    assert rh['string'] * 24 + rh['fret'] == 60
    assert lh['techniques'] == {'hand': 'lh'}
    assert rh['techniques'] == {'hand': 'rh'}
    assert all('time' in n and 'sustain' in n for n in notes)


def test_extra_staves_stay_unassigned():
    # Staff 3 (e.g. organ pedals) is not a grand-staff hand: no hand key.
    xml = _score(
        _note('C', '2', staff='3'), staves=3
    )
    arr = _arrangement(xml)
    assert arr['notes'][0]['techniques'] == {}


def test_notation_payload_rides_along_verbatim():
    xml = _score(_note('C', '4'))
    result = mxml2notation.parse_musicxml(xml)
    arr = mxml2notation.editor_arrangement(result)
    assert arr['notation'] is result['notation']
    assert arr['notation']['measures']


def test_arrangement_shape_matches_editor_add_keys_contract():
    arr = _arrangement(_score(_note('C', '4')))
    assert arr['tuning'] == [0, 0, 0, 0, 0, 0]
    assert arr['capo'] == 0
    assert arr['chords'] == []
    assert arr['chord_templates'] == []


def test_name_keeps_keys_recognizable_part_name():
    # "Piano" already satisfies the editor's prefix-anchored keys router.
    arr = _arrangement(_score(_note('C', '4'), part_name='Piano'))
    assert arr['name'] == 'Piano'


def test_name_prefixes_unrecognizable_part_name():
    # "Electric Piano" fails the prefix rule (start-anchored) — prefix it.
    arr = _arrangement(_score(_note('C', '4'), part_name='Electric Piano'))
    assert arr['name'] == 'Keys — Electric Piano'


def test_name_falls_back_without_part_name():
    arr = _arrangement(_score(_note('C', '4'), part_name=''))
    assert arr['name'] == 'Keys'


# ── the endpoint (stateless) ────────────────────────────────────────────────

def _client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import routes

    app = FastAPI()
    context = {
        'get_dlc_dir': lambda: None,
        'extract_meta': lambda p: {},
        'meta_db': None,
        'config_dir': None,
        'log': logging.getLogger('test'),
        'load_sibling': lambda name: mxml2notation,
    }
    routes.setup(app, context)
    return TestClient(app)


def _post(client, filename, payload: bytes):
    return client.post(
        '/api/plugins/musicxml_import/parse-arrangement',
        json={'filename': filename,
              'data': base64.b64encode(payload).decode()},
    )


def test_endpoint_returns_ready_arrangement():
    client = _client()
    resp = _post(client, 'score.musicxml', _score(_note('C', '4')))
    assert resp.status_code == 200
    data = resp.json()
    assert 'error' not in data
    arr = data['arrangement']
    assert arr['name'] == 'Piano'
    assert arr['notes'][0]['techniques'] == {'hand': 'rh'}
    assert arr['notation']['measures']
    # JSON-serializable end to end (no bytes leaked into the payload).
    json.dumps(data)


def test_endpoint_rejects_unsupported_extension():
    client = _client()
    resp = _post(client, 'score.gp5', b'not xml')
    assert 'error' in resp.json()


def test_endpoint_rejects_bad_base64():
    client = _client()
    resp = client.post(
        '/api/plugins/musicxml_import/parse-arrangement',
        json={'filename': 'score.xml', 'data': 'aGVs!bG8='},
    )
    assert resp.json() == {'error': 'Invalid base64 data'}


def test_endpoint_reports_parse_failure_as_error():
    client = _client()
    resp = _post(client, 'score.xml', b'<not-a-score/>')
    assert 'error' in resp.json()
