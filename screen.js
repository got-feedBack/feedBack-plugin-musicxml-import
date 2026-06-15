// MusicXML Import plugin — screen.js

(function () {
'use strict';

const PLUGIN_ID = 'musicxml_import';
const API_BASE  = `/api/plugins/${PLUGIN_ID}`;
const WS_PROTO  = location.protocol === 'https:' ? 'wss' : 'ws';
const WS_BASE   = `${WS_PROTO}://${location.host}/ws/plugins/${PLUGIN_ID}`;

let _uploadId = null;

function esc(s) {
    return String(s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;')
        .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ── Drop zone setup ───────────────────────────────────────────────────────

setTimeout(() => {
    const dropzone  = document.getElementById('mxi-dropzone');
    const fileInput = document.getElementById('mxi-file-input');
    if (!dropzone || !fileInput) return;

    dropzone.addEventListener('click', () => fileInput.click());

    dropzone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropzone.classList.add('border-accent/60', 'bg-accent/5');
    });

    dropzone.addEventListener('dragleave', () => {
        dropzone.classList.remove('border-accent/60', 'bg-accent/5');
    });

    dropzone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropzone.classList.remove('border-accent/60', 'bg-accent/5');
        const file = e.dataTransfer.files[0];
        if (file) mxiHandleFile(file);
    });

    fileInput.addEventListener('change', () => {
        if (fileInput.files[0]) mxiHandleFile(fileInput.files[0]);
    });
}, 100);

// ── File handling ─────────────────────────────────────────────────────────

async function mxiHandleFile(file) {
    const ext = file.name.split('.').pop().toLowerCase();
    if (!['xml', 'musicxml'].includes(ext)) {
        alert('Only .xml and .musicxml files are supported.');
        return;
    }

    const dropzone = document.getElementById('mxi-dropzone');
    dropzone.innerHTML = `<p class="text-gray-400 text-sm">Parsing ${esc(file.name)}…</p>`;

    const reader = new FileReader();
    reader.onload = async (e) => {
        const b64 = e.target.result.split(',')[1];
        try {
            const resp = await fetch(`${API_BASE}/upload`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ filename: file.name, data: b64 }),
            });
            const data = await resp.json();

            if (data.error) {
                dropzone.innerHTML = `
                    <p class="text-red-400 text-sm">${esc(data.error)}</p>
                    <button onclick="mxiReset()" class="mt-3 text-xs text-gray-500 hover:text-white">
                        Try another file
                    </button>`;
                return;
            }

            _uploadId = data.upload_id;
            mxiShowParsed(data);
        } catch (err) {
            dropzone.innerHTML = `
                <p class="text-red-400 text-sm">Upload failed: ${esc(String(err))}</p>
                <button onclick="mxiReset()" class="mt-3 text-xs text-gray-500 hover:text-white">
                    Try again
                </button>`;
        }
    };
    reader.readAsDataURL(file);
}

function mxiShowParsed(data) {
    document.getElementById('mxi-dropzone').classList.add('hidden');
    document.getElementById('mxi-parsed').classList.remove('hidden');
    document.getElementById('mxi-progress').classList.add('hidden');
    document.getElementById('mxi-result').classList.add('hidden');

    document.getElementById('mxi-title').value    = data.title    || '';
    document.getElementById('mxi-composer').value = data.composer || '';

    const mins  = Math.floor((data.duration || 0) / 60);
    const secs  = Math.round((data.duration || 0) % 60);
    const parts = (data.part_names || []).join(', ') || '—';
    document.getElementById('mxi-info').innerHTML =
        `${data.measure_count} measures`
        + ` &nbsp;·&nbsp; ${mins}:${String(secs).padStart(2, '0')}`
        + ` &nbsp;·&nbsp; Parts: ${esc(parts)}`;
}

// ── Build ─────────────────────────────────────────────────────────────────

async function mxiBuild() {
    if (!_uploadId) return;

    const title    = document.getElementById('mxi-title').value.trim();
    const composer = document.getElementById('mxi-composer').value.trim();

    document.getElementById('mxi-parsed').classList.add('hidden');
    document.getElementById('mxi-progress').classList.remove('hidden');
    document.getElementById('mxi-result').classList.add('hidden');

    const params = new URLSearchParams({ upload_id: _uploadId, title, composer });
    const ws = new WebSocket(`${WS_BASE}/build?${params}`);

    ws.onmessage = (ev) => {
        const msg = JSON.parse(ev.data);

        if (msg.progress !== undefined)
            document.getElementById('mxi-bar').style.width = msg.progress + '%';
        if (msg.stage)
            document.getElementById('mxi-stage').textContent = msg.stage;

        if (msg.done) {
            document.getElementById('mxi-progress').classList.add('hidden');
            document.getElementById('mxi-result').classList.remove('hidden');

            const mins = Math.floor((msg.duration || 0) / 60);
            const secs = Math.round((msg.duration || 0) % 60);
            const audioLine = msg.audio_warning
                ? `<p class="text-amber-400/80 text-xs mt-2">⚠ No audio: ${esc(msg.audio_warning)}</p>`
                : '';

            document.getElementById('mxi-result').innerHTML = `
                <div class="bg-green-900/20 border border-green-800/30 rounded-xl p-5 text-center">
                    <p class="text-green-400 font-semibold mb-1">Notation Sloppak Created!</p>
                    <p class="text-sm text-gray-400">${esc(msg.filename)}</p>
                    <p class="text-xs text-gray-500 mt-1">
                        ${msg.measure_count} measures &nbsp;·&nbsp; ${mins}:${String(secs).padStart(2, '0')}
                    </p>
                    ${audioLine}
                    <button onclick="mxiReset()"
                        class="mt-4 px-4 py-2 bg-dark-600 hover:bg-dark-500 rounded-xl text-sm text-gray-300 transition">
                        Import Another
                    </button>
                </div>`;
        }

        if (msg.error) {
            document.getElementById('mxi-progress').classList.add('hidden');
            document.getElementById('mxi-result').classList.remove('hidden');
            document.getElementById('mxi-result').innerHTML = `
                <div class="bg-red-900/20 border border-red-800/30 rounded-xl p-5 text-center">
                    <p class="text-red-400 font-semibold mb-1">Build Failed</p>
                    <p class="text-sm text-gray-400">${esc(msg.error)}</p>
                    <button onclick="mxiReset()"
                        class="mt-4 px-4 py-2 bg-dark-600 hover:bg-dark-500 rounded-xl text-sm text-gray-300 transition">
                        Try Again
                    </button>
                </div>`;
        }
    };

    ws.onerror = () => {
        document.getElementById('mxi-progress').classList.add('hidden');
        document.getElementById('mxi-result').classList.remove('hidden');
        document.getElementById('mxi-result').innerHTML = `
            <p class="text-red-400">Connection lost</p>
            <button onclick="mxiReset()" class="mt-3 text-xs text-gray-500 hover:text-white">
                Try again
            </button>`;
    };
}

// ── Reset ─────────────────────────────────────────────────────────────────

function mxiReset() {
    _uploadId = null;
    const fi = document.getElementById('mxi-file-input');
    if (fi) fi.value = '';
    document.getElementById('mxi-parsed').classList.add('hidden');
    document.getElementById('mxi-progress').classList.add('hidden');
    document.getElementById('mxi-result').classList.add('hidden');

    const dropzone = document.getElementById('mxi-dropzone');
    dropzone.classList.remove('hidden');
    dropzone.innerHTML = `
        <svg class="w-12 h-12 mx-auto mb-4 text-gray-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5"
                d="M9 19V6l12-3v13M9 19c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zm12-3c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zM9 10l12-3"/>
        </svg>
        <p class="text-gray-400 text-sm mb-2">Drag and drop a MusicXML file here</p>
        <p class="text-gray-600 text-xs">or click to browse &nbsp;·&nbsp; .xml .musicxml</p>`;
}

// Expose build/reset globally so onclick in screen.html works
window.mxiBuild = mxiBuild;
window.mxiReset = mxiReset;

})();
