"""Compressed MusicXML (.mxl) unwrapping tests.

Covers the W3C compressed-MXL convention (container.xml + primary
rootfile) and the malformed-archive fallback.
"""

import io
import zipfile

import pytest

import mxml2notation


_SCORE_XML = '''<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="3.1">
  <work><work-title>Compressed Test</work-title></work>
  <part-list>
    <score-part id="P1"><part-name>Piano</part-name></score-part>
  </part-list>
  <part id="P1">
    <measure number="1">
      <attributes>
        <divisions>1</divisions>
        <key><fifths>0</fifths></key>
        <time><beats>4</beats><beat-type>4</beat-type></time>
        <clef number="1"><sign>G</sign><line>2</line></clef>
      </attributes>
      <note><pitch><step>C</step><octave>4</octave></pitch><duration>1</duration><voice>1</voice><type>quarter</type></note>
      <note><pitch><step>D</step><octave>4</octave></pitch><duration>1</duration><voice>1</voice><type>quarter</type></note>
      <note><pitch><step>E</step><octave>4</octave></pitch><duration>1</duration><voice>1</voice><type>quarter</type></note>
      <note><pitch><step>F</step><octave>4</octave></pitch><duration>1</duration><voice>1</voice><type>quarter</type></note>
    </measure>
  </part>
</score-partwise>'''.encode()

_CONTAINER_XML = '''<?xml version="1.0" encoding="UTF-8"?>
<container>
  <rootfiles>
    <rootfile full-path="score.xml"/>
  </rootfiles>
</container>'''.encode()


def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_is_mxl_detects_zip_magic():
    mxl = _zip({'META-INF/container.xml': _CONTAINER_XML, 'score.xml': _SCORE_XML})
    assert mxml2notation._is_mxl(mxl)
    assert not mxml2notation._is_mxl(_SCORE_XML)


def test_extract_mxl_resolves_container_rootfile():
    mxl = _zip({'META-INF/container.xml': _CONTAINER_XML, 'score.xml': _SCORE_XML})
    assert mxml2notation._extract_mxl(mxl) == _SCORE_XML


def test_extract_mxl_ignores_non_primary_renditions():
    container = '''<?xml version="1.0" encoding="UTF-8"?>
<container>
  <rootfiles>
    <rootfile full-path="score.xml"/>
    <rootfile full-path="score.pdf" media-type="application/pdf"/>
  </rootfiles>
</container>'''.encode()
    mxl = _zip({
        'META-INF/container.xml': container,
        'score.xml': _SCORE_XML,
        'score.pdf': b'%PDF-fake',
    })
    assert mxml2notation._extract_mxl(mxl) == _SCORE_XML


def test_extract_mxl_falls_back_without_container():
    # Some malformed exporters ship a bare .xml with no META-INF manifest.
    mxl = _zip({'score.xml': _SCORE_XML})
    assert mxml2notation._extract_mxl(mxl) == _SCORE_XML


def test_extract_mxl_fallback_skips_meta_inf_entries():
    mxl = _zip({
        'META-INF/some_notes.xml': b'<not-a-score/>',
        'score.xml': _SCORE_XML,
    })
    assert mxml2notation._extract_mxl(mxl) == _SCORE_XML


def test_extract_mxl_rejects_path_traversal():
    container = '''<?xml version="1.0" encoding="UTF-8"?>
<container><rootfiles><rootfile full-path="../../etc/passwd"/></rootfiles></container>'''.encode()
    mxl = _zip({'META-INF/container.xml': container, 'score.xml': _SCORE_XML})
    with pytest.raises(ValueError, match='Unsafe rootfile path'):
        mxml2notation._extract_mxl(mxl)


def test_extract_mxl_rejects_missing_rootfile_entry():
    container = '''<?xml version="1.0" encoding="UTF-8"?>
<container><rootfiles><rootfile full-path="missing.xml"/></rootfiles></container>'''.encode()
    mxl = _zip({'META-INF/container.xml': container, 'score.xml': _SCORE_XML})
    with pytest.raises(ValueError, match='missing entry'):
        mxml2notation._extract_mxl(mxl)


def test_extract_mxl_rejects_bad_zip():
    with pytest.raises(ValueError, match='Invalid .mxl archive'):
        mxml2notation._extract_mxl(b'not a zip at all')


def test_extract_mxl_rejects_empty_archive():
    mxl = _zip({})
    with pytest.raises(ValueError, match='no .xml entry'):
        mxml2notation._extract_mxl(mxl)


def test_extract_mxl_resolves_namespaced_container():
    # container.xml is DTD-defined (no XSD, no namespace) and every known
    # real-world sample is namespace-less — but that's not a guarantee, so
    # <rootfile>/<rootfiles> must resolve even if some exporter wraps the
    # document in a default namespace.
    container = '''<?xml version="1.0" encoding="UTF-8"?>
<container xmlns="urn:example:container">
  <rootfiles>
    <rootfile full-path="score.xml"/>
  </rootfiles>
</container>'''.encode()
    mxl = _zip({'META-INF/container.xml': container, 'score.xml': _SCORE_XML})
    assert mxml2notation._extract_mxl(mxl) == _SCORE_XML


def test_extract_mxl_rejects_decompression_bomb(monkeypatch):
    # The upload cap in routes.py only bounds the compressed bytes; a highly
    # compressible rootfile can still decompress far past it. Shrink the cap
    # so the test stays cheap, then feed an entry that blows it.
    monkeypatch.setattr(mxml2notation, '_MXL_MAX_UNCOMPRESSED', 4096)
    bomb = b'<score>' + b' ' * 100_000 + b'</score>'  # ~100 KB > 4 KB cap
    mxl = _zip({'META-INF/container.xml': _CONTAINER_XML, 'score.xml': bomb})
    with pytest.raises(ValueError, match='too large uncompressed'):
        mxml2notation._extract_mxl(mxl)




def test_parse_musicxml_transparently_unwraps_mxl():
    mxl = _zip({'META-INF/container.xml': _CONTAINER_XML, 'score.xml': _SCORE_XML})
    result_mxl = mxml2notation.parse_musicxml(mxl)
    result_plain = mxml2notation.parse_musicxml(_SCORE_XML)
    assert result_mxl['title'] == result_plain['title'] == 'Compressed Test'
    assert result_mxl['notation'] == result_plain['notation']
