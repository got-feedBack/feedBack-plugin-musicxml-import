"""MusicXML Import plugin — backend routes.

Endpoints:
  POST /api/plugins/musicxml_import/upload
    Receives a MusicXML file as base64. Parses it and returns metadata
    for the UI to display (title, composer, measure count, duration).
    Saves the raw bytes to a temp file for the build step.

  WS   /ws/plugins/musicxml_import/build
    Builds a .feedpak from the uploaded MusicXML file, streaming progress
    messages. Produces notation_<instrument>.json + song_timeline.json in
    the zip.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import secrets
import shutil
import tempfile
import time
from pathlib import Path

from fastapi import WebSocket, WebSocketDisconnect

try:
    from gp2midi import render_midi_to_audio as _render_midi_to_audio
except ImportError:
    _render_midi_to_audio = None

_get_dlc_dir = None
_extract_meta = None
_meta_db = None
_config_dir = None
_log = None
_mxml = None

# Decoded upload size cap. MusicXML is text; real-world scores are well
# under this. Checked cheaply against the base64 payload length.
_MAX_UPLOAD_BYTES = 20 * 1024 * 1024

# Server-side upload registry: opaque token -> (xml temp path, created-at).
# The build WS receives only the token, never a filesystem path — the
# client must not be able to point the build step (which reads the file
# and deletes its parent directory) at an arbitrary path.
_UPLOAD_TTL_SECONDS = 3600
_uploads: dict[str, tuple[Path, float]] = {}


def _purge_stale_uploads() -> None:
    now = time.monotonic()
    for token in [t for t, (_, ts) in _uploads.items()
                  if now - ts > _UPLOAD_TTL_SECONDS]:
        path, _ = _uploads.pop(token)
        shutil.rmtree(path.parent, ignore_errors=True)


def setup(app, context):
    global _get_dlc_dir, _extract_meta, _meta_db, _config_dir, _log, _mxml
    _get_dlc_dir = context['get_dlc_dir']
    _extract_meta = context['extract_meta']
    _meta_db = context['meta_db']
    _config_dir = context['config_dir']
    _log = context['log']
    _mxml = context['load_sibling']('mxml2notation')

    @app.post('/api/plugins/musicxml_import/upload')
    async def upload_mxml(data: dict):
        """Receive a MusicXML file as base64, parse metadata, return summary."""
        filename = data.get('filename', '')
        b64 = data.get('data', '')
        if not filename or not b64:
            return {'error': 'No file data'}

        ext = Path(filename).suffix.lower()
        if ext not in ('.xml', '.musicxml'):
            return {'error': f'Unsupported format ({ext}). Only .xml / .musicxml files are supported.'}

        # Cheap size bound on the encoded payload (base64 ≈ 4/3 overhead).
        if len(b64) > _MAX_UPLOAD_BYTES * 4 // 3 + 4:
            return {'error': 'File too large (max 20 MB)'}

        try:
            xml_bytes = base64.b64decode(b64)
        except Exception:
            return {'error': 'Invalid base64 data'}

        _purge_stale_uploads()

        # The on-disk name is sanitised (no separators, no traversal) and
        # only ever used inside a fresh private temp dir; the client gets
        # back an opaque token, not the path.
        safe_filename = re.sub(r'[^a-zA-Z0-9._-]', '_', filename) or 'score.xml'
        tmp_dir = Path(tempfile.mkdtemp(prefix='musicxml_import_'))
        tmp_path = tmp_dir / safe_filename
        tmp_path.write_bytes(xml_bytes)

        try:
            result = _mxml.parse_musicxml(xml_bytes)
            token = secrets.token_hex(16)
            _uploads[token] = (tmp_path, time.monotonic())
            return {
                'title': result['title'],
                'composer': result['composer'],
                'duration': result['duration'],
                'part_names': result['part_names'],
                'measure_count': result['measure_count'],
                'upload_id': token,
            }
        except Exception as e:
            _log.exception('MusicXML parse error')
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return {'error': f'Failed to parse: {e}'}

    @app.websocket('/ws/plugins/musicxml_import/build')
    async def ws_build_mxml(
        websocket: WebSocket,
        upload_id: str,
        title: str = '',
        composer: str = '',
        include_audio: str = '1',
    ):
        """Build a .feedpak from the uploaded MusicXML, stream progress."""
        await websocket.accept()
        want_audio = include_audio not in ('0', 'false', 'no')

        dlc = _get_dlc_dir()
        if not dlc:
            await websocket.send_json({'error': 'DLC folder not configured'})
            await websocket.close()
            return

        # Resolve the opaque upload token to the server-created temp path.
        # Pop immediately: each upload is built at most once, and the temp
        # dir is removed in the build's finally block either way. The TTL
        # is enforced here too — the purge in /upload is only opportunistic.
        entry = _uploads.pop(upload_id, None)
        if entry is not None and time.monotonic() - entry[1] > _UPLOAD_TTL_SECONDS:
            shutil.rmtree(entry[0].parent, ignore_errors=True)
            entry = None
        if entry is None or not entry[0].exists():
            await websocket.send_json({'error': 'File expired — please upload again'})
            await websocket.close()
            return
        tmp_path = str(entry[0])

        progress_queue: asyncio.Queue = asyncio.Queue()

        def _do_build():
            tmp_midi_dir = None

            def report(stage: str, pct: int) -> None:
                progress_queue.put_nowait({'stage': stage, 'progress': pct})

            try:
                report('Parsing MusicXML…', 10)
                xml_bytes = Path(tmp_path).read_bytes()
                result = _mxml.parse_musicxml(xml_bytes)

                use_title = title.strip() or result['title']
                use_composer = composer.strip() or result['composer']

                report(
                    f'Parsed {result["measure_count"]} measures'
                    f' — generating MIDI…',
                    25,
                )

                tmp_midi_dir = Path(tempfile.mkdtemp())
                tmp_midi = tmp_midi_dir / 'score.mid'
                tmp_midi.write_bytes(result['midi_bytes'])

                # Either way a pack may end up without stems — a local
                # authoring intermediate (feedpak spec §5.3.2 carve-out):
                # deliberately when the user unchecked "include audio",
                # or gracefully when FluidSynth fails (reported on
                # completion, never fatal).
                audio_path = None
                audio_error = None
                audio_skipped = False
                if not want_audio:
                    audio_skipped = True
                    report('Audio skipped (unchecked).', 65)
                elif _render_midi_to_audio is None:
                    audio_error = 'gp2midi not available'
                    report(f'Audio skipped: {audio_error}', 65)
                else:
                    try:
                        report('Rendering audio with FluidSynth…', 40)
                        tmp_ogg_base = str(tmp_midi_dir / 'audio')
                        audio_path = _render_midi_to_audio(str(tmp_midi), tmp_ogg_base)
                        report('Audio rendered.', 65)
                    except Exception as e:
                        audio_error = str(e)
                        report(f'Audio skipped: {audio_error}', 65)

                report('Assembling feedpak…', 75)
                pak_bytes = _mxml.build_feedpak_zip(
                    result, audio_path, use_title, use_composer
                )

                safe_t = re.sub(r'[<>:"/\\|?*\s]', '_', use_title)[:60]
                safe_t = re.sub(r'_mxml$', '', safe_t, flags=re.IGNORECASE)
                safe_a = re.sub(r'[<>:"/\\|?*\s]', '_', use_composer)[:40]
                out_name = (
                    f'{safe_t}_{safe_a}_mxml.feedpak'
                    if safe_a else f'{safe_t}_mxml.feedpak'
                )

                out_dir = Path(dlc) / 'musicxml'
                out_dir.mkdir(parents=True, exist_ok=True)
                out_path = out_dir / out_name

                report('Writing to DLC folder…', 88)
                out_path.write_bytes(pak_bytes)

                try:
                    rel_name = str(Path('musicxml') / out_name)
                    meta = _extract_meta(out_path)
                    stat = out_path.stat()
                    _meta_db.put(rel_name, stat.st_mtime, stat.st_size, meta)
                except Exception:
                    _log.warning('metadata indexing failed for %r', out_name, exc_info=True)

                msg = {
                    'done': True,
                    'progress': 100,
                    'stage': 'Complete!',
                    'filename': out_name,
                    'measure_count': result['measure_count'],
                    'duration': result['duration'],
                }
                if audio_error:
                    msg['audio_warning'] = audio_error
                if audio_skipped:
                    msg['audio_skipped'] = True
                progress_queue.put_nowait(msg)

            except Exception as e:
                _log.exception('build error')
                progress_queue.put_nowait({'error': str(e)})
            finally:
                if tmp_midi_dir is not None:
                    shutil.rmtree(tmp_midi_dir, ignore_errors=True)
                try:
                    shutil.rmtree(Path(tmp_path).parent, ignore_errors=True)
                except Exception:
                    pass

        loop = asyncio.get_running_loop()
        build_task = loop.run_in_executor(None, _do_build)

        try:
            while True:
                try:
                    msg = await asyncio.wait_for(progress_queue.get(), timeout=1.0)
                    await websocket.send_json(msg)
                    if msg.get('done') or msg.get('error'):
                        break
                except asyncio.TimeoutError:
                    if build_task.done():
                        break
        except WebSocketDisconnect:
            pass

        await websocket.close()
