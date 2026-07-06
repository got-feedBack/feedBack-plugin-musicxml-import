"""Feedpak-spec conformance tests for the pack builder and the
song_timeline / tuplet additions (feedpak spec §4.1, §5, §7.4, §7.6).
"""

import io
import json
import zipfile

import yaml

import mxml2notation


def _score(measures_xml: str, part_name: str = 'Piano') -> bytes:
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="3.1">
  <work><work-title>Test Piece</work-title></work>
  <part-list>
    <score-part id="P1"><part-name>{part_name}</part-name></score-part>
  </part-list>
  <part id="P1">
    {measures_xml}
  </part>
</score-partwise>'''.encode()


_ATTRS = '''<attributes>
  <divisions>2</divisions>
  <key><fifths>0</fifths></key>
  <time><beats>4</beats><beat-type>4</beat-type></time>
  <clef number="1"><sign>G</sign><line>2</line></clef>
</attributes>'''


def _note(step='C', octave='4', extra='', dur='2', ntype='quarter'):
    return f'''<note>
      <pitch><step>{step}</step><octave>{octave}</octave></pitch>
      <duration>{dur}</duration><voice>1</voice><type>{ntype}</type>
      {extra}
    </note>'''


def _simple_measure(number=1, attrs=_ATTRS):
    return f'''<measure number="{number}">
      {attrs}
      {_note('C')}{_note('D')}{_note('E')}{_note('F')}
    </measure>'''


def _manifest_of(pak_bytes: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(pak_bytes)) as zf:
        return yaml.safe_load(zf.read('manifest.yaml'))


# ── Manifest conformance (spec §4.1 / §5) ───────────────────────────────────

def test_manifest_stamps_feedpak_version_and_required_keys():
    result = mxml2notation.parse_musicxml(_score(_simple_measure()))
    pak = mxml2notation.build_feedpak_zip(result, None, 'Test Piece', 'Composer')
    manifest = _manifest_of(pak)

    assert manifest['feedpak_version'] == mxml2notation._FEEDPAK_VERSION
    assert manifest['title'] == 'Test Piece'
    assert manifest['artist'] == 'Composer'
    assert isinstance(manifest['duration'], (int, float))
    assert manifest['arrangements'], 'arrangements MUST be non-empty'


def test_manifest_omits_empty_album_year_placeholders():
    result = mxml2notation.parse_musicxml(_score(_simple_measure()))
    manifest = _manifest_of(
        mxml2notation.build_feedpak_zip(result, None, 'T', 'C'))
    assert 'album' not in manifest
    assert 'year' not in manifest


def test_arrangement_is_notation_only_with_type_hint():
    result = mxml2notation.parse_musicxml(_score(_simple_measure()))
    manifest = _manifest_of(
        mxml2notation.build_feedpak_zip(result, None, 'T', 'C'))
    arr = manifest['arrangements'][0]
    assert arr['id'] == 'piano'
    assert arr['notation'] == 'notation_piano.json'
    assert 'file' not in arr
    assert arr['type'] == 'piano'


def test_arrangement_type_omitted_when_instrument_unknown():
    result = mxml2notation.parse_musicxml(
        _score(_simple_measure(), part_name='Zzyzx'))
    manifest = _manifest_of(
        mxml2notation.build_feedpak_zip(result, None, 'T', 'C'))
    arr = manifest['arrangements'][0]
    assert 'type' not in arr
    assert arr['notation'] == 'notation_unknown.json'


def test_stems_key_omitted_without_audio():
    # A stem-less pack is the §5.3.2 local-authoring carve-out: the stems
    # key must be absent, not an empty list pointing at nothing.
    result = mxml2notation.parse_musicxml(_score(_simple_measure()))
    manifest = _manifest_of(
        mxml2notation.build_feedpak_zip(result, None, 'T', 'C'))
    assert 'stems' not in manifest


def test_stems_entry_present_with_audio(tmp_path):
    ogg = tmp_path / 'audio.ogg'
    ogg.write_bytes(b'OggS fake')
    result = mxml2notation.parse_musicxml(_score(_simple_measure()))
    pak = mxml2notation.build_feedpak_zip(result, str(ogg), 'T', 'C')
    manifest = _manifest_of(pak)
    assert manifest['stems'] == [
        {'id': 'full', 'file': 'stems/full.ogg', 'default': True}
    ]
    with zipfile.ZipFile(io.BytesIO(pak)) as zf:
        assert 'stems/full.ogg' in zf.namelist()


def test_zip_contains_notation_and_timeline_referenced_by_manifest():
    result = mxml2notation.parse_musicxml(_score(_simple_measure()))
    pak = mxml2notation.build_feedpak_zip(result, None, 'T', 'C')
    manifest = _manifest_of(pak)
    with zipfile.ZipFile(io.BytesIO(pak)) as zf:
        names = zf.namelist()
        assert manifest['arrangements'][0]['notation'] in names
        assert manifest['song_timeline'] in names
        notation = json.loads(zf.read(manifest['arrangements'][0]['notation']))
        timeline = json.loads(zf.read(manifest['song_timeline']))
    assert notation['version'] == 1
    assert timeline['version'] == 1


# ── song_timeline tempo / time-signature maps (spec §7.4) ───────────────────

def test_timeline_carries_initial_tempo_and_ts():
    result = mxml2notation.parse_musicxml(_score(_simple_measure()))
    tl = result['song_timeline']
    assert tl['tempos'][0] == {'time': 0.0, 'bpm': 120.0}
    assert tl['time_signatures'][0] == {'time': 0.0, 'ts': [4, 4]}


def test_timeline_tempo_change_and_score_tempo_wins_at_zero():
    xml = _score(f'''<measure number="1">
      {_ATTRS}
      <direction><sound tempo="100"/></direction>
      {_note('C')}{_note('D')}{_note('E')}{_note('F')}
    </measure>
    <measure number="2">
      <direction><sound tempo="60"/></direction>
      {_note('C')}{_note('D')}{_note('E')}{_note('F')}
    </measure>''')
    tl = mxml2notation.parse_musicxml(xml)['song_timeline']
    # The builder's 120 BPM seed at t=0 collapses into the score's own
    # initial tempo (same-time events: last wins).
    assert tl['tempos'][0] == {'time': 0.0, 'bpm': 100.0}
    # Measure 1 lasts 4 quarters at 100 BPM = 2.4 s.
    assert tl['tempos'][1] == {'time': 2.4, 'bpm': 60.0}


def test_timeline_time_signature_change():
    xml = _score(f'''{_simple_measure(1)}
    <measure number="2">
      <attributes><time><beats>3</beats><beat-type>4</beat-type></time></attributes>
      {_note('C')}{_note('D')}{_note('E')}
    </measure>''')
    tl = mxml2notation.parse_musicxml(xml)['song_timeline']
    assert tl['time_signatures'] == [
        {'time': 0.0, 'ts': [4, 4]},
        {'time': 2.0, 'ts': [3, 4]},
    ]


def test_timeline_consecutive_equal_bpm_deduped():
    xml = _score(f'''<measure number="1">
      {_ATTRS}
      <direction><sound tempo="120"/></direction>
      {_note('C')}{_note('D')}{_note('E')}{_note('F')}
    </measure>''')
    tl = mxml2notation.parse_musicxml(xml)['song_timeline']
    assert tl['tempos'] == [{'time': 0.0, 'bpm': 120.0}]


# ── Tuplets (spec §7.6 `tu`) ────────────────────────────────────────────────

_TM_TRIPLET = ('<time-modification><actual-notes>3</actual-notes>'
               '<normal-notes>2</normal-notes></time-modification>')


def test_triplet_emits_tu_on_note_beats():
    # Three eighth-note triplets in the time of one quarter (divisions=6
    # for exact thirds), then a plain quarter with no tu.
    xml = _score(f'''<measure number="1">
      <attributes>
        <divisions>6</divisions>
        <time><beats>4</beats><beat-type>4</beat-type></time>
        <clef number="1"><sign>G</sign><line>2</line></clef>
      </attributes>
      {_note('C', '5', _TM_TRIPLET, dur='2', ntype='eighth')}
      {_note('D', '5', _TM_TRIPLET, dur='2', ntype='eighth')}
      {_note('E', '5', _TM_TRIPLET, dur='2', ntype='eighth')}
      {_note('F', '5', dur='6', ntype='quarter')}
    </measure>''')
    beats = (mxml2notation.parse_musicxml(xml)['notation']['measures'][0]
             ['staves']['rh']['voices'][0]['beats'])
    assert [b.get('tu') for b in beats] == [[3, 2], [3, 2], [3, 2], None]


def test_tuplet_rest_carries_tu():
    xml = _score(f'''<measure number="1">
      <attributes>
        <divisions>6</divisions>
        <time><beats>4</beats><beat-type>4</beat-type></time>
        <clef number="1"><sign>G</sign><line>2</line></clef>
      </attributes>
      {_note('C', '5', _TM_TRIPLET, dur='2', ntype='eighth')}
      <note>
        <rest/>
        <duration>2</duration><voice>1</voice><type>eighth</type>
        {_TM_TRIPLET}
      </note>
      {_note('E', '5', _TM_TRIPLET, dur='2', ntype='eighth')}
      {_note('F', '5', dur='6', ntype='quarter')}
    </measure>''')
    beats = (mxml2notation.parse_musicxml(xml)['notation']['measures'][0]
             ['staves']['rh']['voices'][0]['beats'])
    rest = next(b for b in beats if b.get('rest'))
    assert rest['tu'] == [3, 2]


def test_degenerate_time_modification_omitted():
    # actual == normal is not a tuplet; zero values are garbage — omit tu.
    tm_noop = ('<time-modification><actual-notes>2</actual-notes>'
               '<normal-notes>2</normal-notes></time-modification>')
    xml = _score(f'''<measure number="1">
      {_ATTRS}
      {_note('C', '5', tm_noop)}
      {_note('D')}{_note('E')}{_note('F')}
    </measure>''')
    beats = (mxml2notation.parse_musicxml(xml)['notation']['measures'][0]
             ['staves']['rh']['voices'][0]['beats'])
    assert all('tu' not in b for b in beats)
