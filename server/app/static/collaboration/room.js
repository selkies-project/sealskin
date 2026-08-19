// ---------------------------------------------------------------------------
// WebCodecs track plumbing: capture (MediaStreamTrack -> VideoFrame/AudioData for
// the encoders) and presentation (decoded VideoFrame -> MediaStreamTrack for a
// <video> element). The same two capabilities ship in three different shapes:
//
//   * Chromium exposes MediaStreamTrackProcessor and MediaStreamTrackGenerator on
//     Window only (non-standard), and both handle audio and video.
//   * Safari 18+ implements the standard, where MediaStreamTrackProcessor and
//     VideoTrackGenerator are exposed to a DedicatedWorker *only* and are
//     video-only; a MediaStreamTrack is transferred into the worker to reach them.
//   * Firefox has neither yet and intends to follow Safari, so it stays on the DOM
//     shims at the bottom of this block (as does Safari's audio, which the standard
//     processor does not cover).
//
// Note the shape difference: a VideoTrackGenerator *has* a .track, while a
// MediaStreamTrackGenerator *is* a track. Everything below is written against a
// { track, ... } pair so the paths stay interchangeable at the call sites.
// ---------------------------------------------------------------------------

const hasWindowTrackProcessor = (typeof MediaStreamTrackProcessor !== 'undefined');
const hasWindowTrackGenerator = (typeof MediaStreamTrackGenerator !== 'undefined');

// Frames a worker-hosted generator may be behind before new ones are dropped, and
// the consecutive-drop count after which a generator is considered stalled for good.
const SINK_MAX_IN_FLIGHT = 3;
const SINK_STALL_DROP_LIMIT = 30;
// A worker that never reports its capabilities must not hang the media startup.
const MEDIA_WORKER_PROBE_TIMEOUT_MS = 3000;

// Hosts the DedicatedWorker-only halves of mediacapture-transform. Multiplexed by
// id so a single worker serves every capture source and every presentation sink.
const MEDIA_WORKER_SRC = `
const sources = new Map();
const sinks = new Map();
const MAX_IN_FLIGHT = ${SINK_MAX_IN_FLIGHT};
const STALL_DROP_LIMIT = ${SINK_STALL_DROP_LIMIT};

self.postMessage({
    type: 'caps',
    processor: (typeof MediaStreamTrackProcessor !== 'undefined'),
    generator: (typeof VideoTrackGenerator !== 'undefined')
});

async function startSource(id, track) {
    let reader;
    try {
        reader = new MediaStreamTrackProcessor({ track: track }).readable.getReader();
    } catch (err) {
        try { track.stop(); } catch (e) {}
        self.postMessage({ type: 'sourceFailed', id: id });
        return;
    }
    const state = { reader: reader, track: track, inFlight: 0 };
    sources.set(id, state);
    try {
        for (;;) {
            const result = await reader.read();
            if (result.done) break;
            const frame = result.value;
            // Stopped while a read was outstanding.
            if (sources.get(id) !== state) { frame.close(); return; }
            // The page has not drained what it already has, so this frame would only
            // add latency and pin GPU memory: drop it and keep reading fresher ones.
            if (state.inFlight >= MAX_IN_FLIGHT) { frame.close(); continue; }
            state.inFlight++;
            self.postMessage({ type: 'frame', id: id, frame: frame }, [frame]);
        }
    } catch (err) {}
    if (sources.get(id) === state) {
        sources.delete(id);
        try { track.stop(); } catch (e) {}
        self.postMessage({ type: 'sourceEnd', id: id });
    }
}

function stopSource(id) {
    const state = sources.get(id);
    if (!state) return;
    sources.delete(id);
    try { state.reader.cancel(); } catch (e) {}
    try { state.track.stop(); } catch (e) {}
}

function startSink(id) {
    let generator;
    try {
        generator = new VideoTrackGenerator();
    } catch (err) {
        self.postMessage({ type: 'sinkFailed', id: id });
        return;
    }
    sinks.set(id, { writer: generator.writable.getWriter(), drops: 0 });
    // The generator has a track rather than being one; the page needs it for
    // <video>.srcObject, and it is only reachable from here by transfer.
    self.postMessage({ type: 'sinkTrack', id: id, track: generator.track }, [generator.track]);
}

function presentToSink(id, frame) {
    const state = sinks.get(id);
    if (!state) { frame.close(); return; }
    // A generator whose consumer stopped pulling stays backpressured forever, so
    // drop rather than queue, and give the sink up once it is clearly stalled.
    if (state.writer.desiredSize !== null && state.writer.desiredSize <= 0) {
        frame.close();
        self.postMessage({ type: 'sinkAck', id: id });
        if (++state.drops >= STALL_DROP_LIMIT) {
            closeSink(id);
            self.postMessage({ type: 'sinkError', id: id });
        }
        return;
    }
    state.drops = 0;
    // write() consumes the frame on success; on rejection it does not, so it is
    // closed here to avoid leaking it.
    state.writer.write(frame).then(function () {
        self.postMessage({ type: 'sinkAck', id: id });
    }, function () {
        try { frame.close(); } catch (e) {}
        self.postMessage({ type: 'sinkAck', id: id });
        closeSink(id);
        self.postMessage({ type: 'sinkError', id: id });
    });
}

function closeSink(id) {
    const state = sinks.get(id);
    if (!state) return;
    sinks.delete(id);
    try { state.writer.close(); } catch (e) {}
}

self.onmessage = (e) => {
    const m = e.data;
    if (!m) return;
    switch (m.type) {
        case 'source': startSource(m.id, m.track); break;
        case 'sourceAck': {
            const state = sources.get(m.id);
            if (state && state.inFlight > 0) state.inFlight--;
            break;
        }
        case 'sourceStop': stopSource(m.id); break;
        case 'sink': startSink(m.id); break;
        case 'present': presentToSink(m.id, m.frame); break;
        case 'sinkClose': closeSink(m.id); break;
    }
};
`;

// Worklet for the shim capture path: hands raw channel data to the page, which
// packs it into AudioData. Kept tiny because it runs on the audio render thread.
const SHIM_AUDIO_WORKLET_SRC = `
registerProcessor('mstp-shim', class extends AudioWorkletProcessor {
    process(inputs) {
        const input = inputs[0];
        if (input && input.length > 0 && input[0] && input[0].length > 0) {
            this.port.postMessage(input);
        }
        return true;
    }
});
`;

let mediaWorker = null;
let mediaWorkerProbe = null;
let mediaWorkerSeq = 0;
const mediaWorkerSources = new Map();
const mediaWorkerSinks = new Map();

// Which path a session settled on, said once: knowing that a client fell back to a
// canvas readback is the difference between explaining its CPU cost and guessing.
const loggedMediaPaths = new Set();
const logMediaPath = (message) => {
    if (loggedMediaPaths.has(message)) return;
    loggedMediaPaths.add(message);
    console.info(`[Media] ${message}`);
};

// Every client of the worker gives up when it dies; from then on callers fall back
// to the DOM shims and the canvas rather than restarting a worker that just failed.
const failMediaWorker = () => {
    mediaWorker = null;
    mediaWorkerProbe = Promise.resolve(null);
    const sources = Array.from(mediaWorkerSources.values());
    const sinks = Array.from(mediaWorkerSinks.values());
    mediaWorkerSources.clear();
    mediaWorkerSinks.clear();
    sources.forEach(source => source.onEnd(true));
    sinks.forEach(sink => sink.onFail());
};

// Create the shared media worker and resolve once it has reported which of the
// worker-only interfaces it actually has. Resolves null when there is no worker or
// it has neither, so callers can fall back without probing again.
const ensureMediaWorker = () => {
    if (mediaWorkerProbe) return mediaWorkerProbe;
    mediaWorkerProbe = new Promise((resolve) => {
        let worker;
        try {
            const workerURL = URL.createObjectURL(new Blob([MEDIA_WORKER_SRC], { type: 'text/javascript' }));
            worker = new Worker(workerURL);
            // The worker holds its own reference to the fetched script.
            URL.revokeObjectURL(workerURL);
        } catch (err) {
            console.warn('[Media] media worker unavailable:', err);
            resolve(null);
            return;
        }

        let settled = false;
        const settle = (caps) => {
            if (settled) return;
            settled = true;
            clearTimeout(probeTimer);
            if (!caps) {
                try { worker.terminate(); } catch (e) {}
            } else {
                mediaWorker = worker;
            }
            resolve(caps);
        };
        const probeTimer = setTimeout(() => settle(null), MEDIA_WORKER_PROBE_TIMEOUT_MS);

        worker.onerror = (event) => {
            if (!settled) {
                console.warn('[Media] media worker failed to start:', event.message || event);
                settle(null);
                return;
            }
            console.warn('[Media] media worker error:', event.message || event);
            try { worker.terminate(); } catch (e) {}
            failMediaWorker();
        };

        worker.onmessage = (e) => {
            const m = e.data;
            if (!m) return;
            switch (m.type) {
                case 'caps':
                    // Nothing worker-only here: not worth keeping the worker alive.
                    settle((m.processor || m.generator) ? m : null);
                    return;
                case 'frame': {
                    const source = mediaWorkerSources.get(m.id);
                    if (source) source.onFrame(m.frame);
                    else m.frame.close();
                    return;
                }
                case 'sourceEnd':
                case 'sourceFailed': {
                    const source = mediaWorkerSources.get(m.id);
                    if (source) source.onEnd(m.type === 'sourceFailed');
                    return;
                }
                case 'sinkTrack': {
                    const sink = mediaWorkerSinks.get(m.id);
                    if (sink) sink.onTrack(m.track);
                    else { try { m.track.stop(); } catch (err) {} }
                    return;
                }
                case 'sinkFailed':
                case 'sinkError': {
                    const sink = mediaWorkerSinks.get(m.id);
                    if (sink) sink.onFail();
                    return;
                }
                case 'sinkAck': {
                    const sink = mediaWorkerSinks.get(m.id);
                    if (sink) sink.onAck();
                    return;
                }
            }
        };
    });
    return mediaWorkerProbe;
};

// Standard capture path: the track is transferred into the worker, which reads it
// with the real MediaStreamTrackProcessor and transfers frames back one at a time.
const createWorkerTrackProcessor = (track) => {
    if (!mediaWorker) return null;
    // The page keeps the original track for the local preview and the enabled
    // toggle -- transferring it would detach it -- so the worker gets a clone.
    let clone;
    try {
        clone = track.clone();
    } catch (err) {
        console.warn('[Media] track clone failed:', err);
        return null;
    }

    const id = ++mediaWorkerSeq;
    let stopped = false;
    const stop = () => {
        if (stopped) return;
        stopped = true;
        mediaWorkerSources.delete(id);
        if (mediaWorker) mediaWorker.postMessage({ type: 'sourceStop', id });
        // A no-op once the clone has been transferred away; the worker stops its own.
        try { clone.stop(); } catch (err) {}
        // Lets a reader waiting on the next frame settle instead of hanging.
        if (streamController) { try { streamController.close(); } catch (err) {} }
    };

    let streamController = null;
    const readable = new ReadableStream({
        start(controller) {
            streamController = controller;
            mediaWorkerSources.set(id, {
                onFrame(frame) {
                    if (stopped) { frame.close(); return; }
                    // Enqueue only what the encoder is keeping up with.
                    if (controller.desiredSize !== null && controller.desiredSize <= 0) frame.close();
                    else controller.enqueue(frame);
                    if (mediaWorker) mediaWorker.postMessage({ type: 'sourceAck', id });
                },
                onEnd(failed) {
                    mediaWorkerSources.delete(id);
                    if (stopped) return;
                    stopped = true;
                    try {
                        if (failed) controller.error(new Error('worker MediaStreamTrackProcessor failed'));
                        else controller.close();
                    } catch (err) {}
                }
            });
        },
        cancel() { stop(); }
    });

    try {
        mediaWorker.postMessage({ type: 'source', id, track: clone }, [clone]);
    } catch (err) {
        // No transferable MediaStreamTrack here after all.
        console.warn('[Media] track transfer failed:', err);
        mediaWorkerSources.delete(id);
        stopped = true;
        try { clone.stop(); } catch (e) {}
        return null;
    }

    return { readable, close: stop };
};

// Shim capture path for engines with no processor at all (Firefox today).
const createShimVideoSource = (track, controller) => {
    const video = document.createElement('video');
    video.muted = true;
    video.playsInline = true;
    video.autoplay = true;
    video.disableRemotePlayback = true;
    // Visually inert but still in the DOM and painted: a fully detached <video> is
    // allowed to stop producing frames.
    video.style.cssText = 'position:fixed; top:0; left:0; width:1px; height:1px; opacity:0; pointer-events:none;';
    document.body.appendChild(video);
    video.srcObject = new MediaStream([track]);

    let closed = false;
    let usesFrameCallback = false;
    let handle = null;
    let canvas = null;
    let canvasCtx = null;
    let needsReadback = false;

    const teardown = () => {
        if (closed) return;
        closed = true;
        if (handle !== null) {
            if (usesFrameCallback) {
                try { video.cancelVideoFrameCallback(handle); } catch (err) {}
            } else {
                cancelAnimationFrame(handle);
            }
            handle = null;
        }
        try { video.srcObject = null; } catch (err) {}
        video.remove();
    };

    const schedule = () => {
        if (closed) return;
        usesFrameCallback = (typeof video.requestVideoFrameCallback === 'function');
        handle = usesFrameCallback ? video.requestVideoFrameCallback(onFrame) : requestAnimationFrame(onFrame);
    };

    function onFrame(now, metadata) {
        handle = null;
        if (closed) return;
        if (track.readyState === 'ended') {
            teardown();
            try { controller.close(); } catch (err) {}
            return;
        }
        // Skip the work entirely while the encoder is behind.
        const draining = (controller.desiredSize === null || controller.desiredSize > 0);
        if (draining && video.readyState >= 2 && video.videoWidth > 0) {
            const timestamp = Math.round(metadata && metadata.mediaTime != null
                ? metadata.mediaTime * 1e6
                : performance.now() * 1000);
            let frame = null;
            try {
                // Zero-copy where the engine takes a video element: no CPU readback.
                if (needsReadback) throw new Error('readback');
                frame = new VideoFrame(video, { timestamp });
            } catch (err) {
                // Engines that reject HTMLVideoElement fall back to a canvas readback,
                // latched so the rejection is not re-thrown on every single frame.
                needsReadback = true;
                if (!canvas) {
                    canvas = document.createElement('canvas');
                    canvasCtx = canvas.getContext('2d', { desynchronized: true, willReadFrequently: true });
                }
                if (canvas.width !== video.videoWidth || canvas.height !== video.videoHeight) {
                    canvas.width = video.videoWidth;
                    canvas.height = video.videoHeight;
                }
                try {
                    canvasCtx.drawImage(video, 0, 0);
                    frame = new VideoFrame(canvas, { timestamp });
                } catch (readbackErr) {
                    frame = null;
                }
            }
            if (frame) controller.enqueue(frame);
        }
        schedule();
    }

    const playback = video.play();
    if (playback && playback.catch) playback.catch(() => {});
    schedule();

    return teardown;
};

const createShimAudioSource = (track, controller) => {
    let closed = false;
    let audioCtx = null;
    let sourceNode = null;
    let workletNode = null;

    const teardown = () => {
        closed = true;
        if (workletNode) {
            workletNode.port.onmessage = null;
            try { workletNode.disconnect(); } catch (err) {}
            workletNode = null;
        }
        if (sourceNode) {
            try { sourceNode.disconnect(); } catch (err) {}
            sourceNode = null;
        }
        if (audioCtx) {
            const ctx = audioCtx;
            audioCtx = null;
            if (ctx.state !== 'closed') ctx.close().catch(() => {});
        }
    };

    (async () => {
        let workletURL = null;
        try {
            audioCtx = new (window.AudioContext || window.webkitAudioContext)();
            workletURL = URL.createObjectURL(new Blob([SHIM_AUDIO_WORKLET_SRC], { type: 'application/javascript' }));
            await audioCtx.audioWorklet.addModule(workletURL);
            if (closed) { teardown(); return; }

            sourceNode = audioCtx.createMediaStreamSource(new MediaStream([track]));
            workletNode = new AudioWorkletNode(audioCtx, 'mstp-shim');
            sourceNode.connect(workletNode);

            // AudioEncoder wants a gap-free, monotonic timeline, which a wall clock
            // read at message-delivery time does not give.
            let sampleCursor = 0;
            workletNode.port.onmessage = ({ data: channels }) => {
                if (closed || !channels || channels.length === 0) return;
                const numberOfFrames = channels[0].length;
                const numberOfChannels = channels.length;
                const timestamp = Math.round(sampleCursor / audioCtx.sampleRate * 1e6);
                sampleCursor += numberOfFrames;
                if (controller.desiredSize !== null && controller.desiredSize <= 0) return;

                // "f32" is the interleaved layout.
                const interleaved = new Float32Array(numberOfFrames * numberOfChannels);
                for (let i = 0; i < numberOfFrames; i++) {
                    for (let ch = 0; ch < numberOfChannels; ch++) {
                        interleaved[i * numberOfChannels + ch] = channels[ch][i];
                    }
                }
                controller.enqueue(new AudioData({
                    format: 'f32',
                    sampleRate: audioCtx.sampleRate,
                    numberOfFrames,
                    numberOfChannels,
                    timestamp,
                    data: interleaved
                }));
            };
        } catch (err) {
            teardown();
            try { controller.error(err); } catch (controllerErr) {}
        } finally {
            if (workletURL) URL.revokeObjectURL(workletURL);
        }
    })();

    return teardown;
};

const createShimTrackProcessor = (track) => {
    logMediaPath(`${track.kind} capture: DOM shim (no MediaStreamTrackProcessor in this browser).`);
    let teardown = () => {};
    let streamController = null;
    const readable = new ReadableStream({
        start(controller) {
            streamController = controller;
            teardown = (track.kind === 'video')
                ? createShimVideoSource(track, controller)
                : createShimAudioSource(track, controller);
        },
        cancel() { teardown(); }
    });
    return {
        readable,
        close: () => {
            teardown();
            // Lets a reader waiting on the next frame settle instead of hanging.
            try { streamController.close(); } catch (err) {}
        }
    };
};

// Capture side. Returns a MediaStreamTrackProcessor-alike: { readable, close() }.
const createTrackProcessor = async (track) => {
    if (hasWindowTrackProcessor) {
        try {
            const processor = new MediaStreamTrackProcessor({ track });
            logMediaPath(`${track.kind} capture: MediaStreamTrackProcessor on the page.`);
            // The native processor's readable ends with its track, so there is
            // nothing of our own to unwind.
            return { readable: processor.readable, close: () => {} };
        } catch (err) {
            console.warn('[Media] page MediaStreamTrackProcessor failed:', err);
        }
    }
    // The standard processor is worker-only, and video-only.
    if (track.kind === 'video') {
        const caps = await ensureMediaWorker();
        if (caps && caps.processor) {
            const processor = createWorkerTrackProcessor(track);
            if (processor) {
                logMediaPath('video capture: MediaStreamTrackProcessor in the media worker.');
                return processor;
            }
        }
    }
    return createShimTrackProcessor(track);
};

// Chromium's generator, which is a track rather than having one.
const createWindowVideoSink = (onFail) => {
    let generator;
    let writer;
    try {
        generator = new MediaStreamTrackGenerator({ kind: 'video' });
        writer = generator.writable.getWriter();
    } catch (err) {
        console.warn('[Media] MediaStreamTrackGenerator unavailable:', err);
        if (generator) { try { generator.stop(); } catch (e) {} }
        return null;
    }

    let dead = false;
    let drops = 0;
    const die = () => {
        if (dead) return;
        dead = true;
        try { writer.close(); } catch (err) {}
        try { generator.stop(); } catch (err) {}
        if (onFail) onFail();
    };

    return {
        track: generator,
        get alive() { return !dead; },
        // Returns true when the sink took ownership of the frame.
        write(frame) {
            if (dead) return false;
            if (writer.desiredSize !== null && writer.desiredSize <= 0) {
                frame.close();
                if (++drops >= SINK_STALL_DROP_LIMIT) {
                    console.warn('[Media] video sink stalled; falling back to the canvas.');
                    die();
                }
                return true;
            }
            drops = 0;
            writer.write(frame).catch(() => {
                try { frame.close(); } catch (err) {}
                die();
            });
            return true;
        },
        close() {
            if (dead) return;
            dead = true;
            try { writer.close(); } catch (err) {}
            try { generator.stop(); } catch (err) {}
        }
    };
};

// Standard sink: the worker builds the generator and transfers its track back here
// for <video>.srcObject; decoded frames are transferred the other way.
const createWorkerVideoSink = async (onFail) => {
    if (!mediaWorker) return null;
    const id = ++mediaWorkerSeq;
    let dead = false;
    let inFlight = 0;

    const track = await new Promise((resolve) => {
        mediaWorkerSinks.set(id, {
            onTrack: resolve,
            onFail() {
                mediaWorkerSinks.delete(id);
                const wasLive = !dead;
                dead = true;
                resolve(null);
                if (wasLive && onFail) onFail();
            },
            onAck() { if (inFlight > 0) inFlight--; }
        });
        try {
            mediaWorker.postMessage({ type: 'sink', id });
        } catch (err) {
            console.warn('[Media] media worker sink request failed:', err);
            mediaWorkerSinks.delete(id);
            dead = true;
            resolve(null);
        }
    });
    if (!track) return null;

    const close = () => {
        if (dead) return;
        dead = true;
        mediaWorkerSinks.delete(id);
        if (mediaWorker) mediaWorker.postMessage({ type: 'sinkClose', id });
        try { track.stop(); } catch (err) {}
    };

    return {
        track,
        get alive() { return !dead; },
        write(frame) {
            if (dead) return false;
            // Backpressure: the worker acks each frame it hands to the generator.
            if (inFlight >= SINK_MAX_IN_FLIGHT) {
                frame.close();
                return true;
            }
            try {
                mediaWorker.postMessage({ type: 'present', id, frame }, [frame]);
                inFlight++;
            } catch (err) {
                try { frame.close(); } catch (e) {}
                close();
                if (onFail) onFail();
            }
            return true;
        },
        close
    };
};

// Presentation side. Resolves to { track, write(frame), close() }, or null when the
// browser has no generator at all and the caller should paint a canvas instead.
// onFail is called if a live sink dies later, so the caller can go back to canvas.
const createVideoSink = async (onFail) => {
    // No shipping browser exposes both a page-side MediaStreamTrackGenerator and a
    // worker-side VideoTrackGenerator, so taking this one directly skips starting a
    // worker on Chromium; revisit the short-circuit if one ever exposes both.
    if (hasWindowTrackGenerator) {
        const sink = createWindowVideoSink(onFail);
        if (sink) {
            logMediaPath('video sink: MediaStreamTrackGenerator on the page.');
            return sink;
        }
    }
    const caps = await ensureMediaWorker();
    if (caps && caps.generator) {
        const sink = await createWorkerVideoSink(onFail);
        if (sink) {
            logMediaPath('video sink: VideoTrackGenerator in the media worker.');
            return sink;
        }
    }
    logMediaPath('video sink: 2D canvas - no track generator in this browser, so every '
        + 'frame is drawn by hand instead of being handed to a <video> element.');
    return null;
};

function applyTranslations(scope, t) {
  scope.querySelectorAll('[data-i18n]').forEach(el => {
    const key = el.getAttribute('data-i18n');
    el.innerHTML = t(key);
  });
  scope.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
    const key = el.getAttribute('data-i18n-placeholder');
    el.placeholder = t(key);
  });
  scope.querySelectorAll('[data-i18n-title]').forEach(el => {
    const key = el.getAttribute('data-i18n-title');
    el.title = t(key);
  });
}

document.addEventListener('DOMContentLoaded', () => {
    const translator = getTranslator(navigator.language);
    const t = translator.t;
    document.title = t('pageTitle');

    const COLLAB_DATA = window.COLLAB_DATA;
    if (!COLLAB_DATA) {
        console.error("Collaboration data not found.");
        return;
    }

    if (COLLAB_DATA.userPermission === 'readonly') {
        const localContainer = document.getElementById('local-user-container');
        if (localContainer) {
            localContainer.style.display = 'none';
        }
    }

    if (COLLAB_DATA.userRole === 'viewer') {
        const sourceBox = document.getElementById('gamepad-source-box');
        if (sourceBox) {
            sourceBox.style.display = 'none';
        }
        const startBtn = document.getElementById('start-menu-btn');
        if (startBtn) {
            startBtn.style.display = 'none';
        }
    }


    let localStream = null;
    let audioEncoder = null;
    let videoEncoder = null;
    let audioProcessor = null;
    let videoProcessor = null;
    let remoteStreams = {};
    let mediaInitialized = false;
    let isInitializingMedia = false;
    let isMicOn = false;
    let isWebcamOn = false;
    let preferredMicId = localStorage.getItem('collab_preferredMicId') || null;
    let preferredCamId = localStorage.getItem('collab_preferredCamId') || null;
    let localAudioAnalyser = null;
    let animationFrameId = null;
    let lastKnownVolume = parseFloat(localStorage.getItem('collab_iframe_volume')) || 1.0;
    let isIframeMuted = false;

    const sendVolumeToIframe = () => {
        const iframe = document.getElementById('session-frame');
        if (iframe && iframe.contentWindow) {
            const vol = isIframeMuted ? 0 : lastKnownVolume;
            iframe.contentWindow.postMessage({ type: 'setVolume', value: vol }, '*');
            if (isIframeMuted) iframe.contentWindow.postMessage({ type: 'setMute', value: true }, '*');
        }
    };

    const handlePageInteraction = () => {
        setTimeout(sendVolumeToIframe, 500);
        ['click', 'keydown', 'touchstart'].forEach(e => document.removeEventListener(e, handlePageInteraction));
    };
    ['click', 'keydown', 'touchstart'].forEach(e => document.addEventListener(e, handlePageInteraction));
    window.addEventListener('blur', handlePageInteraction);

    let ws;
    let username = localStorage.getItem('collab_username');
    let isSidebarVisible = false;
    let messageStore = {};
    let replyingTo = null;
    let notificationAudioCtx;
    let gamepadIcons = {};
    let mkIcon = null;
    const GAMEPAD_COUNT = 4;
    let currentUserState = [];
    let publicIdToTokenMap = {};
    let currentDesignatedSpeaker = null;
    let localPublicId = null;
    let localPublicIdBytes = null;
    let availableAppsList = [];
    let pendingActions = new Set();
    const textEncoder = new TextEncoder();
    let clientResolutions = {};
    let isResolutionLocked = false;
    let currentMkOwner = null;

    const applyAutoResolution = () => {
        if (COLLAB_DATA.userRole !== 'controller') return;
        const targetToken = currentMkOwner || COLLAB_DATA.userToken;
        const iframe = document.getElementById('session-frame');
        if (!iframe || !iframe.contentWindow) return;

        if (isResolutionLocked) return;

        if (targetToken === COLLAB_DATA.userToken) {
            console.log("[Controller] MK owner is controller. Resetting resolution to window.");
            iframe.contentWindow.postMessage({ type: 'resetResolutionToWindow' }, window.location.origin);
        } else {
            const res = clientResolutions[targetToken];
            if (res) {
                console.log(`[Controller] Auto-syncing resolution to MK owner (${targetToken}): ${res.width}x${res.height}`);
                iframe.contentWindow.postMessage({ type: 'setManualResolution', width: res.width, height: res.height }, window.location.origin);
            }
        }
    };

    const sidebarEl = document.getElementById('sidebar');
    const toggleHandle = document.getElementById('sidebar-toggle-handle');
    const videoToggleHandle = document.getElementById('video-toggle-handle');
    let isVideoGridVisible = true;
    const settingsModalOverlay = document.getElementById('settings-modal-overlay');
    const settingsModalCloseBtn = document.getElementById('settings-modal-close');
    const audioInputSelect = document.getElementById('audio-input-select');
    const videoInputSelect = document.getElementById('video-input-select');
    const videoGrid = document.getElementById('video-grid');
    const videoGridContent = document.getElementById('video-grid-content');
    const localVideo = document.getElementById('local-video');
    const localContainer = document.getElementById('local-user-container');
    const toastContainer = document.getElementById('toast-container');
    let toggleMicBtn, toggleVideoBtn, iframeMuteBtn, iframeVolumeSlider; 
    
    localContainer.dataset.userToken = COLLAB_DATA.userToken;

    const MSG_TYPE = {
        VIDEO_FRAME: 0x01,
        AUDIO_FRAME: 0x02,
        VIDEO_CONFIG: 0x03,
        PCM_FRAME: 0x04,
    };
    
    const initNotificationAudio = () => {
        if (notificationAudioCtx) return;
        try {
            notificationAudioCtx = new (window.AudioContext || window.webkitAudioContext)();
            console.log('Notification AudioContext initialized.');
        } catch (e) {
            console.error("Web Audio API is not supported in this browser.", e);
        }
    };

    let isAudioUnlocked = false;
    const unlockAllAudio = () => {
        if (isAudioUnlocked) return;
        console.log("Attempting to unlock media stream audio contexts.");
        
        Object.values(remoteStreams).forEach(stream => {
            if (stream.audioContext && stream.audioContext.state === 'suspended') {
                stream.audioContext.resume().then(() => {
                    console.log(`Resumed audio for ${stream.username}`);
                });
            }
        });
        isAudioUnlocked = true;
    };


    const audioWorkletCode = `
      class AudioPlayerProcessor extends AudioWorkletProcessor {
        constructor() {
          super();
          this.audioBufferQueue = [];
          this.currentAudioData = null;
          this.currentDataOffset = 0;
          this.MAX_BUFFER_PACKETS = 5; 

          this.port.onmessage = (event) => {
            const pcmData = event.data;
            if (this.audioBufferQueue.length >= this.MAX_BUFFER_PACKETS) {
                this.audioBufferQueue.shift(); // Drop the oldest packet to reduce latency
            }
            this.audioBufferQueue.push(pcmData);
          };
        }

        process(inputs, outputs, parameters) {
            const outputChannel = outputs[0][0];
            if (!outputChannel) return true;

            const samplesPerBuffer = outputChannel.length;
            let currentSampleIndex = 0;

            while (currentSampleIndex < samplesPerBuffer) {
                if (!this.currentAudioData || this.currentDataOffset >= this.currentAudioData.length) {
                    if (this.audioBufferQueue.length > 0) {
                        this.currentAudioData = this.audioBufferQueue.shift();
                        this.currentDataOffset = 0;
                    } else {
                        outputChannel.fill(0, currentSampleIndex);
                        return true;
                    }
                }

                const samplesToCopy = Math.min(samplesPerBuffer - currentSampleIndex, this.currentAudioData.length - this.currentDataOffset);
                const chunkToCopy = this.currentAudioData.subarray(this.currentDataOffset, this.currentDataOffset + samplesToCopy);
                
                outputChannel.set(chunkToCopy, currentSampleIndex);

                this.currentDataOffset += samplesToCopy;
                currentSampleIndex += samplesToCopy;
            }

            return true;
        }
      }
      registerProcessor('audio-player-processor', AudioPlayerProcessor);
    `;

    const startMedia = async () => {
        const isStreamingSupported = 'VideoEncoder' in window && 'AudioEncoder' in window;
        
        if (COLLAB_DATA.userPermission === 'readonly' || !isStreamingSupported) {
            if (COLLAB_DATA.userPermission !== 'readonly') alert(t('alerts.webcodecsUnsupported'));
            return false;
        }
        if (mediaInitialized) {
            stopMedia();
        }

        try {
            const audioConstraints = {
                deviceId: preferredMicId ? { exact: preferredMicId } : undefined,
                echoCancellation: true,
                noiseSuppression: true,
                autoGainControl: true,
                sampleRate: 48000,
                channelCount: 1,
            };

            const videoConstraints = {
                deviceId: preferredCamId ? { exact: preferredCamId } : undefined,
                width: 320,
                height: 240,
                frameRate: 30
            };

            try {
                localStream = await navigator.mediaDevices.getUserMedia({
                    audio: audioConstraints,
                    video: videoConstraints
                });
            } catch (err) {
                localStream = await navigator.mediaDevices.getUserMedia({
                    audio: audioConstraints,
                    video: false
                });
            }

            if (localStream.getVideoTracks().length > 0) {
                localVideo.srcObject = localStream;
                localVideo.onloadedmetadata = () => {
                    localVideo.play().catch(e => console.warn("Local video autoplay was blocked.", e));
                };
            }

            const audioCtx = new AudioContext();
            const source = audioCtx.createMediaStreamSource(localStream);
            localAudioAnalyser = audioCtx.createAnalyser();
            localAudioAnalyser.fftSize = 512;
            source.connect(localAudioAnalyser);

            localStream.getAudioTracks().forEach(t => t.enabled = isMicOn);
            localStream.getVideoTracks().forEach(t => t.enabled = isWebcamOn);

            await setupAudioEncoder();

            if (localStream.getVideoTracks().length > 0) {
                await setupVideoEncoder();
            }

            mediaInitialized = true;
            return true;
        } catch (err) {
            console.error("Error getting user media:", err);
            alert(t('alerts.mediaAccessError', { message: err.message }));
            mediaInitialized = false;
            return false;
        }
    };

    const handleMediaToggle = async (type) => {
        if (isInitializingMedia) {
            console.warn("Media initialization is already in progress. Please wait.");
            return;
        }

        if (COLLAB_DATA.userPermission === 'readonly') return;
        unlockAllAudio();

        if (!mediaInitialized) {
            isInitializingMedia = true;
            try {
                const success = await startMedia();
                if (!success) {
                    return;
                }
            } finally {
                isInitializingMedia = false;
            }
        }

        if (type === 'mic') {
            isMicOn = !isMicOn;
            if (localStream) localStream.getAudioTracks().forEach(t => t.enabled = isMicOn);
            sendControlMessage('audio_state', isMicOn);
        } else if (type === 'video') {
            if (localStream && localStream.getVideoTracks().length > 0) {
                isWebcamOn = !isWebcamOn;
                localStream.getVideoTracks().forEach(t => t.enabled = isWebcamOn);
                localContainer.style.display = isWebcamOn ? 'flex' : 'none';
                sendControlMessage('video_state', isWebcamOn);
            } else {
                isWebcamOn = false;
            }
        }

        updateMediaButtonUI();
    };

    const stopMedia = () => {
        if (localStream) {
            localStream.getTracks().forEach(track => track.stop());
            localStream = null;
        }
        // Unwinds whatever the processor owns beyond the track itself: the worker's
        // cloned track, or the shim's hidden <video> / AudioContext.
        if (videoProcessor) videoProcessor.close();
        if (audioProcessor) audioProcessor.close();
        videoProcessor = null;
        audioProcessor = null;
        if (videoEncoder && videoEncoder.state !== 'closed') videoEncoder.close();
        if (audioEncoder && audioEncoder.state !== 'closed') audioEncoder.close();
        videoEncoder = null;
        audioEncoder = null;
        localAudioAnalyser = null;
        mediaInitialized = false;
    };

    const setupVideoEncoder = async () => {
        const [videoTrack] = localStream.getVideoTracks();
        if (!videoTrack) return;

        const processor = await createTrackProcessor(videoTrack);
        // startMedia() may have been restarted while the processor was being set up.
        if (!localStream || localStream.getVideoTracks()[0] !== videoTrack) {
            processor.close();
            return;
        }
        videoProcessor = processor;
        const videoReader = processor.readable.getReader();

        let frameCounter = 0;

        videoEncoder = new VideoEncoder({
            output: (chunk, meta) => {
                if (!ws || ws.readyState !== WebSocket.OPEN || !localPublicIdBytes) return;

                if (meta && meta.decoderConfig && meta.decoderConfig.description) {
                    const description = meta.decoderConfig.description;
                    const message = new Uint8Array(8 + 1 + description.byteLength);
                    message.set(localPublicIdBytes, 0);
                    message[8] = MSG_TYPE.VIDEO_CONFIG;
                    message.set(new Uint8Array(description), 9);
                    ws.send(message.buffer);
                }
                
                if (chunk.byteLength === 0) return;

                const isKeyFrame = chunk.type === 'key';
                const chunkData = new Uint8Array(8 + chunk.byteLength + 2);
                chunkData.set(localPublicIdBytes, 0);
                chunkData[8] = MSG_TYPE.VIDEO_FRAME;
                chunkData[9] = isKeyFrame ? 0x01 : 0x00;
                chunk.copyTo(chunkData.subarray(10));
                ws.send(chunkData.buffer);
            },
            error: (e) => console.error('[Encoder] VideoEncoder error:', e),
        });

        videoEncoder.configure({
            codec: 'vp8',
            width: 320,
            height: 240,
            bitrate: 1_000_000,
            framerate: 30,
            latencyMode: 'realtime',
        });

        const readFrame = () => {
            videoReader.read().then(({ done, value: frame }) => {
                if (done || !localStream) return;
                
                if (videoEncoder.state === 'configured' && isWebcamOn) {
                    const needsKeyFrame = (frameCounter % 120 === 0);
                    videoEncoder.encode(frame, { keyFrame: needsKeyFrame });
                    frameCounter++;
                }
                
                frame.close();
                readFrame();
            }).catch(e => console.error("[Encoder] Video reader error", e));
        };
        readFrame();
    };

    const setupAudioEncoder = async () => {
        const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);

        if (isIOS) {
            const ctx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
            const source = ctx.createMediaStreamSource(localStream);
            const processor = ctx.createScriptProcessor(1024, 1, 1);

            source.connect(processor);
            processor.connect(ctx.destination);

            processor.onaudioprocess = (e) => {
                if (!ws || ws.readyState !== WebSocket.OPEN || !isMicOn || !localPublicIdBytes) return;

                const inputData = e.inputBuffer.getChannelData(0);
                const pcm16 = new Int16Array(inputData.length);

                for (let i = 0; i < inputData.length; i++) {
                    const s = Math.max(-1, Math.min(1, inputData[i]));
                    pcm16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
                }

                const chunkData = new Uint8Array(9 + pcm16.byteLength);
                chunkData.set(localPublicIdBytes, 0);
                chunkData[8] = MSG_TYPE.PCM_FRAME;
                chunkData.set(new Uint8Array(pcm16.buffer), 9);
                ws.send(chunkData.buffer);
            };

            audioEncoder = {
                state: 'configured',
                close: () => {
                    source.disconnect();
                    processor.disconnect();
                    ctx.close();
                }
            };
        } else {
            const [audioTrack] = localStream.getAudioTracks();
            if (!audioTrack) return;

            const processor = await createTrackProcessor(audioTrack);
            // startMedia() may have been restarted while the processor was being set up.
            if (!localStream || localStream.getAudioTracks()[0] !== audioTrack) {
                processor.close();
                return;
            }
            audioProcessor = processor;
            const audioReader = processor.readable.getReader();

            audioEncoder = new AudioEncoder({
                output: (chunk, meta) => {
                    if (ws && ws.readyState === WebSocket.OPEN && isMicOn && localPublicIdBytes) {
                        const chunkData = new Uint8Array(8 + chunk.byteLength + 2);
                        chunkData.set(localPublicIdBytes, 0);
                        chunkData[8] = MSG_TYPE.AUDIO_FRAME;
                        chunkData[9] = 0x00;
                        chunk.copyTo(chunkData.subarray(10));
                        ws.send(chunkData.buffer);
                    }
                },
                error: (e) => console.error('[Encoder] AudioEncoder error:', e),
            });

            audioEncoder.configure({
                codec: 'opus',
                sampleRate: 48000,
                numberOfChannels: 1,
                bitrate: 128000,
            });

            const readFrame = () => {
                audioReader.read().then(({ done, value: frame }) => {
                    if (done || !localStream) return;
                    if (frame && isMicOn && audioEncoder.state === 'configured') {
                        audioEncoder.encode(frame);
                    }
                    if (frame) frame.close();
                    readFrame();
                }).catch(e => console.error("[Encoder] Audio reader error", e));
            };
            readFrame();
        }
    };

    const handleRemoteStream = (token, data) => {
        const stream = remoteStreams[token];
        if (!stream) return;

        const mediaType = new Uint8Array(data, 0, 1)[0];

        try {
            switch (mediaType) {
                case MSG_TYPE.VIDEO_CONFIG:
                    const description = data.slice(1);
                    const config = { codec: 'vp8', description: description };
                    if (stream.videoDecoder.state !== 'closed') {
                        stream.videoDecoder.configure(config);
                        stream.isConfigured = true;
                    }
                    break;

                case MSG_TYPE.VIDEO_FRAME:
                    if (stream.videoDecoder.state !== 'configured' || stream.videoMuted) return;

                    const frameType = new Uint8Array(data, 1, 1)[0];
                    const isKeyFrame = frameType === 0x01;

                    if (!stream.hasReceivedKeyFrame) {
                        if (isKeyFrame) {
                            stream.hasReceivedKeyFrame = true;
                        } else {
                            return;
                        }
                    }

                    const chunkData = data.slice(2);
                    const chunk = new EncodedVideoChunk({
                        type: isKeyFrame ? 'key' : 'delta',
                        timestamp: performance.now() * 1000,
                        data: chunkData,
                    });
                    stream.videoDecoder.decode(chunk);
                    break;

                case MSG_TYPE.AUDIO_FRAME:
                    if (stream.audioDecoder.state !== 'configured' || stream.audioMuted) return;
                    const audioChunkData = data.slice(2);
                    const audioChunk = new EncodedAudioChunk({
                        type: 'key',
                        timestamp: performance.now() * 1000,
                        data: audioChunkData
                    });
                    stream.audioDecoder.decode(audioChunk);
                    break;

                case MSG_TYPE.PCM_FRAME:
                    if (stream.audioMuted || !stream.workletNode) return;

                    const rawBytes = data.slice(1);
                    const int16Data = new Int16Array(rawBytes);
                    const float32Data = new Float32Array(int16Data.length * 3);

                    for (let i = 0; i < int16Data.length; i++) {
                        const sample = int16Data[i] < 0 ? int16Data[i] / 0x8000 : int16Data[i] / 0x7FFF;
                        const idx = i * 3;
                        float32Data[idx] = sample;
                        float32Data[idx + 1] = sample;
                        float32Data[idx + 2] = sample;
                    }

                    stream.workletNode.port.postMessage(float32Data, [float32Data.buffer]);
                    break;
            }
        } catch (e) {
            console.error(`[Decoder:${token}] Error handling remote stream data:`, e);
        }
    };

    // Decoded frames go to a track generator feeding a <video> element where the
    // browser has one, and to the tile's canvas otherwise. The canvas stays behind
    // the <video> as the fallback: it is what shows until the sink has actually
    // painted a frame, and what takes over again if the sink dies.
    const paintRemoteFrame = (stream, frame) => {
        stream.ctx.drawImage(frame, 0, 0, stream.canvas.width, stream.canvas.height);
    };

    const revealRemoteSink = (stream) => {
        stream.sinkShown = true;
        stream.video.style.display = 'block';
        const playback = stream.video.play();
        if (playback && playback.catch) playback.catch(() => {});
        if (typeof stream.video.requestVideoFrameCallback === 'function') {
            // Hiding the canvas on the first write instead flashes black: the first
            // track frame can arrive well before the <video> starts rendering.
            const generation = ++stream.sinkGeneration;
            stream.video.requestVideoFrameCallback(() => {
                if (generation !== stream.sinkGeneration || !stream.sink) return;
                stream.sinkRevealed = true;
                stream.canvas.style.display = 'none';
            });
        } else {
            // Rendering can't be observed here; assume the frame was presented.
            stream.sinkRevealed = true;
            stream.canvas.style.display = 'none';
        }
    };

    const hideRemoteSink = (stream) => {
        stream.sinkShown = false;
        stream.sinkRevealed = false;
        stream.sinkGeneration++;
        stream.video.style.display = 'none';
        stream.canvas.style.display = 'block';
    };

    const detachRemoteSink = (stream) => {
        if (stream.sink) {
            stream.sink.close();
            stream.sink = null;
        }
        hideRemoteSink(stream);
        try { stream.video.srcObject = null; } catch (err) {}
    };

    const presentRemoteFrame = (stream, frame) => {
        if (stream.sink) {
            if (stream.sink.alive) {
                if (!stream.sinkShown) revealRemoteSink(stream);
                if (!stream.sinkRevealed) paintRemoteFrame(stream, frame);
                if (stream.sink.write(frame)) return;
            }
            detachRemoteSink(stream);
        }
        paintRemoteFrame(stream, frame);
        frame.close();
    };

    const addRemoteStream = async (token, username) => {
        if (remoteStreams[token]) return;

        const container = document.createElement('div');
        container.className = 'video-container reorderable';
        container.id = `container-${token}`;
        container.dataset.userToken = token;
        container.draggable = true;
        
        const canvas = document.createElement('canvas');
        canvas.width = 240;
        canvas.height = 180;
        const ctx = canvas.getContext('2d', { desynchronized: true });
        ctx.fillStyle = '#222';
        ctx.fillRect(0, 0, 240, 180);

        // Overlays the canvas, and is only shown once a track generator is feeding it.
        const video = document.createElement('video');
        video.className = 'remote-video';
        video.autoplay = true;
        video.muted = true;
        video.playsInline = true;
        video.disableRemotePlayback = true;
        video.style.display = 'none';

        let controllerControls = '';
        if (COLLAB_DATA.userRole === 'controller') {
            controllerControls = `
                <button class="remote-control-btn resize-to-client" data-token="${token}" title="${t('tooltips.resizeClient')}"><i class="fas fa-desktop"></i></button>
                <button class="remote-control-btn designate-speaker" data-token="${token}" title="${t('tooltips.designateSpeaker')}"><i class="fas fa-star"></i></button>
            `;
        }
        const overlay = document.createElement('div');
        overlay.className = 'video-overlay';
        overlay.innerHTML = `
            <span class="username">${username}</span>
            <div class="remote-controls">
                ${controllerControls}
                <button class="remote-control-btn mute-audio" data-token="${token}" title="${t('tooltips.toggleRemoteAudio')}"><i class="fas fa-microphone"></i></button>
                <button class="remote-control-btn mute-video" data-token="${token}" title="${t('tooltips.toggleRemoteVideo')}"><i class="fas fa-video"></i></button>
            </div>
        `;
        
        container.appendChild(canvas);
        container.appendChild(video);
        container.appendChild(overlay);
        videoGridContent.appendChild(container);

        const stream = {
            username, container, canvas, ctx, video,
            videoMuted: false, audioMuted: false,
            isConfigured: true,
            hasReceivedKeyFrame: false,
            sink: null, sinkShown: false, sinkRevealed: false, sinkGeneration: 0
        };

        const videoDecoder = new VideoDecoder({
            output: (frame) => {
                if (remoteStreams[token] !== stream) {
                    frame.close();
                    return;
                }
                presentRemoteFrame(stream, frame);
            },
            error: (e) => console.error(`[Decoder:${token}] VideoDecoder error:`, e)
        });

        videoDecoder.configure({ codec: 'vp8' });

        const audioContext = new AudioContext({ sampleRate: 48000 });
        if (isAudioUnlocked && audioContext.state === 'suspended') {
            audioContext.resume();
        }
        
        const workletBlob = new Blob([audioWorkletCode], { type: 'application/javascript' });
        const workletURL = URL.createObjectURL(workletBlob);
        await audioContext.audioWorklet.addModule(workletURL);
        const workletNode = new AudioWorkletNode(audioContext, 'audio-player-processor');
        
        const analyser = audioContext.createAnalyser();
        analyser.fftSize = 512;
        workletNode.connect(analyser);
        analyser.connect(audioContext.destination);

        const audioDecoder = new AudioDecoder({
            output: (frame) => {
                const buffer = new Float32Array(frame.allocationSize({ planeIndex: 0, format: "f32" }) / 4);
                frame.copyTo(buffer, { planeIndex: 0, format: "f32" });
                workletNode.port.postMessage(buffer, [buffer.buffer]);
                frame.close();
            },
            error: (e) => console.error(`[Decoder:${token}] AudioDecoder error:`, e),
        });
        audioDecoder.configure({ codec: 'opus', sampleRate: 48000, numberOfChannels: 1 });

        Object.assign(stream, { videoDecoder, audioDecoder, audioContext, workletNode, analyser });
        remoteStreams[token] = stream;

        // The handshake can involve a worker round-trip, so the canvas renders until
        // it lands -- and again from wherever the sink gives out.
        createVideoSink(() => {
            if (remoteStreams[token] === stream) detachRemoteSink(stream);
        }).then((sink) => {
            if (!sink) return;
            // Removed, or the sink already gave out, while the handshake was in flight.
            if (remoteStreams[token] !== stream || !sink.alive) {
                sink.close();
                return;
            }
            try {
                video.srcObject = new MediaStream([sink.track]);
            } catch (err) {
                console.warn(`[Media] video sink attach failed for ${token}:`, err);
                sink.close();
                return;
            }
            stream.sink = sink;
        });
    };

    const removeRemoteStream = (token) => {
        const stream = remoteStreams[token];
        if (stream) {
            if (stream.sink) {
                stream.sink.close();
                stream.sink = null;
            }

            if (stream.videoDecoder && stream.videoDecoder.state !== 'closed') {
                stream.videoDecoder.close();
            }

            if (stream.audioDecoder && stream.audioDecoder.state !== 'closed') {
                stream.audioDecoder.close();
            }

            if (stream.audioContext) {
                if (stream.audioContext.state !== 'closed') {
                    stream.audioContext.close();
                }
            }
            if (stream.workletNode) {
                stream.workletNode.disconnect();
            }

            if (stream.container) {
                stream.container.remove();
            }

            delete remoteStreams[token];
        }
    };

    const populateDeviceLists = async () => {
        try {
            await navigator.mediaDevices.getUserMedia({audio:true, video:true});
        } catch (err) {
            console.warn("Could not get media stream for device enumeration:", err.message);
        }
        
        try {
            const devices = await navigator.mediaDevices.enumerateDevices();
            audioInputSelect.innerHTML = '';
            videoInputSelect.innerHTML = '';
            devices.forEach(device => {
                if(device.deviceId === 'default' || device.deviceId === '' || device.kind === 'audiooutput') return;
                const option = document.createElement('option');
                option.value = device.deviceId;
                option.textContent = device.label || t('devices.unlabeledDevice', { kind: device.kind, number: (device.kind === 'audioinput' ? audioInputSelect.length : videoInputSelect.length) + 1 });
                if (device.kind === 'audioinput') {
                    audioInputSelect.appendChild(option);
                } else if (device.kind === 'videoinput') {
                    videoInputSelect.appendChild(option);
                }
            });

            if (preferredMicId && audioInputSelect.querySelector(`option[value="${preferredMicId}"]`)) {
                audioInputSelect.value = preferredMicId;
            }
            if (preferredCamId && videoInputSelect.querySelector(`option[value="${preferredCamId}"]`)) {
                videoInputSelect.value = preferredCamId;
            }

        } catch (err) {
            console.error("Could not enumerate devices:", err);
        }
    };
    
    const updateSpeakingIndicators = () => {
        const speakingThreshold = 5;
        let isAnyoneSpeaking = false; 
        
        if (localAudioAnalyser && isMicOn) {
            const dataArray = new Uint8Array(localAudioAnalyser.frequencyBinCount);
            localAudioAnalyser.getByteFrequencyData(dataArray);
            const avg = dataArray.reduce((a, b) => a + b, 0) / dataArray.length;
            localContainer.classList.toggle('speaking', avg > speakingThreshold);
            if (avg > speakingThreshold) isAnyoneSpeaking = true;
        } else {
            localContainer.classList.remove('speaking');
        }

        Object.values(remoteStreams).forEach(stream => {
            if (stream.analyser && !stream.audioMuted && stream.container) {
                const dataArray = new Uint8Array(stream.analyser.frequencyBinCount);
                stream.analyser.getByteFrequencyData(dataArray);
                const avg = dataArray.reduce((a, b) => a + b, 0) / dataArray.length;
                stream.container.classList.toggle('speaking', avg > speakingThreshold);
                if (avg > speakingThreshold) isAnyoneSpeaking = true;
            } else if (stream.container) {
                stream.container.classList.remove('speaking');
            }
        });

        if (videoToggleHandle) {
            videoToggleHandle.classList.toggle('speaking-glow', isAnyoneSpeaking);
        }

        animationFrameId = requestAnimationFrame(updateSpeakingIndicators);
    };

    const initTheme = () => {
        const savedTheme = localStorage.getItem('theme') || 'light';
        document.documentElement.setAttribute('data-theme', savedTheme);
        const themeToggle = sidebarEl.querySelector('.theme-toggle');
        if (themeToggle) {
            themeToggle.classList.toggle('light', savedTheme === 'light');
        }
    };

    const toggleTheme = () => {
        const currentTheme = document.documentElement.getAttribute('data-theme');
        const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
        document.documentElement.setAttribute('data-theme', newTheme);
        localStorage.setItem('theme', newTheme);
        initTheme();
    };
    
    const toggleSidebar = () => {
        isSidebarVisible = !isSidebarVisible;
        sidebarEl.classList.toggle('visible', isSidebarVisible);
        document.querySelector('.content').classList.toggle('sidebar-visible', isSidebarVisible);
    };

    const toggleVideoGrid = () => {
        isVideoGridVisible = !isVideoGridVisible;

        if (isVideoGridVisible) {
            videoGrid.classList.remove('hidden');
        } else {
            videoGrid.classList.add('hidden');
        }
    };

    const updateGestureOverlay = () => {
        const gestureOverlay = document.getElementById('gesture-overlay');
        if (!gestureOverlay) return;

        if (COLLAB_DATA.userRole === 'controller') {
            gestureOverlay.classList.add('hidden');
            return;
        }

        const self = currentUserState.find(u => u.token === COLLAB_DATA.userToken);
        const hasInput = self && (self.slot || self.has_mk);

        if (hasInput) {
            gestureOverlay.classList.add('hidden');
        } else {
            gestureOverlay.classList.remove('hidden');
        }
    };

    const initGestures = () => {
        let sbTouchStartX = 0;
        let sbTouchStartY = 0;
        
        sidebarEl.addEventListener('touchstart', (e) => {
            sbTouchStartX = e.changedTouches[0].screenX;
            sbTouchStartY = e.changedTouches[0].screenY;
        }, { passive: true });

        sidebarEl.addEventListener('touchend', (e) => {
            const sbTouchEndX = e.changedTouches[0].screenX;
            const sbTouchEndY = e.changedTouches[0].screenY;
            
            const deltaX = sbTouchEndX - sbTouchStartX;
            const deltaY = Math.abs(sbTouchEndY - sbTouchStartY);

            if (deltaX > 50 && deltaY < 50) { 
                if (isSidebarVisible) toggleSidebar();
            }
        }, { passive: true });

        let vbTouchStartX = 0;
        let vbTouchStartY = 0;

        videoGrid.addEventListener('touchstart', (e) => {
            vbTouchStartX = e.changedTouches[0].screenX;
            vbTouchStartY = e.changedTouches[0].screenY;
        }, { passive: true });

        videoGrid.addEventListener('touchend', (e) => {
            const vbTouchEndX = e.changedTouches[0].screenX;
            const vbTouchEndY = e.changedTouches[0].screenY;

            const deltaY = vbTouchEndY - vbTouchStartY;
            const deltaX = Math.abs(vbTouchEndX - vbTouchStartX);

            if (deltaY > 50 && deltaX < 50) { 
                if (isVideoGridVisible) toggleVideoGrid();
            }
        }, { passive: true });

        const gestureOverlay = document.getElementById('gesture-overlay');
        if (gestureOverlay) {
            let lastTapTime = 0;
            gestureOverlay.addEventListener('touchstart', (e) => {
                const currentTime = new Date().getTime();
                const tapLength = currentTime - lastTapTime;
                if (tapLength < 300 && tapLength > 0) {
                    e.preventDefault(); 
                    
                    const anyVisible = isSidebarVisible || isVideoGridVisible;
                    if (anyVisible) {
                        if (isSidebarVisible) toggleSidebar();
                        if (isVideoGridVisible) toggleVideoGrid();
                    } else {
                        if (!isSidebarVisible) toggleSidebar();
                        if (!isVideoGridVisible) toggleVideoGrid();
                    }
                }
                lastTapTime = currentTime;
            });
        }
    };

    const connectWebSocket = () => {
        const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const url = `${proto}//${window.location.host}/ws/room/${COLLAB_DATA.sessionId}?token=${COLLAB_DATA.userToken}`;
        ws = new WebSocket(url);
        ws.binaryType = 'arraybuffer';

        ws.onopen = () => {
            console.log('[WS] Collaboration WebSocket connected.');
            if (COLLAB_DATA.userRole === 'controller') {
                ws.send(JSON.stringify({ action: 'get_apps' }));
                ws.send(JSON.stringify({ action: 'request_resolutions' }));
            }
            const iframe = document.getElementById('session-frame')
            if (iframe) {
                ws.send(JSON.stringify({ action: 'client_resolution', width: iframe.clientWidth, height: iframe.clientHeight }));
                if (COLLAB_DATA.userRole === 'controller') {
                    clientResolutions[COLLAB_DATA.userToken] = { width: iframe.clientWidth, height: iframe.clientHeight };
                }
            }
        };

        ws.onmessage = (event) => {
            if (event.data instanceof ArrayBuffer) {
                const publicId = new TextDecoder().decode(event.data.slice(0, 8));
                const token = publicIdToTokenMap[publicId];
                if (!token) return;
                const payload = event.data.slice(8);
                handleRemoteStream(token, payload);
                return;
            }

            const data = JSON.parse(event.data);
            switch (data.type) {
                case 'session_ended':
                    handleControllerDisconnect();
                    break;
                case 'request_resolutions': {
                    const reqIframe = document.getElementById('session-frame');
                    if (reqIframe && ws && ws.readyState === WebSocket.OPEN) {
                        ws.send(JSON.stringify({ action: 'client_resolution', width: reqIframe.clientWidth, height: reqIframe.clientHeight }));
                    }
                    break;
                }
                case 'resolution_update':
                    clientResolutions[data.token] = { width: data.width, height: data.height };
                    if (COLLAB_DATA.userRole === 'controller' && !isResolutionLocked && currentMkOwner === data.token) {
                        applyAutoResolution();
                    }
                    break;
                case 'state_update':
                    const hasJoined = sessionStorage.getItem('collab_hasJoined_' + COLLAB_DATA.sessionId);
                    if (COLLAB_DATA.userRole === 'viewer' && !hasJoined) {
                        return;
                    }
                    currentUserState = data.viewers;
                    const controllerUser = data.viewers.find(u => u.permission === 'controller');
                    const waitingOverlay = document.getElementById('waiting-overlay');
                    const iframe = document.getElementById('session-frame');
                    if (controllerUser && controllerUser.online) {
                        if (iframe && iframe.getAttribute('src') === 'about:blank' && iframe.dataset.src) {
                            iframe.src = iframe.dataset.src;
                        }
                        if (waitingOverlay && COLLAB_DATA.userRole === 'viewer') {
                            waitingOverlay.classList.add('hidden');
                        }
                    } else {
                        if (waitingOverlay && COLLAB_DATA.userRole === 'viewer') {
                            waitingOverlay.classList.remove('hidden');
                        }
                    }

                    currentDesignatedSpeaker = data.designated_speaker;

                    const self = data.viewers.find(u => u.token === COLLAB_DATA.userToken);
                    if (self && self.publicId && self.publicId !== localPublicId) {
                        localPublicId = self.publicId;
                        localPublicIdBytes = textEncoder.encode(localPublicId);
                    }

                    publicIdToTokenMap = {};
                    data.viewers.forEach(user => {
                        if (user.publicId) publicIdToTokenMap[user.publicId] = user.token;
                    });

                    document.querySelectorAll('.video-container').forEach(el => el.classList.remove('designated-speaker'));
                    document.querySelectorAll('.designate-speaker').forEach(el => el.classList.remove('active'));
                    if (currentDesignatedSpeaker) {
                        const speakerContainer = document.querySelector(`[data-user-token="${currentDesignatedSpeaker}"]`);
                        if (speakerContainer) {
                            speakerContainer.classList.add('designated-speaker');
                            const speakerButton = speakerContainer.querySelector('.designate-speaker');
                            if (speakerButton) speakerButton.classList.add('active');
                        }
                    }

                    updateGestureOverlay();

                    const mkOwnerUser = data.viewers.find(u => u.has_mk);
                    const newMkOwner = mkOwnerUser ? mkOwnerUser.token : COLLAB_DATA.userToken;
                    
                    const gamingModeBtn = document.getElementById('gaming-mode-btn');
                    if (gamingModeBtn) {
                        const isController = COLLAB_DATA.userRole === 'controller';
                        const iHaveMk = (mkOwnerUser && mkOwnerUser.token === COLLAB_DATA.userToken) || (!mkOwnerUser && isController);
                        if (iHaveMk) {
                            gamingModeBtn.classList.remove('hidden');
                        } else {
                            gamingModeBtn.classList.add('hidden');
                        }
                    }

                    if (COLLAB_DATA.userRole === 'controller' && currentMkOwner !== newMkOwner) {
                        currentMkOwner = newMkOwner;
                        if (!isResolutionLocked) {
                            applyAutoResolution();
                        }
                    }

                    const participantsToShow = data.viewers.filter(u =>
                        u.permission !== 'readonly' &&
                        u.online &&
                        u.token !== COLLAB_DATA.userToken
                    );
                    const serverTokens = new Set(participantsToShow.map(u => u.token));
                    const clientTokens = new Set(Object.keys(remoteStreams));

                    for (const token of clientTokens) {
                        if (!serverTokens.has(token)) {
                            removeRemoteStream(token);
                        }
                    }

                    for (const user of participantsToShow) {
                        const stream = remoteStreams[user.token];
                        if (!stream) {
                            addRemoteStream(user.token, user.username);
                        } else if (stream.username !== user.username) {
                            stream.username = user.username;
                            const usernameEl = stream.container.querySelector('.username');
                            if (usernameEl) {
                                usernameEl.textContent = user.username;
                            }
                        }
                    }
                    
                    updateGamepadIcons(data.viewers);
                    break;
                case 'chat_message':
                    messageStore[data.messageId] = data;
                    appendChatMessage(data, 'chat');
                    break;
                case 'user_joined':
                case 'user_left':
                case 'username_changed':
                case 'gamepad_change':
                case 'mk_change':
                    appendChatMessage(data, 'system');
                    break;
                case 'control':
                    handleControlMessage(data.payload);
                    break;
                case 'controller_disconnected':
                    break;
                case 'app_list':
                    availableAppsList = data.apps;
                    if (document.getElementById('start-menu-modal') && !document.getElementById('start-menu-modal').classList.contains('hidden')) {
                        renderStartMenu();
                    }
                    const activeApp = availableAppsList.find(app => app.active);
                    if (activeApp) {
                        const titleEl = document.getElementById('sidebar-app-title');
                        if (titleEl) titleEl.textContent = activeApp.name;
                        document.title = activeApp.name;
                    }
                    break;
                case 'app_swapped': {
                    pendingActions.clear();
                    const iframe = document.getElementById('session-frame');
                    let urlStr = iframe.src;
                    const isBlank = iframe.getAttribute('src') === 'about:blank';
                    
                    if (isBlank && iframe.dataset.src) {
                        urlStr = iframe.dataset.src;
                    }
                    if (urlStr && urlStr !== 'about:blank') {
                        try {
                            const currentSrc = new URL(urlStr, window.location.href);
                            currentSrc.searchParams.set('t', Date.now());
                            
                            if (isBlank) {
                                iframe.dataset.src = currentSrc.toString();
                            } else {
                                iframe.src = currentSrc.toString();
                            }
                        } catch (e) {
                            console.warn("Could not reload iframe on swap:", e);
                        }
                    }
                    const titleEl = document.getElementById('sidebar-app-title');
                    if (titleEl) titleEl.textContent = data.app_name;
                    document.title = data.app_name;
                    ws.send(JSON.stringify({ action: 'get_apps' }));
                    showToast({ sender: t('systemMessages.systemSender'), message: t('systemMessages.swappedApp', { app_name: data.app_name }) });
                    break;
                }
                case 'error':
                     pendingActions.clear();
                     if (document.getElementById('start-menu-modal')) renderStartMenu();
                     alert(data.message);
                     break;
            }
        };

        ws.onclose = () => {
            console.log('[WS] WebSocket closed.');
            handleControllerDisconnect();
        };
        ws.onerror = (err) => console.error('[WS] WebSocket error:', err);
    };

    const handleControllerDisconnect = () => {
        document.getElementById('disconnection-overlay').classList.remove('hidden');
        const iframe = document.getElementById('session-frame');
        if (iframe) iframe.remove();
        if (ws && ws.readyState !== WebSocket.CLOSED) ws.close();
    };
    
    const handleControlMessage = (payload) => {
        const { action, sender_token, state } = payload;
        
        if (action === 'force_cursor_render') {
            if (COLLAB_DATA.userRole === 'controller') {
                const iframe = document.getElementById('session-frame');
                if (iframe && iframe.contentWindow && iframe.contentWindow.webrtcInput) {
                    iframe.contentWindow.webrtcInput.send(`SET_NATIVE_CURSOR_RENDERING,${state}`);
                }
            }
            return;
        }

        const stream = remoteStreams[sender_token];
        if (!stream) return;

        if (action === 'video_state') {
            stream.container.style.display = state ? 'flex' : 'none';
        }
    };
 
    const sendControlMessage = (action, state) => {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ action, state }));
        }
    };

    const renderSidebar = () => {
        const hasJoined = sessionStorage.getItem('collab_hasJoined_' + COLLAB_DATA.sessionId);
        if (COLLAB_DATA.userRole === 'viewer' && !hasJoined) {
            renderUsernamePrompt();
        } else {
            renderMainSidebar();
        }
        initTheme();
    };
    
    const renderUsernamePrompt = () => {
        sidebarEl.innerHTML = `
            <div class="sidebar-content">
                <div class="username-prompt">
                    <h3>${t('usernamePrompt.title')}</h3>
                    <p>${t('usernamePrompt.description')}</p>
                    <form id="username-form">
                        <input type="text" id="username-input" placeholder="${t('usernamePrompt.placeholder')}" maxlength="25" required>
                        <button type="submit">${t('usernamePrompt.joinButton')}</button>
                    </form>
                </div>
            </div>`;
        const usernameInput = document.getElementById('username-input');
        if (username) {
            usernameInput.value = username;
        }
        document.getElementById('username-form').addEventListener('submit', handleUsernameSubmit);
    };

    const renderMainSidebar = () => {
        const isController = COLLAB_DATA.userRole === 'controller';
        const isParticipant = COLLAB_DATA.userRole === 'viewer' && COLLAB_DATA.userPermission === 'participant';

        let inviteLinksHtml = '';

        if (isController) {
            inviteLinksHtml = `
            <div class="sidebar-invite-section">
                <div class="link-group">
                    <label data-i18n="inviteLinks.participant">Collaboration User Invite</label>
                    <div class="link-input-group">
                        <input type="text" id="participant-link-input" value="${COLLAB_DATA.participantJoinUrl}" readonly>
                        <button class="copy-link-btn" data-target="participant-link-input"><i class="fas fa-copy"></i></button>
                    </div>
                </div>
                <div class="link-group">
                    <label data-i18n="inviteLinks.readonly">Read Only User Invite</label>
                    <div class="link-input-group">
                        <input type="text" id="readonly-link-input" value="${COLLAB_DATA.readonlyJoinUrl}" readonly>
                        <button class="copy-link-btn" data-target="readonly-link-input"><i class="fas fa-copy"></i></button>
                    </div>
                </div>
            </div>`;
        } else if (isParticipant && COLLAB_DATA.readonlyJoinUrl) {
            inviteLinksHtml = `
            <div class="sidebar-invite-section">
                <div class="link-group">
                    <label data-i18n="inviteLinks.readonlyParticipantView">Read Only Invite</label>
                    <div class="link-input-group">
                        <input type="text" id="readonly-link-input" value="${COLLAB_DATA.readonlyJoinUrl}" readonly>
                        <button class="copy-link-btn" data-target="readonly-link-input"><i class="fas fa-copy"></i></button>
                    </div>
                </div>
            </div>`;
        }

        let localControls = '';
        if (isController) {
            localControls = `
                <button class="remote-control-btn toggle-resolution-lock" title="${t('tooltips.lockResolution')}"><i class="fas fa-lock-open"></i></button>
                <button class="remote-control-btn resize-to-client" data-token="${COLLAB_DATA.userToken}" title="${t('tooltips.resizeClient')}"><i class="fas fa-desktop"></i></button>
                <button class="remote-control-btn designate-speaker" data-token="${COLLAB_DATA.userToken}" title="${t('tooltips.designateSpeaker')}"><i class="fas fa-star"></i></button>
            `;
        }
        document.querySelector('#local-user-container .video-overlay').innerHTML = `
            <span class="username">${isController ? 'Controller' : (username || 'You')}</span>
            <div class="remote-controls">${localControls}</div>`;

        sidebarEl.innerHTML = `
            <div class="sidebar-header">
                <h2 id="sidebar-app-title">${t('sidebar.title')}</h2>
                <div class="header-controls">
                    <button id="gaming-mode-btn" class="settings-button hidden" title="${t('tooltips.gamingMode')}">
                        <svg viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" fill="none" width="18" height="18">
                            <circle cx="12" cy="12" r="1.5" fill="currentColor" />
                            <path d="M12 5V9M12 15V19M5 12H9M15 12H19" stroke-linecap="round" />
                        </svg>
                    </button>
                    <div class="theme-toggle">
                        <div class="icon sun-icon"><svg viewBox="0 0 24 24"><path d="M12 2.25a.75.75 0 01.75.75v2.25a.75.75 0 01-1.5 0V3a.75.75 0 01.75-.75zM7.5 12a4.5 4.5 0 119 0 4.5 4.5 0 01-9 0zM18.894 6.106a.75.75 0 010 1.06l-1.591 1.59a.75.75 0 11-1.06-1.06l1.59-1.59a.75.75 0 011.06 0zM21.75 12a.75.75 0 01-.75.75h-2.25a.75.75 0 010-1.5h2.25a.75.75 0 01.75.75zM17.836 17.836a.75.75 0 01-1.06 0l-1.59-1.591a.75.75 0 111.06-1.06l1.59 1.59a.75.75 0 010 1.061zM12 21.75a.75.75 0 01-.75-.75v-2.25a.75.75 0 011.5 0v2.25a.75.75 0 01-.75-.75zM5.636 17.836a.75.75 0 010-1.06l1.591-1.59a.75.75 0 111.06 1.06l-1.59 1.59a.75.75 0 01-1.06 0zM3.75 12a.75.75 0 01.75-.75h2.25a.75.75 0 010 1.5H4.5a.75.75 0 01-.75-.75zM6.106 6.106a.75.75 0 011.06 0l1.59 1.591a.75.75 0 11-1.06 1.06l-1.59-1.59a.75.75 0 010-1.06z"/></svg></div>
                        <div class="icon moon-icon"><svg viewBox="0 0 24 24"><path d="M21.752 15.002A9.718 9.718 0 0118 15.75c-5.385 0-9.75-4.365-9.75-9.75 0-1.33.266-2.597.748-3.752A9.753 9.753 0 003 11.25C3 16.635 7.365 21 12.75 21c3.73 0 7.01-1.939 8.71-4.922.482-.97.74-2.053.742-3.176z"/></svg></div>
                    </div>
                    <button id="reload-stream-btn" class="settings-button" title="${t('tooltips.reloadStream')}"><i class="fas fa-sync"></i></button>
                    <button id="settings-btn" class="settings-button"><i class="fas fa-cog"></i></button>
                </div>
            </div>
            ${inviteLinksHtml}
            <div class="sidebar-media-controls">
                <button id="toggle-mic-btn" class="control-btn" title="${t('tooltips.toggleLocalMic')}">
                    <i class="fas fa-microphone"></i>
                </button>
                <button id="toggle-video-btn" class="control-btn" title="${t('tooltips.toggleLocalWebcam')}">
                    <i class="fas fa-video"></i>
                </button>
                <div class="iframe-audio-controls">
                    <button id="iframe-mute-btn" class="control-btn" title="${t('tooltips.toggleSessionAudio')}">
                        <i class="fas fa-volume-up"></i>
                    </button>
                    <input type="range" id="iframe-volume-slider" min="0" max="1" step="0.01" value="1" title="${t('tooltips.sessionVolume')}">
                </div>
            </div>
            <div id="sidebar-main-content" class="sidebar-content"></div>
            <div id="chat-reply-banner"></div>
            <div id="chat-form-container">
                <form id="chat-form">
                    <input type="text" id="chat-input" placeholder="${t('chat.inputPlaceholder')}" autocomplete="off" maxlength="500">
                    <button type="submit"><i class="fas fa-paper-plane"></i></button>
                </form>
            </div>`;
        
        applyTranslations(sidebarEl, t);

        if (isController || isParticipant) {
            document.querySelectorAll('.copy-link-btn').forEach(btn => {
                btn.addEventListener('click', (e) => {
                    const targetId = e.currentTarget.dataset.target;
                    const input = document.getElementById(targetId);
                    navigator.clipboard.writeText(input.value).then(() => {
                        const originalIcon = btn.innerHTML;
                        btn.innerHTML = '<i class="fas fa-check"></i>';
                        setTimeout(() => { btn.innerHTML = originalIcon; }, 2000);
                    });
                });
            });
        }

        if (isController) initStartMenu();
 
        if (isController) {
            initGamepadControls();
        } else if (isParticipant) {
             initGamepadControls();
        }

        if (COLLAB_DATA.userPermission === 'readonly') {
            const mediaControls = document.querySelector('.sidebar-media-controls');
            if (mediaControls) {
                mediaControls.classList.add('readonly-view');
                mediaControls.querySelector('#toggle-mic-btn').style.display = 'none';
                mediaControls.querySelector('#toggle-video-btn').style.display = 'none';
            }
            const settingsBtn = document.querySelector('#settings-btn');
            if (settingsBtn) {
                settingsBtn.style.display = 'none';
            }
        }

        toggleMicBtn = document.getElementById('toggle-mic-btn');
        toggleVideoBtn = document.getElementById('toggle-video-btn');
        iframeMuteBtn = document.getElementById('iframe-mute-btn');
        iframeVolumeSlider = document.getElementById('iframe-volume-slider');
        toggleMicBtn.addEventListener('click', () => handleMediaToggle('mic'));
        toggleVideoBtn.addEventListener('click', () => handleMediaToggle('video'));
        updateMediaButtonUI();

        const gameIframe = document.getElementById('session-frame');
        iframeVolumeSlider.value = isIframeMuted ? 0 : lastKnownVolume;
        if (isIframeMuted) iframeMuteBtn.querySelector('i').className = 'fas fa-volume-mute';

        if (gameIframe) {
            gameIframe.addEventListener('load', sendVolumeToIframe);
        }

        iframeMuteBtn.addEventListener('click', () => {
            isIframeMuted = !isIframeMuted;
            sendVolumeToIframe();
            iframeMuteBtn.querySelector('i').className = isIframeMuted ? 'fas fa-volume-mute' : 'fas fa-volume-up';
            iframeVolumeSlider.value = isIframeMuted ? 0 : lastKnownVolume;
        });

        iframeVolumeSlider.addEventListener('input', (e) => {
            const newVolume = parseFloat(e.target.value);

            if (newVolume > 0) {
                lastKnownVolume = newVolume;
                localStorage.setItem(`collab_iframe_volume`, lastKnownVolume);
                isIframeMuted = false;
                iframeMuteBtn.querySelector('i').className = 'fas fa-volume-up';
            } else {
                isIframeMuted = true;
                iframeMuteBtn.querySelector('i').className = 'fas fa-volume-mute';
            }
            sendVolumeToIframe();
        });

        const gamingModeBtn = sidebarEl.querySelector('#gaming-mode-btn');
        if (gamingModeBtn) {
            gamingModeBtn.addEventListener('click', () => {
                if (document.fullscreenElement) {
                    if (document.exitFullscreen) {
                        document.exitFullscreen().catch(err => console.error(err));
                    }
                } else {
                    const iframe = document.getElementById('session-frame');
                    if (iframe && iframe.contentWindow) {
                        iframe.contentWindow.postMessage({ type: 'requestFullscreen' }, window.location.origin);
                        iframe.focus(); 
                    }
                    if (COLLAB_DATA.userRole !== 'controller' && ws && ws.readyState === WebSocket.OPEN) {
                        ws.send(JSON.stringify({ action: 'force_cursor_render', state: 1 }));
                    }
                }
            });
        }

        sidebarEl.querySelector('.theme-toggle').addEventListener('click', toggleTheme);
        sidebarEl.querySelector('#reload-stream-btn').addEventListener('click', () => {
            const iframe = document.getElementById('session-frame');
            if (iframe) {
                if (iframe.getAttribute('src') === 'about:blank' && iframe.dataset.src) {
                    iframe.src = iframe.dataset.src;
                } else {
                    const currentSrc = new URL(iframe.src);
                    currentSrc.searchParams.set('t', Date.now());
                    iframe.src = currentSrc.toString();
                }
            }
        });
        sidebarEl.querySelector('#settings-btn').addEventListener('click', () => {
            unlockAllAudio();
            populateDeviceLists();
            settingsModalOverlay.classList.remove('hidden')
        });
        sidebarEl.querySelector('#chat-form').addEventListener('submit', handleChatSubmit);
        document.getElementById('sidebar-main-content').innerHTML = '<div id="chat-messages"></div>';
        document.getElementById('sidebar-main-content').addEventListener('click', handleChatAreaClick);
    };
   
    const initStartMenu = () => {
        const btn = document.getElementById('start-menu-btn');
        const modal = document.getElementById('start-menu-modal');
        if(!btn || !modal) return;

        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            if (modal.classList.contains('hidden')) {
                modal.classList.remove('hidden');
                ws.send(JSON.stringify({ action: 'get_apps' }));
            } else {
                modal.classList.add('hidden');
            }
        });

        document.addEventListener('click', (e) => {
            if (!modal.classList.contains('hidden') && !modal.contains(e.target) && !btn.contains(e.target)) {
                modal.classList.add('hidden');
            }
        });

        const tabs = modal.querySelectorAll('.sm-tab-btn');
        tabs.forEach(tab => {
            tab.addEventListener('click', () => {
                tabs.forEach(t => t.classList.remove('active'));
                tab.classList.add('active');
                modal.querySelectorAll('.sm-view').forEach(v => v.classList.remove('active'));
                document.getElementById(`sm-view-${tab.dataset.tab}`).classList.add('active');
            });
        });

        document.getElementById('sm-app-search').addEventListener('input', (e) => {
            renderStartMenu(e.target.value);
        });
    };

    const renderStartMenu = (filter = '') => {
        const searchInput = document.getElementById('sm-app-search');
        const currentFilter = filter || (searchInput ? searchInput.value : '');
        const launchGrid = document.getElementById('sm-app-grid');
        const activeList = document.getElementById('sm-active-list');
        
        if (launchGrid) launchGrid.innerHTML = '';
        if (activeList) activeList.innerHTML = '';

        const filteredApps = availableAppsList.filter(app => app.name.toLowerCase().includes(currentFilter.toLowerCase()));

        filteredApps.forEach(app => {
            const card = document.createElement('div');
            card.className = 'sm-app-card';
            
            if (app.running) card.classList.add('running');
            if (app.active) card.classList.add('active');

            if (pendingActions.has(`swap_${app.id}`)) {
                card.classList.add('pending');
                card.innerHTML = `<div class="spinner"></div><span>${t('startMenu.loading')}</span>`;
            } else {
                const showOverlay = app.running && !app.active;
                const overlay = showOverlay ? `<div class="running-overlay"><i class="fas fa-exchange-alt"></i></div>` : '';
                
                const iconHtml = app.logo ? `<img src="${app.logo}" alt="${app.name}">` : `<div class="app-icon-fallback"><i class="fas fa-image"></i></div>`;

                card.innerHTML = `${overlay}${iconHtml}<span>${app.name}</span>`; 
                if (!app.active) {
                    card.onclick = (e) => {
                        e.stopPropagation();
                        pendingActions.add(`swap_${app.id}`);
                        renderStartMenu(currentFilter);
                        ws.send(JSON.stringify({ action: 'swap_app', app_id: app.id }));
                    };
                }
            }
            if (launchGrid) launchGrid.appendChild(card);
        });

        const runningApps = availableAppsList.filter(app => app.running);
        runningApps.forEach(app => {
            const item = document.createElement('div');
            item.className = 'sm-session-item';
            
            const stopBtn = app.active 
                ? `<button class="sm-btn-action sm-btn-stop" disabled title="${t('tooltips.cannotStopActive')}"><i class="fas fa-ban"></i></button>`
                : `<button class="sm-btn-action sm-btn-stop" data-action="stop" data-id="${app.id}" title="${t('tooltips.stopApp')}"><i class="fas fa-stop"></i></button>`;
            
            const swapBtn = !app.active
                ? `<button class="sm-btn-action sm-btn-swap" data-action="swap" data-id="${app.id}" title="${t('tooltips.swapApp')}"><i class="fas fa-exchange-alt"></i></button>`
                : '';

            let actionButtons = '';
            if (pendingActions.has(`app_${app.id}`)) {
                actionButtons = `<div class="spinner"></div>`;
            } else {
                actionButtons = `${swapBtn} <button class="sm-btn-action sm-btn-restart" data-action="restart" data-id="${app.id}" title="${t('tooltips.restartApp')}"><i class="fas fa-redo"></i></button> ${stopBtn}`;
            }

            const iconHtml = app.logo ? `<img src="${app.logo}">` : `<div class="app-icon-fallback"><i class="fas fa-image"></i></div>`; 
            item.innerHTML = `
                ${iconHtml}
                <div class="sm-session-info">
                    <div class="sm-session-name">${app.name}</div>
                    <div class="sm-session-status">${app.active ? t('startMenu.activeVisible') : t('startMenu.runningBackground')}</div>
                </div>
                <div class="sm-session-actions">
                    ${actionButtons}
                </div>
            `;
            item.querySelectorAll('button').forEach(b => b.onclick = (e) => {
                e.stopPropagation();
                handleSessionAction(e.currentTarget.dataset.action, app.id, app.name);
            });
            if (activeList) activeList.appendChild(item);
        });
    };

    const handleSessionAction = (action, appId, appName) => {
        if (action === 'swap') {
            pendingActions.add(`swap_${appId}`);
            renderStartMenu();
            ws.send(JSON.stringify({ action: 'swap_app', app_id: appId }));
        } else if (action === 'stop') {
            pendingActions.add(`app_${appId}`);
            renderStartMenu();
            ws.send(JSON.stringify({ action: 'stop_app', app_id: appId }));
        } else if (action === 'restart') {
            pendingActions.add(`app_${appId}`);
            renderStartMenu();
            ws.send(JSON.stringify({ action: 'restart_app', app_id: appId }));
        }
    };
 
    const escapeHTML = (str) => str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#039;');

    const linkify = (text) => {
        const urlRegex = /(\b(https?|ftp|file):\/\/[-A-Z0-9+&@#\/%?=~_|!:,.;]*[-A-Z0-9+&@#\/%=~_|])/ig;
        return text.replace(urlRegex, (url) => `<a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>`);
    };

    const createMessageHTML = (data) => {
        const isSelf = data.sender === username || (COLLAB_DATA.userRole === 'controller' && data.sender === 'Controller');
        const senderName = isSelf ? t('chat.selfUsername') : escapeHTML(data.sender);
        
        let replyHTML = '';
        if (data.replyTo && messageStore[data.replyTo]) {
            const originalMessage = messageStore[data.replyTo];
            const originalSender = escapeHTML(originalMessage.sender);
            const originalMessageSnippet = escapeHTML(originalMessage.message.substring(0, 70)) + (originalMessage.message.length > 70 ? '...' : '');
            replyHTML = `
                <div class="reply-quote">
                    <span class="reply-sender">${originalSender}</span>
                    <span class="reply-content">${originalMessageSnippet}</span>
                </div>
            `;
        }
        
        const sanitizedMessage = escapeHTML(data.message);
        const linkedMessage = linkify(sanitizedMessage);
        const timestamp = new Date(data.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

        return `
            <div class="chat-message" data-message-id="${data.messageId}">
                <div class="message-content">
                    ${replyHTML}
                    <div class="sender-info">
                        <span class="sender">${senderName}</span>
                        <span class="timestamp">${timestamp}</span>
                    </div>
                    <div class="bubble">${linkedMessage}</div>
                    <div class="message-actions">
                        <button class="reply-btn" title="${t('tooltips.reply')}"><i class="fas fa-reply"></i></button>
                    </div>
                </div>
            </div>
        `;
    };

    const appendChatMessage = (data, type) => {
        const messagesContainer = document.getElementById('chat-messages');
        if (!messagesContainer) return;

        const scrollContainer = document.getElementById('sidebar-main-content');
        if (!scrollContainer) return;

        const isScrolledToBottom = scrollContainer.scrollHeight - scrollContainer.clientHeight <= scrollContainer.scrollTop + 50;

        const msgEl = document.createElement('div');
        let isOwnMessage = false;

        if (type === 'chat') {
            isOwnMessage = data.sender === username || (COLLAB_DATA.userRole === 'controller' && data.sender === 'Controller');
            msgEl.innerHTML = createMessageHTML(data);
        } else {
            let content = '';
            switch (data.type) {
                case 'user_joined': content = t('systemMessages.userJoined', { username: escapeHTML(data.username) }); break;
                case 'user_left': content = t('systemMessages.userLeft', { username: escapeHTML(data.username) }); break;
                case 'username_changed': content = t('systemMessages.usernameChanged', { old_username: escapeHTML(data.old_username), new_username: escapeHTML(data.new_username) }); break;
                case 'gamepad_change': content = data.message; break;
                case 'mk_change': content = data.message; break;
            }
            msgEl.className = 'system-message';
            msgEl.innerHTML = `<span>${content}</span>`;
        }
        
        messagesContainer.appendChild(msgEl);

        if (isScrolledToBottom) {
            scrollContainer.scrollTop = scrollContainer.scrollHeight;
        }

        if (type === 'chat' && !isOwnMessage) {
            playNotificationSound();
            if (!isSidebarVisible) {
                showToast(data);
            }
        }
    };

    const handleUsernameSubmit = async (e) => {
        e.preventDefault();
        const input = document.getElementById('username-input');
        const newUsername = input.value.trim();
        if (newUsername) {
            unlockAllAudio();
            username = newUsername;
            localStorage.setItem('collab_username', username);
            sessionStorage.setItem('collab_hasJoined_' + COLLAB_DATA.sessionId, 'true');
            ws.send(JSON.stringify({ action: 'set_username', username: username }));
            renderSidebar();

            if (!mediaInitialized) {
                await startMedia();
            }
            
            isMicOn = true;
            if (localStream) localStream.getAudioTracks().forEach(t => t.enabled = true);
            sendControlMessage('audio_state', true);
            updateMediaButtonUI();

            await new Promise(r => setTimeout(r, 1000));

            isWebcamOn = true;
            if (localStream && localStream.getVideoTracks().length > 0) {
                localStream.getVideoTracks().forEach(t => t.enabled = true);
                localContainer.style.display = 'flex';
                sendControlMessage('video_state', true);
            }
            updateMediaButtonUI();

            toggleSidebar();
        }
    };

    const handleChatSubmit = (e) => {
        e.preventDefault();
        const input = document.getElementById('chat-input');
        if (input.value.trim()) {
            const payload = { 
                action: 'send_chat_message', 
                message: input.value.trim() 
            };
            if (replyingTo) {
                payload.replyTo = replyingTo.messageId;
            }
            ws.send(JSON.stringify(payload));
            input.value = '';
            cancelReply();
        }
    };

    const handleChatAreaClick = (e) => {
        const replyBtn = e.target.closest('.reply-btn');
        if (replyBtn) {
            const messageEl = e.target.closest('.chat-message');
            const messageId = messageEl.dataset.messageId;
            if (messageStore[messageId]) {
                replyingTo = messageStore[messageId];
                renderReplyBanner();
            }
        }
    };

    const renderReplyBanner = () => {
        const banner = document.getElementById('chat-reply-banner');
        if (!banner) return;
        if (replyingTo) {
            banner.style.display = 'flex';
            banner.innerHTML = `
                <span class="reply-target-text">${t('chat.replyingTo', { sender: escapeHTML(replyingTo.sender) })}</span>
                <button id="cancel-reply-btn" title="${t('tooltips.cancelReply')}">&times;</button>
            `;
            document.getElementById('cancel-reply-btn').addEventListener('click', cancelReply);
        } else {
            banner.style.display = 'none';
            banner.innerHTML = '';
        }
    };

    const cancelReply = () => {
        replyingTo = null;
        renderReplyBanner();
    };
    
    const closeModal = () => settingsModalOverlay.classList.add('hidden');
    
    const updateMediaButtonUI = () => {
        if (!toggleMicBtn || !toggleVideoBtn) return;

        toggleMicBtn.classList.toggle('inactive', !isMicOn);
        toggleMicBtn.querySelector('i').className = isMicOn ? 'fas fa-microphone' : 'fas fa-microphone-slash';

        toggleVideoBtn.classList.toggle('inactive', !isWebcamOn);
        toggleVideoBtn.querySelector('i').className = isWebcamOn ? 'fas fa-video' : 'fas fa-video-slash';
    };

    const playNotificationSound = () => {
        if (!notificationAudioCtx || notificationAudioCtx.state !== 'running') return;
        const oscillator = notificationAudioCtx.createOscillator();
        const gainNode = notificationAudioCtx.createGain();
        oscillator.connect(gainNode);
        gainNode.connect(notificationAudioCtx.destination);
        
        oscillator.type = 'sine';
        oscillator.frequency.setValueAtTime(880, notificationAudioCtx.currentTime);
        gainNode.gain.setValueAtTime(0.1, notificationAudioCtx.currentTime);
        
        gainNode.gain.exponentialRampToValueAtTime(0.00001, notificationAudioCtx.currentTime + 0.1);
        oscillator.start(notificationAudioCtx.currentTime);
        oscillator.stop(notificationAudioCtx.currentTime + 0.1);
    };

    const showToast = (data) => {
        const toast = document.createElement('div');
        toast.className = 'toast';
        toast.innerHTML = `
            <div class="toast-sender">${escapeHTML(data.sender)}</div>
            <div class="toast-message">${linkify(escapeHTML(data.message))}</div>
        `;
        toast.addEventListener('click', () => {
            if (!isSidebarVisible) {
                toggleSidebar();
            }
            toast.classList.add('closing');
        });
        toastContainer.appendChild(toast);

        setTimeout(() => {
            toast.classList.add('closing');
            toast.addEventListener('animationend', () => toast.remove());
        }, 5000);
    };

    const initGamepadControls = () => {
        const sourceBox = document.getElementById('gamepad-source-box');
        if (!sourceBox) return;

        sourceBox.innerHTML = '';
        gamepadIcons = {};

        mkIcon = document.createElement('div');
        mkIcon.id = 'mk-icon';
        mkIcon.className = 'gamepad-icon mk-icon';
        if (COLLAB_DATA.userRole === 'controller') {
            mkIcon.classList.add('draggable');
            mkIcon.draggable = true;
        }
        mkIcon.innerHTML = `<i class="fas fa-keyboard"></i><i class="fas fa-mouse" style="margin-left: 3px; font-size: 0.8em;"></i>`;

        if (COLLAB_DATA.userRole === 'controller') {
            document.getElementById('local-user-container').appendChild(mkIcon);
        } else {
            sourceBox.appendChild(mkIcon);
        }

        for (let i = 1; i <= GAMEPAD_COUNT; i++) {
            const icon = document.createElement('div');
            icon.id = `gamepad-icon-${i}`;
            icon.className = 'gamepad-icon';
            if (COLLAB_DATA.userRole === 'controller') {
                icon.classList.add('draggable');
                icon.draggable = true;
            }
            icon.dataset.gamepadId = i;
            icon.innerHTML = `<i class="fas fa-gamepad"></i><span class="gamepad-number">${i}</span>`;
            gamepadIcons[i] = icon;
            sourceBox.appendChild(icon);
        }
    };

    const updateGamepadIcons = (users) => {
        const sourceBox = document.getElementById('gamepad-source-box');
        if (!sourceBox || Object.keys(gamepadIcons).length === 0) return;

        const assignedGamepadIds = new Set();
        let mkAssigned = false;
        users.forEach(user => {
            if (user.slot) {
                assignedGamepadIds.add(user.slot);
                const icon = gamepadIcons[user.slot];
                const container = user.token === COLLAB_DATA.userToken
                    ? document.getElementById('local-user-container')
                    : document.getElementById(`container-${user.token}`);

                if (icon && container && icon.parentElement !== container) {
                    container.appendChild(icon);
                }
            }

            if (user.has_mk) {
                mkAssigned = true;
                const container = user.token === COLLAB_DATA.userToken
                    ? document.getElementById('local-user-container')
                    : document.getElementById(`container-${user.token}`);

                if (mkIcon && container && mkIcon.parentElement !== container) {
                    container.appendChild(mkIcon);
                }
            }
        });

        for (let i = 1; i <= GAMEPAD_COUNT; i++) {
            if (!assignedGamepadIds.has(i)) {
                const icon = gamepadIcons[i];
                if (icon && icon.parentElement !== sourceBox) {
                    sourceBox.appendChild(icon);
                }
            }
        }

        if (!mkAssigned && mkIcon && mkIcon.parentElement !== sourceBox) {
            if (COLLAB_DATA.userRole === 'controller') {
                const localContainer = document.getElementById('local-user-container');
                if (localContainer) localContainer.appendChild(mkIcon);
            } else {
                sourceBox.appendChild(mkIcon);
            }
        }
    };

    let draggedElement = null;

    videoGrid.addEventListener('dragstart', (e) => {
        const target = e.target.closest('.draggable, .reorderable');
        if (!target) {
            e.preventDefault();
            return;
        }
        draggedElement = target;

        if (target.classList.contains('gamepad-icon')) {
            if (target.classList.contains('mk-icon')) {
                document.body.classList.add('dragging-mk');
                e.dataTransfer.setData('type', 'mk');
            } else {
                document.body.classList.add('dragging-gamepad');
                e.dataTransfer.setData('type', 'gamepad');
                e.dataTransfer.setData('text/plain', target.dataset.gamepadId);
            }
            e.dataTransfer.effectAllowed = 'move';
            
            setTimeout(() => target.classList.add('dragging'), 0);
            
            document.querySelectorAll('.video-container').forEach(container => {
                const userToken = container.dataset.userToken;
                const user = currentUserState.find(u => u.token === userToken);
                if (container.id === 'gamepad-source-box' || user) {
                    container.classList.add('can-drop-gamepad');
                }
            });
        } else if (target.classList.contains('reorderable')) {
            document.body.classList.add('dragging-stream');
            e.dataTransfer.setData('text/plain', target.id);
            e.dataTransfer.effectAllowed = 'move';
            setTimeout(() => target.classList.add('reordering'), 0);
        }
    });

    videoGrid.addEventListener('dragend', (e) => {
        document.body.className = '';
        draggedElement?.classList.remove('dragging', 'reordering');
        document.querySelectorAll('.can-drop-gamepad').forEach(el => el.classList.remove('can-drop-gamepad'));
        draggedElement = null;
    });

    videoGrid.addEventListener('dragover', (e) => {
        e.preventDefault();
        const dropTarget = e.target.closest('.video-container');

        if (document.body.classList.contains('dragging-gamepad') || document.body.classList.contains('dragging-mk')) {
            if (dropTarget && dropTarget.classList.contains('can-drop-gamepad')) {
                e.dataTransfer.dropEffect = 'move';
            } else {
                e.dataTransfer.dropEffect = 'none';
            }
        } else if (document.body.classList.contains('dragging-stream')) {
            if (dropTarget && !dropTarget.classList.contains('pinned') && dropTarget !== draggedElement) {
                const rect = dropTarget.getBoundingClientRect();
                const offsetX = e.clientX - rect.left;
                if (offsetX < rect.width / 2) {
                    dropTarget.parentNode.insertBefore(draggedElement, dropTarget);
                } else {
                    dropTarget.parentNode.insertBefore(draggedElement, dropTarget.nextSibling);
                }
            }
        }
    });

    videoGrid.addEventListener('drop', (e) => {
        e.preventDefault();
        const isGamepad = document.body.classList.contains('dragging-gamepad');
        const isMk = document.body.classList.contains('dragging-mk');

        if (!isGamepad && !isMk) return;

        const dropTarget = e.target.closest('.video-container.can-drop-gamepad');
        if (!dropTarget) return;

        if (isMk) {
            const userToken = dropTarget.dataset.userToken;
            const tokenToAssign = (dropTarget.id === 'gamepad-source-box') ? COLLAB_DATA.userToken : userToken;
            ws.send(JSON.stringify({ action: 'assign_mk', token: tokenToAssign }));
        } else {
            const gamepadId = parseInt(e.dataTransfer.getData('text/plain'), 10);
            const draggedIcon = document.getElementById(`gamepad-icon-${gamepadId}`);
            if (dropTarget.id === 'gamepad-source-box') {
                const parentContainer = draggedIcon.parentElement;
                if (parentContainer && parentContainer.id !== 'gamepad-source-box') {
                    const userToken = parentContainer.dataset.userToken;
                    if (userToken) ws.send(JSON.stringify({ action: 'assign_slot', viewer_token: userToken, slot: null }));
                }
            } else {
                const userToken = dropTarget.dataset.userToken;
                if (userToken) ws.send(JSON.stringify({ action: 'assign_slot', viewer_token: userToken, slot: gamepadId }));
            }
        }
    });

    let isBouncing = false;
    videoGrid.addEventListener('wheel', e => {
        if (videoGrid.scrollWidth > videoGrid.clientWidth) {
            e.preventDefault();
            videoGrid.scrollLeft += e.deltaY;
        } else {
            if (isBouncing || Math.abs(e.deltaY) < 5) return;
            e.preventDefault();
            const bounceAmount = 20;
            const direction = e.deltaY > 0 ? -1 : 1;

            isBouncing = true;
            videoGridContent.style.transform = `translateX(${bounceAmount * direction}px)`;
            
            setTimeout(() => {
                videoGridContent.style.transform = 'translateX(0)';
                setTimeout(() => { isBouncing = false; }, 150);
            }, 150);
        }
    });

    toggleHandle.addEventListener('click', toggleSidebar);

    if (videoToggleHandle) {
        videoToggleHandle.addEventListener('click', toggleVideoGrid);
    }
    
    initGestures();

    settingsModalCloseBtn.addEventListener('click', closeModal);
    settingsModalOverlay.addEventListener('click', (e) => {
        if (e.target === settingsModalOverlay) closeModal();
    });

    audioInputSelect.addEventListener('change', (e) => {
        preferredMicId = e.target.value;
        localStorage.setItem('collab_preferredMicId', preferredMicId);
        if(mediaInitialized) startMedia();
    });
    videoInputSelect.addEventListener('change', (e) => {
        preferredCamId = e.target.value;
        localStorage.setItem('collab_preferredCamId', preferredCamId);
        if(mediaInitialized) startMedia();
    });
    
    videoGrid.addEventListener('click', (e) => {
        const btn = e.target.closest('.remote-control-btn');
        if (!btn) return;
    
        unlockAllAudio();
        const token = btn.dataset.token;
        const stream = remoteStreams[token];
    
        if (btn.classList.contains('mute-audio')) {
            if (!stream) return;
            stream.audioMuted = !stream.audioMuted;
            if (stream.audioContext) {
                if (stream.audioMuted && stream.audioContext.state === 'running') {
                    stream.audioContext.suspend();
                } else if (!stream.audioMuted && stream.audioContext.state === 'suspended') {
                    stream.audioContext.resume();
                }
            }
            btn.classList.toggle('inactive', stream.audioMuted);
            btn.querySelector('i').className = stream.audioMuted ? 'fas fa-microphone-slash' : 'fas fa-microphone';
        } else if (btn.classList.contains('mute-video')) {
            if (!stream) return;
            stream.videoMuted = !stream.videoMuted;
            btn.classList.toggle('inactive', stream.videoMuted);
            btn.querySelector('i').className = stream.videoMuted ? 'fas fa-video-slash' : 'fas fa-video';
            
            if (stream.videoMuted) {
                // No further frames are decoded while muted, so the <video> would
                // otherwise sit frozen on the last one: hide it and blank the canvas.
                // The next frame after unmuting reveals the sink again.
                hideRemoteSink(stream);
                stream.ctx.fillStyle = '#222';
                stream.ctx.fillRect(0, 0, stream.canvas.width, stream.canvas.height);
            } else {
                stream.hasReceivedKeyFrame = false;
            }
        } else if (btn.classList.contains('resize-to-client')) {
            const res = clientResolutions[token];
            if (!res) {
                console.warn(`[Controller] No resolution data available for client ${token}`);
                return;
            }
            console.log(`[Controller] Manually resizing to client ${token}: ${res.width}x${res.height}`);
            const iframe = document.getElementById('session-frame');
            if (iframe && iframe.contentWindow) {
                iframe.contentWindow.postMessage({ type: 'setManualResolution', width: res.width, height: res.height }, window.location.origin);
            }
        } else if (btn.classList.contains('toggle-resolution-lock')) {
            isResolutionLocked = !isResolutionLocked;
            btn.querySelector('i').className = isResolutionLocked ? 'fas fa-lock' : 'fas fa-lock-open';
            console.log(`[Controller] Resolution Lock is now ${isResolutionLocked}`);
            if (!isResolutionLocked) {
                applyAutoResolution();
            }
        } else if (btn.classList.contains('designate-speaker')) {
            const tokenToSet = (currentDesignatedSpeaker === token) ? null : token;
            ws.send(JSON.stringify({ action: 'set_designated_speaker', token: tokenToSet }));
        }
    });

    applyTranslations(document.body, t);
    renderSidebar();
    connectWebSocket();
    updateSpeakingIndicators();

    document.body.addEventListener('click', initNotificationAudio, { once: true });
    document.body.addEventListener('keydown', initNotificationAudio, { once: true });

    if (window.history.replaceState) {
        const url = new URL(window.location);
        url.searchParams.delete('token');
        url.searchParams.delete('access_token');
        window.history.replaceState({ path: url.href }, '', url.href);
    }

    const iframeEl = document.getElementById('session-frame');
    if (iframeEl) {
        const resizeObserver = new ResizeObserver(() => {
            if (ws && ws.readyState === WebSocket.OPEN) {
                const width = iframeEl.clientWidth;
                const height = iframeEl.clientHeight;
                ws.send(JSON.stringify({ action: 'client_resolution', width, height }));
                if (COLLAB_DATA.userRole === 'controller') {
                    clientResolutions[COLLAB_DATA.userToken] = { width, height };
                    if (!isResolutionLocked && currentMkOwner === COLLAB_DATA.userToken) {
                        applyAutoResolution();
                    }
                }
            }
        });
        resizeObserver.observe(iframeEl);
    }

    document.addEventListener('fullscreenchange', () => {
        if (!document.fullscreenElement && COLLAB_DATA.userRole !== 'controller' && ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ action: 'force_cursor_render', state: 0 }));
        }
    });

    setTimeout(toggleSidebar, 500);
});
