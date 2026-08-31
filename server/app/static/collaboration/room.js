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

// ---------------------------------------------------------------------------
// Camera orientation. Nothing downstream re-orients a frame (the receiver
// paints whatever arrives), so the sender has to hand the encoder upright
// pixels. Chromium stamps each VideoFrame with rotation/flip and applies them
// in drawImage. Mobile WebKit delivers sensor-fixed frames with no metadata
// at all, and the only thing that says which way is up is the window
// orientation relative to the one the sensor is upright in.
// ---------------------------------------------------------------------------

const hasFrameOrientation = typeof VideoFrame !== 'undefined'
    && typeof VideoFrame.prototype === 'object'
    && 'rotation' in VideoFrame.prototype;

const SENSOR_UPRIGHT_ORIENTATION = -90;

const canDeriveOrientation = () =>
    !hasFrameOrientation && typeof window.orientation === 'number';

const deriveRotation = () =>
    ((window.orientation - SENSOR_UPRIGHT_ORIENTATION) % 360 + 360) % 360;

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
    // Settles the page's rung choice before any frame: on failure it falls
    // through to its own processor in the same call instead of erroring later.
    self.postMessage({ type: 'sourceStarted', id: id });
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
    constructor() {
        super();
        // Handed a port, channel data goes straight to its holder (the socket
        // worker) instead of through the page.
        this.out = this.port;
        this.port.onmessage = (e) => {
            if (e.data && e.data.port) this.out = e.data.port;
        };
    }
    process(inputs) {
        const input = inputs[0];
        if (input && input.length > 0 && input[0] && input[0].length > 0) {
            this.out.postMessage(input);
        }
        return true;
    }
});
`;

let mediaWorker = null;
let mediaWorkerProbe = null;
let mediaWorkerSeq = 0;
// Set once the worker's processor failed to read a transferred track: a second
// track would fail the same way, so the worker capture rung is not offered again.
let mediaWorkerTrackUnsupported = false;
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
                case 'sourceStarted': {
                    const source = mediaWorkerSources.get(m.id);
                    if (source) source.onStart();
                    return;
                }
                case 'sourceEnd':
                case 'sourceFailed': {
                    if (m.type === 'sourceFailed') mediaWorkerTrackUnsupported = true;
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
// Resolves null when the worker cannot read the track, so the caller can take a
// page rung in the same call instead of erroring at the first read.
const createWorkerTrackProcessor = async (track) => {
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
    let settleStart = null;
    const started = new Promise((resolve) => { settleStart = resolve; });
    const readable = new ReadableStream({
        start(controller) {
            streamController = controller;
            mediaWorkerSources.set(id, {
                onStart() { settleStart(true); },
                onFrame(frame) {
                    if (stopped) { frame.close(); return; }
                    // Enqueue only what the encoder is keeping up with.
                    if (controller.desiredSize !== null && controller.desiredSize <= 0) frame.close();
                    else controller.enqueue(frame);
                    if (mediaWorker) mediaWorker.postMessage({ type: 'sourceAck', id });
                },
                onEnd(failed) {
                    settleStart(false);
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

    if (!(await started)) return null;
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
// A worker reader keeps every frame off the page's thread, so it is asked first;
// the page's own processor is the rung below it, not above. The standard
// processor is worker-only and video-only, so audio starts at the page rung.
const createTrackProcessor = async (track) => {
    if (track.kind === 'video' && !mediaWorkerTrackUnsupported) {
        const caps = await ensureMediaWorker();
        if (caps && caps.processor) {
            const processor = await createWorkerTrackProcessor(track);
            if (processor) {
                logMediaPath('video capture: MediaStreamTrackProcessor in the media worker.');
                return processor;
            }
        }
    }
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
// The worker's VideoTrackGenerator outranks the page's MediaStreamTrackGenerator,
// so the worker is asked first and the page generator is taken only once it has
// reported it holds no generator of its own.
const createVideoSink = async (onFail) => {
    const caps = await ensureMediaWorker();
    if (caps && caps.generator) {
        const sink = await createWorkerVideoSink(onFail);
        if (sink) {
            logMediaPath('video sink: VideoTrackGenerator in the media worker.');
            return sink;
        }
    }
    if (hasWindowTrackGenerator) {
        const sink = createWindowVideoSink(onFail);
        if (sink) {
            logMediaPath('video sink: MediaStreamTrackGenerator on the page.');
            return sink;
        }
    }
    logMediaPath('video sink: 2D canvas - no track generator in this browser, so every '
        + 'frame is drawn by hand instead of being handed to a <video> element.');
    return null;
};

const MSG_TYPE = {
    VIDEO_FRAME: 0x01,
    AUDIO_FRAME: 0x02,
    VIDEO_CONFIG: 0x03,
    PCM_FRAME: 0x04,
};

// ---------------------------------------------------------------------------
// Room socket in a worker. The page's thread is otherwise between the socket
// and every remote voice: anything occupying it -- a getUserMedia prompt, a
// start-menu render -- stops audio being delivered for as long as it lasts.
// Here the worker owns the WebSocket, decodes AUDIO_FRAME (Opus) itself, and
// hands PCM straight down a port to the speaking tile's worklet; PCM_FRAME is
// upsampled the same way. Everything else -- JSON control, video -- reaches
// the page unchanged, and sends keep their order through the worker's port.
// The microphone runs here too: capture frames arrive over a transferred
// track-processor stream or the capture worklet's port, are encoded in this
// worker, and leave framed -- so a stalled page delays outgoing voice no
// more than incoming.
// Gecko still routes a worker's WebSocket delivery through the page's main
// thread, so there a page stall costs what the playback buffer cannot cover;
// Chromium and WebKit deliver to the worker directly.
// ---------------------------------------------------------------------------

const ROOM_SOCKET_WORKER_SRC = `
const AUDIO_FRAME = ${MSG_TYPE.AUDIO_FRAME};
const PCM_FRAME = ${MSG_TYPE.PCM_FRAME};
let ws = null;
const ports = new Map();
const muted = new Set();
const decoders = new Map();
const idDecoder = new TextDecoder();
const hasAudioDecoder = (typeof AudioDecoder !== 'undefined');

function portFor(publicId) {
    const port = ports.get(publicId);
    return (port && !muted.has(publicId)) ? port : null;
}

function decoderFor(publicId) {
    let dec = decoders.get(publicId);
    if (dec && dec.state === 'configured') return dec;
    if (dec) { try { dec.close(); } catch (e) {} }
    dec = new AudioDecoder({
        output: (frame) => {
            const port = portFor(publicId);
            if (!port) { frame.close(); return; }
            const pcm = new Float32Array(frame.allocationSize({ planeIndex: 0, format: 'f32' }) / 4);
            frame.copyTo(pcm, { planeIndex: 0, format: 'f32' });
            frame.close();
            port.postMessage(pcm, [pcm.buffer]);
        },
        // A fresh decoder recovers on the next packet.
        error: () => { decoders.delete(publicId); },
    });
    dec.configure({ codec: 'opus', sampleRate: 48000, numberOfChannels: 1 });
    decoders.set(publicId, dec);
    return dec;
}

// True when the frame was consumed here; anything else goes to the page.
function divertAudio(data) {
    if (data.byteLength < 10) return false;
    const type = new Uint8Array(data, 8, 1)[0];
    if (type !== AUDIO_FRAME && type !== PCM_FRAME) return false;
    const publicId = idDecoder.decode(new Uint8Array(data, 0, 8));
    if (!ports.has(publicId)) return false;
    if (muted.has(publicId)) return true;
    if (type === AUDIO_FRAME) {
        if (!hasAudioDecoder) return false;
        try {
            decoderFor(publicId).decode(new EncodedAudioChunk({
                type: 'key', timestamp: performance.now() * 1000, data: data.slice(10) }));
        } catch (err) {
            const dec = decoders.get(publicId);
            if (dec) { decoders.delete(publicId); try { dec.close(); } catch (e) {} }
        }
        return true;
    }
    // 16 kHz mono int16 from an iOS sender, tripled to the 48 kHz context rate.
    const int16 = new Int16Array(data.slice(9));
    const pcm = new Float32Array(int16.length * 3);
    for (let i = 0; i < int16.length; i++) {
        const s = int16[i] < 0 ? int16[i] / 0x8000 : int16[i] / 0x7FFF;
        const at = i * 3;
        pcm[at] = s; pcm[at + 1] = s; pcm[at + 2] = s;
    }
    const port = portFor(publicId);
    if (port) port.postMessage(pcm, [pcm.buffer]);
    return true;
}

// Microphone uplink: capture frames reach this worker directly (a
// transferred track-processor stream, or the capture worklet's port), are
// encoded here, and go out framed -- a stalled page then delays outgoing
// voice no more than incoming.
let micEncoder = null, micActive = false, micId = null, micCursor = 0;
const hasAudioEncoder = (typeof AudioEncoder !== 'undefined');

function micEnsureEncoder() {
    if (!hasAudioEncoder) return null;
    if (micEncoder && micEncoder.state === 'configured') return micEncoder;
    if (micEncoder) { try { micEncoder.close(); } catch (e) {} }
    micEncoder = new AudioEncoder({
        output: (chunk) => {
            if (!micActive || !micId || !ws || ws.readyState !== 1) return;
            const msg = new Uint8Array(10 + chunk.byteLength);
            msg.set(micId, 0);
            msg[8] = AUDIO_FRAME;
            msg[9] = 0x00;
            chunk.copyTo(msg.subarray(10));
            try { ws.send(msg.buffer); } catch (err) {}
        },
        error: () => { micEncoder = null; },
    });
    micEncoder.configure({ codec: 'opus', sampleRate: 48000, numberOfChannels: 1, bitrate: 128000 });
    return micEncoder;
}

function micEncode(frame) {
    const enc = micActive ? micEnsureEncoder() : null;
    if (enc) { try { enc.encode(frame); } catch (err) {} }
    frame.close();
}

self.onmessage = (e) => {
    const m = e.data;
    if (m.type === 'micState') {
        if (m.active !== undefined) micActive = !!m.active;
        if (m.id !== undefined) micId = m.id;
        return;
    }
    if (m.type === 'micStream') {
        const reader = m.readable.getReader();
        (async () => {
            for (;;) {
                let r;
                try { r = await reader.read(); } catch (err) { break; }
                if (r.done) break;
                micEncode(r.value);
            }
        })();
        return;
    }
    if (m.type === 'micPort') {
        // Channel arrays from the capture worklet; mono, at its context rate.
        const rate = m.sampleRate || 48000;
        micCursor = 0;
        m.port.onmessage = (ev) => {
            const channels = ev.data;
            if (!micActive || !channels || !channels[0]) return;
            const n = channels[0].length;
            const frame = new AudioData({
                format: 'f32', sampleRate: rate, numberOfFrames: n,
                numberOfChannels: 1,
                timestamp: Math.round(micCursor / rate * 1e6), data: channels[0] });
            micCursor += n;
            micEncode(frame);
        };
        return;
    }
    if (m.type === 'micStop') {
        micActive = false;
        if (micEncoder) { try { micEncoder.close(); } catch (err) {} micEncoder = null; }
        return;
    }
    if (m.type === 'audioPort') { ports.set(m.publicId, m.port); return; }
    if (m.type === 'audioState') { m.active ? muted.delete(m.publicId) : muted.add(m.publicId); return; }
    if (m.type === 'audioClose') {
        ports.delete(m.publicId);
        muted.delete(m.publicId);
        const dec = decoders.get(m.publicId);
        if (dec) { decoders.delete(m.publicId); try { dec.close(); } catch (err) {} }
        return;
    }
    if (m.type === 'open') {
        ws = new WebSocket(m.url);
        ws.binaryType = 'arraybuffer';
        ws.onopen = () => self.postMessage({ type: 'open' });
        ws.onerror = () => self.postMessage({ type: 'error' });
        ws.onclose = (ev) => {
            self.postMessage({ type: 'close', code: ev.code, reason: ev.reason, wasClean: ev.wasClean });
            ws = null;
        };
        ws.onmessage = (ev) => {
            const d = ev.data;
            if (d instanceof ArrayBuffer) {
                if (divertAudio(d)) return;
                self.postMessage({ type: 'message', data: d }, [d]);
                return;
            }
            self.postMessage({ type: 'message', data: d });
        };
        return;
    }
    if (!ws) return;
    if (m.type === 'send') {
        try { ws.send(m.data); } catch (err) { /* a closing socket reports through onclose */ }
        return;
    }
    if (m.type === 'close') { try { ws.close(m.code, m.reason); } catch (err) {} }
};
`;

// A WebSocket-shaped handle onto the socket the worker owns. Call sites keep
// the API they had; only the thread the bytes are read on changes.
class RoomSocket {
    constructor(url) {
        this.readyState = WebSocket.CONNECTING;
        this.binaryType = 'arraybuffer';
        this.onopen = this.onmessage = this.onerror = this.onclose = null;
        this._listeners = {};
        // Called instead of onclose when the worker itself dies before it ever
        // spoke: a policy that blocks blob workers reports there, not as a
        // synchronous throw, and the caller retries on a page socket.
        this.onworkerdead = null;
        this._workerSpoke = false;
        const workerURL = URL.createObjectURL(new Blob([ROOM_SOCKET_WORKER_SRC], { type: 'text/javascript' }));
        try {
            this._worker = new Worker(workerURL);
        } finally {
            URL.revokeObjectURL(workerURL);
        }
        this._worker.onmessage = (e) => {
            const m = e.data;
            this._workerSpoke = true;
            if (m.type === 'message') {
                const ev = { data: m.data };
                if (this.onmessage) this.onmessage(ev);
                this._emit('message', ev);
                return;
            }
            if (m.type === 'open') {
                this.readyState = WebSocket.OPEN;
                if (this.onopen) this.onopen();
                this._emit('open', {});
                return;
            }
            if (m.type === 'error') { if (this.onerror) this.onerror(m); this._emit('error', m); return; }
            if (m.type === 'close') {
                this.readyState = WebSocket.CLOSED;
                if (this.onclose) this.onclose(m);
                this._emit('close', m);
                this._retire();
            }
        };
        // A worker that died reports like a failed socket, not a silent hang.
        this._worker.onerror = (event) => {
            if (this.readyState === WebSocket.CLOSED) return;
            this.readyState = WebSocket.CLOSED;
            this._retire();
            if (!this._workerSpoke && this.onworkerdead) { this.onworkerdead(event); return; }
            if (this.onerror) this.onerror(event);
            if (this.onclose) this.onclose({ code: 1006, reason: '', wasClean: false });
        };
        this._worker.postMessage({ type: 'open', url });
    }

    send(data) {
        if (this.readyState !== WebSocket.OPEN) return;
        if (typeof data === 'string') {
            this._worker.postMessage({ type: 'send', data });
            return;
        }
        // An ArrayBuffer is transferred rather than copied, so a caller must
        // not keep it; a view is copied.
        const buf = data instanceof ArrayBuffer ? data
            : ArrayBuffer.isView(data)
                ? data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength)
                : data;
        this._worker.postMessage({ type: 'send', data: buf }, [buf]);
    }

    close(code, reason) {
        this.readyState = WebSocket.CLOSING;
        try { this._worker.postMessage({ type: 'close', code, reason }); } catch (err) { /* already gone */ }
        this._retire();
    }

    // Transfers a native track-processor stream for the worker to read,
    // encode and send: the whole microphone chain then skips the page.
    connectMicStream(readable) {
        try { this._worker.postMessage({ type: 'micStream', readable }, [readable]); } catch (err) {}
    }

    // The capture worklet's line, for engines with no page processor.
    connectMicPort(port, sampleRate) {
        try { this._worker.postMessage({ type: 'micPort', port, sampleRate }, [port]); } catch (err) {}
    }

    setMicActive(active) {
        try { this._worker.postMessage({ type: 'micState', active }); } catch (err) {}
    }

    setMicId(idBytes) {
        try { this._worker.postMessage({ type: 'micState', id: idBytes }); } catch (err) {}
    }

    stopMic() {
        try { this._worker.postMessage({ type: 'micStop' }); } catch (err) {}
    }

    addEventListener(type, fn) {
        (this._listeners[type] || (this._listeners[type] = [])).push(fn);
    }

    removeEventListener(type, fn) {
        const list = this._listeners[type];
        if (!list) return;
        const at = list.indexOf(fn);
        if (at >= 0) list.splice(at, 1);
    }

    // Calls every listener for the type; one throwing does not stop the rest.
    _emit(type, ev) {
        const list = this._listeners[type];
        if (!list) return;
        for (const fn of list.slice()) {
            try { fn(ev); } catch (err) {}
        }
    }

    // Hands the worker a speaking tile's line into its playback worklet.
    connectAudio(publicId, port) {
        try { this._worker.postMessage({ type: 'audioPort', publicId, port }, [port]); } catch (err) {}
    }

    setAudioActive(publicId, active) {
        try { this._worker.postMessage({ type: 'audioState', publicId, active }); } catch (err) {}
    }

    closeAudio(publicId) {
        try { this._worker.postMessage({ type: 'audioClose', publicId }); } catch (err) {}
    }

    // Ends the worker once, whichever side closed the socket.
    _retire() {
        if (this._retiring) return;
        this._retiring = true;
        setTimeout(() => { try { this._worker.terminate(); } catch (err) {} }, 2000);
    }
}

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
    let localAudioContext = null;
    let localSpeakingData = null;
    let animationFrameId = null;
    const WEBCAM_WIDTH = 240;
    const WEBCAM_HEIGHT = 180;
    let lastKnownVolume = parseFloat(localStorage.getItem('collab_iframe_volume')) || 1.0;
    let isIframeMuted = false;

    const sendVolumeToIframe = () => {
        const iframe = document.getElementById('session-frame');
        if (iframe && iframe.contentWindow) {
            const vol = isIframeMuted ? 0 : lastKnownVolume;
            iframe.contentWindow.postMessage({ type: 'setVolume', value: vol }, window.location.origin);
            if (isIframeMuted) iframe.contentWindow.postMessage({ type: 'setMute', value: true }, window.location.origin);
        }
    };

    const streamMuteBtn = document.getElementById('stream-mute-btn');
    const streamVolumeSlider = document.getElementById('stream-volume-slider');
    const syncStreamVolumeControls = () => {
        streamMuteBtn.querySelector('i').className = isIframeMuted ? 'fas fa-volume-mute' : 'fas fa-volume-up';
        streamMuteBtn.classList.toggle('inactive', isIframeMuted);
        streamVolumeSlider.value = isIframeMuted ? 0 : lastKnownVolume;
    };
    syncStreamVolumeControls();
    streamVolumeSlider.addEventListener('input', (e) => {
        const value = parseFloat(e.target.value);
        isIframeMuted = value === 0;
        if (!isIframeMuted) {
            lastKnownVolume = value;
            localStorage.setItem('collab_iframe_volume', lastKnownVolume);
        }
        syncStreamVolumeControls();
        sendVolumeToIframe();
    });
    streamMuteBtn.addEventListener('click', () => {
        isIframeMuted = !isIframeMuted;
        syncStreamVolumeControls();
        sendVolumeToIframe();
    });

    const handlePageInteraction = () => {
        setTimeout(sendVolumeToIframe, 500);
        ['click', 'keydown', 'touchstart'].forEach(e => document.removeEventListener(e, handlePageInteraction));
    };
    ['click', 'keydown', 'touchstart'].forEach(e => document.addEventListener(e, handlePageInteraction));
    window.addEventListener('blur', handlePageInteraction);

    let ws;
    let reconnectTimer = null;
    let reconnectAttempts = 0;
    let sessionEnded = false;
    const MAX_RECONNECT_ATTEMPTS = 8;
    let username = localStorage.getItem('collab_username');
    let isChatOpen = false;
    let messageStore = {};
    const MAX_STORED_MESSAGES = 200;
    let replyingTo = null;
    const PEEK_LIFETIME_MS = 6000;
    const MAX_PEEK_BUBBLES = 3;
    const INVITE_FLASH_MS = 1200;
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

    const videoToggleHandle = document.getElementById('video-toggle-handle');
    let isVideoGridVisible = true;
    const settingsModalOverlay = document.getElementById('settings-modal-overlay');
    const settingsModalCloseBtn = document.getElementById('settings-modal-close');
    const usernameModalOverlay = document.getElementById('username-modal-overlay');
    const audioInputSelect = document.getElementById('audio-input-select');
    const videoInputSelect = document.getElementById('video-input-select');
    const reloadStreamBtn = document.getElementById('reload-stream-btn');
    const gamingModeBtn = document.getElementById('gaming-mode-btn');
    const videoGrid = document.getElementById('video-grid');
    const videoStrip = document.getElementById('video-strip');
    const videoGridContent = document.getElementById('video-grid-content');
    const localVideo = document.getElementById('local-video');
    const localContainer = document.getElementById('local-user-container');
    const toggleMicBtn = document.getElementById('toggle-mic-btn');
    const toggleVideoBtn = document.getElementById('toggle-video-btn');
    const settingsBtn = document.getElementById('settings-btn');
    const chatForm = document.getElementById('chat-form');
    const chatInput = document.getElementById('chat-input');
    const chatDock = document.getElementById('chat-dock');
    const chatTab = document.getElementById('chat-tab');
    const chatScroll = document.getElementById('chat-messages-scroll');
    const chatPeek = document.getElementById('chat-peek');
    const inviteTile = document.getElementById('invite-tile');
    const inviteBtn = document.getElementById('invite-btn');

    localContainer.dataset.userToken = COLLAB_DATA.userToken;
    if (COLLAB_DATA.userRole !== 'controller') gamingModeBtn.classList.remove('hidden');

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
          // Adaptive jitter depth. Output starts once target packets are
          // queued; each mid-stream underrun deepens the target by one, a
          // clean stretch decays it back, and standing depth above it is
          // trimmed a packet at a time -- so steady latency sits at the
          // smallest depth the delivery path has recently proven to hold.
          this.TARGET_MIN = 2;
          this.TARGET_MAX = 4;
          this.MAX_BUFFER_PACKETS = 5;
          this.target = this.TARGET_MIN;
          this.priming = true;
          this.overCount = 0;
          this.cleanCount = 0;
          // Packets left at the moment one is pulled, tracked at its minimum:
          // the slack that proves a shallower target safe.
          this.shiftSlackMin = Infinity;

          this.enqueue = (pcmData) => {
            if (this.audioBufferQueue.length >= this.MAX_BUFFER_PACKETS) {
                this.audioBufferQueue.shift(); // Drop the oldest packet to reduce latency
            }
            this.audioBufferQueue.push(pcmData);
          };
          this.port.onmessage = (event) => {
            const data = event.data;
            // The socket worker's own line in: packets then reach this
            // processor whatever the page's thread is doing.
            if (data && data.port) {
                data.port.onmessage = (m) => this.enqueue(m.data);
                return;
            }
            this.enqueue(data);
          };
        }

        process(inputs, outputs, parameters) {
            const outputChannel = outputs[0][0];
            if (!outputChannel) return true;

            if (this.priming) {
                if (this.audioBufferQueue.length < this.target) {
                    outputChannel.fill(0);
                    return true;
                }
                this.priming = false;
            }

            const samplesPerBuffer = outputChannel.length;
            let currentSampleIndex = 0;

            while (currentSampleIndex < samplesPerBuffer) {
                if (!this.currentAudioData || this.currentDataOffset >= this.currentAudioData.length) {
                    if (this.audioBufferQueue.length > 0) {
                        const slack = this.audioBufferQueue.length - 1;
                        if (slack < this.shiftSlackMin) this.shiftSlackMin = slack;
                        this.currentAudioData = this.audioBufferQueue.shift();
                        this.currentDataOffset = 0;
                    } else {
                        outputChannel.fill(0, currentSampleIndex);
                        this.priming = true;
                        this.target = Math.min(this.target + 1, this.TARGET_MAX);
                        this.overCount = 0;
                        this.cleanCount = 0;
                        this.shiftSlackMin = Infinity;
                        return true;
                    }
                }

                const samplesToCopy = Math.min(samplesPerBuffer - currentSampleIndex, this.currentAudioData.length - this.currentDataOffset);
                const chunkToCopy = this.currentAudioData.subarray(this.currentDataOffset, this.currentDataOffset + samplesToCopy);

                outputChannel.set(chunkToCopy, currentSampleIndex);

                this.currentDataOffset += samplesToCopy;
                currentSampleIndex += samplesToCopy;
            }

            // Reclaims latency: depth held above target is a standing delay,
            // dropped one packet per window; long clean runs shrink the target.
            if (this.audioBufferQueue.length > this.target) {
                if (++this.overCount >= 250) {
                    this.audioBufferQueue.shift();
                    this.overCount = 0;
                }
            } else {
                this.overCount = 0;
            }
            if (++this.cleanCount >= 2000) {
                this.cleanCount = 0;
                // Decay only over proven slack: a whole packet must have
                // stayed spare at every pull, else a shallower target is a
                // periodic audible probe rather than a reclaim.
                if (this.target > this.TARGET_MIN && this.shiftSlackMin >= 2) this.target--;
                this.shiftSlackMin = Infinity;
            }

            return true;
        }
      }
      registerProcessor('audio-player-processor', AudioPlayerProcessor);
    `;

    const closeLocalAudioContext = () => {
        if (!localAudioContext) return;
        if (localAudioContext.state !== 'closed') {
            localAudioContext.close().catch(err => console.warn('[Media] local analyser context close failed:', err));
        }
        localAudioContext = null;
    };

    const startMedia = async () => {
        if (COLLAB_DATA.userPermission === 'readonly') {
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
                width: WEBCAM_WIDTH,
                height: WEBCAM_HEIGHT,
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

            closeLocalAudioContext();
            localAudioContext = new AudioContext();
            const source = localAudioContext.createMediaStreamSource(localStream);
            localAudioAnalyser = localAudioContext.createAnalyser();
            localAudioAnalyser.fftSize = 512;
            localSpeakingData = null;
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
            if (ws && ws.setMicActive) ws.setMicActive(isMicOn);
            sendControlMessage('audio_state', isMicOn);
        } else if (type === 'video') {
            if (localStream && localStream.getVideoTracks().length > 0) {
                isWebcamOn = !isWebcamOn;
                localStream.getVideoTracks().forEach(t => t.enabled = isWebcamOn);
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
        localSpeakingData = null;
        closeLocalAudioContext();
        mediaInitialized = false;
    };

    const restartMediaForDeviceChange = async () => {
        if (!mediaInitialized) return;
        if (isInitializingMedia) {
            console.warn('[Media] device switch ignored: media initialization is already in progress.');
            return;
        }
        isInitializingMedia = true;
        try {
            await startMedia();
        } finally {
            isInitializingMedia = false;
        }
    };

    const composeCanvas = typeof OffscreenCanvas !== 'undefined'
        ? new OffscreenCanvas(WEBCAM_WIDTH, WEBCAM_HEIGHT)
        : Object.assign(document.createElement('canvas'), { width: WEBCAM_WIDTH, height: WEBCAM_HEIGHT });
    const composeCtx = composeCanvas.getContext('2d', { alpha: false, desynchronized: true });
    let composeFailed = false;

    const composeFrame = (frame) => {
        const dw = frame.displayWidth || frame.codedWidth;
        const dh = frame.displayHeight || frame.codedHeight;
        const turn = canDeriveOrientation() ? deriveRotation() : 0;
        const sideways = turn % 180 === 90;
        const uprightW = sideways ? dh : dw;
        const uprightH = sideways ? dw : dh;
        const scale = Math.min(WEBCAM_WIDTH / uprightW, WEBCAM_HEIGHT / uprightH);
        composeCtx.fillStyle = '#000';
        composeCtx.fillRect(0, 0, WEBCAM_WIDTH, WEBCAM_HEIGHT);
        composeCtx.save();
        composeCtx.translate(WEBCAM_WIDTH / 2, WEBCAM_HEIGHT / 2);
        if (turn) composeCtx.rotate((turn * Math.PI) / 180);
        composeCtx.drawImage(frame, (-dw * scale) / 2, (-dh * scale) / 2, dw * scale, dh * scale);
        composeCtx.restore();
        return new VideoFrame(composeCanvas, { timestamp: frame.timestamp });
    };

    const setupVideoEncoder = async () => {
        // No VideoEncoder leaves the session audio-only; audio has a PCM floor.
        if (typeof VideoEncoder === 'undefined') return;
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
            width: WEBCAM_WIDTH,
            height: WEBCAM_HEIGHT,
            bitrate: 1_000_000,
            framerate: 30,
            latencyMode: 'realtime',
        });

        const readFrame = () => {
            videoReader.read().then(({ done, value: frame }) => {
                if (done || !localStream) return;

                if (videoEncoder.state === 'configured' && isWebcamOn) {
                    const needsKeyFrame = (frameCounter % 120 === 0);
                    let upright = frame;
                    if (!composeFailed) {
                        try {
                            upright = composeFrame(frame);
                        } catch (e) {
                            composeFailed = true;
                            logMediaPath(`webcam compose: canvas path unavailable (${e.message}); encoding raw frames.`);
                        }
                    }
                    videoEncoder.encode(upright, { keyFrame: needsKeyFrame });
                    if (upright !== frame) upright.close();
                    frameCounter++;
                }

                frame.close();
                readFrame();
            }).catch(e => console.error("[Encoder] Video reader error", e));
        };
        readFrame();
    };

    // Encode in the socket worker, else on the page; a ScriptProcessorNode
    // sending raw PCM is the floor, taken only where WebCodecs audio is
    // missing altogether (older iOS Safari).
    const setupAudioEncoder = async () => {
        const hasAudioCodec = (typeof AudioEncoder !== 'undefined' && typeof AudioData !== 'undefined');

        if (!hasAudioCodec) {
            logMediaPath('audio capture: ScriptProcessorNode PCM (no WebCodecs audio).');
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
        } else if (ws && ws.connectMicStream) {
            const [audioTrack] = localStream.getAudioTracks();
            if (!audioTrack) return;
            ws.setMicActive(isMicOn);
            if (localPublicIdBytes) ws.setMicId(localPublicIdBytes);
            let wired = false;
            if (hasWindowTrackProcessor) {
                try {
                    const processor = new MediaStreamTrackProcessor({ track: audioTrack });
                    ws.connectMicStream(processor.readable);
                    logMediaPath('audio capture: MediaStreamTrackProcessor read in the socket worker.');
                    // The native readable ends with the track; the worker side is
                    // told to drop its encoder.
                    audioProcessor = { close: () => { try { ws.stopMic(); } catch (err) {} } };
                    wired = true;
                } catch (err) {
                    console.warn('[Media] mic stream transfer failed:', err);
                }
            }
            if (!wired) {
                const ctx = new (window.AudioContext || window.webkitAudioContext)();
                const workletURL = URL.createObjectURL(new Blob([SHIM_AUDIO_WORKLET_SRC], { type: 'application/javascript' }));
                try {
                    await ctx.audioWorklet.addModule(workletURL);
                } finally {
                    URL.revokeObjectURL(workletURL);
                }
                const source = ctx.createMediaStreamSource(new MediaStream([audioTrack]));
                const node = new AudioWorkletNode(ctx, 'mstp-shim');
                const line = new MessageChannel();
                node.port.postMessage({ port: line.port1 }, [line.port1]);
                ws.connectMicPort(line.port2, ctx.sampleRate);
                source.connect(node);
                logMediaPath('audio capture: worklet feeding the socket worker.');
                audioProcessor = { close: () => {
                    try { ws.stopMic(); } catch (err) {}
                    try { source.disconnect(); node.disconnect(); } catch (err) {}
                    if (ctx.state !== 'closed') ctx.close().catch(() => {});
                } };
            }
            audioEncoder = { state: 'configured', close: () => { try { ws.stopMic(); } catch (err) {} } };
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
                    if (!stream.audioDecoder || stream.audioDecoder.state !== 'configured' || stream.audioMuted) return;
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

    const addRemoteStream = async (token, username, publicId) => {
        if (remoteStreams[token]) return;

        const container = document.createElement('div');
        container.className = 'video-container reorderable';
        container.id = `container-${token}`;
        container.dataset.userToken = token;

        const canvas = document.createElement('canvas');
        canvas.width = WEBCAM_WIDTH;
        canvas.height = WEBCAM_HEIGHT;
        const ctx = canvas.getContext('2d', { desynchronized: true });
        ctx.fillStyle = '#222';
        ctx.fillRect(0, 0, WEBCAM_WIDTH, WEBCAM_HEIGHT);

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
            <span class="username">${escapeHTML(username)}</span>
            <div class="remote-controls">
                ${controllerControls}
                <button class="remote-control-btn mute-audio" data-token="${token}" title="${t('tooltips.toggleRemoteAudio')}"><i class="fas fa-microphone"></i></button>
                <button class="remote-control-btn mute-video" data-token="${token}" title="${t('tooltips.toggleRemoteVideo')}"><i class="fas fa-video"></i></button>
            </div>
        `;

        container.appendChild(canvas);
        container.appendChild(video);
        container.appendChild(overlay);
        videoGridContent.insertBefore(container, inviteTile);

        const stream = {
            username, container, canvas, ctx, video, publicId,
            videoMuted: false, audioMuted: false,
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

        stream.videoDecoder = videoDecoder;
        remoteStreams[token] = stream;

        let audioContext;
        let workletNode;
        let analyser;
        try {
            audioContext = new AudioContext({ sampleRate: 48000 });
            if (isAudioUnlocked && audioContext.state === 'suspended') {
                audioContext.resume();
            }

            const workletBlob = new Blob([audioWorkletCode], { type: 'application/javascript' });
            const workletURL = URL.createObjectURL(workletBlob);
            try {
                await audioContext.audioWorklet.addModule(workletURL);
            } finally {
                URL.revokeObjectURL(workletURL);
            }
            workletNode = new AudioWorkletNode(audioContext, 'audio-player-processor');

            analyser = audioContext.createAnalyser();
            analyser.fftSize = 512;
            workletNode.connect(analyser);
            analyser.connect(audioContext.destination);
        } catch (err) {
            console.error(`[Media] audio playback setup failed for ${token}:`, err);
            if (audioContext && audioContext.state !== 'closed') {
                audioContext.close().catch(e => console.warn(`[Media] audio context close failed for ${token}:`, e));
            }
            if (remoteStreams[token] === stream) removeRemoteStream(token);
            return;
        }

        if (remoteStreams[token] !== stream) {
            workletNode.disconnect();
            if (audioContext.state !== 'closed') {
                audioContext.close().catch(e => console.warn(`[Media] audio context close failed for ${token}:`, e));
            }
            return;
        }

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

        Object.assign(stream, { audioDecoder, audioContext, workletNode, analyser });

        // The socket worker decodes this speaker and feeds the worklet down its
        // own line; the in-page decoder above is the path for a page-owned socket.
        if (publicId && ws && ws.connectAudio) {
            const line = new MessageChannel();
            workletNode.port.postMessage({ port: line.port1 }, [line.port1]);
            ws.connectAudio(publicId, line.port2);
        }

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
        if (stream && stream.publicId && ws && ws.closeAudio) ws.closeAudio(stream.publicId);
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
        if (sessionEnded) {
            animationFrameId = null;
            return;
        }
        const speakingThreshold = 5;
        let isAnyoneSpeaking = false;

        if (localAudioAnalyser && isMicOn) {
            if (!localSpeakingData || localSpeakingData.length !== localAudioAnalyser.frequencyBinCount) {
                localSpeakingData = new Uint8Array(localAudioAnalyser.frequencyBinCount);
            }
            const dataArray = localSpeakingData;
            localAudioAnalyser.getByteFrequencyData(dataArray);
            const avg = dataArray.reduce((a, b) => a + b, 0) / dataArray.length;
            localContainer.classList.toggle('speaking', avg > speakingThreshold);
            if (avg > speakingThreshold) isAnyoneSpeaking = true;
        } else {
            localContainer.classList.remove('speaking');
        }

        Object.values(remoteStreams).forEach(stream => {
            if (stream.analyser && !stream.audioMuted && stream.container) {
                if (!stream.speakingData || stream.speakingData.length !== stream.analyser.frequencyBinCount) {
                    stream.speakingData = new Uint8Array(stream.analyser.frequencyBinCount);
                }
                const dataArray = stream.speakingData;
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
        let vbTouchStartX = 0;
        let vbTouchStartY = 0;

        videoStrip.addEventListener('touchstart', (e) => {
            vbTouchStartX = e.changedTouches[0].screenX;
            vbTouchStartY = e.changedTouches[0].screenY;
        }, { passive: true });

        videoStrip.addEventListener('touchend', (e) => {
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
                    toggleVideoGrid();
                }
                lastTapTime = currentTime;
            });
        }
    };

    const adoptSocketWorker = async () => {
        if (!ws) return;
        if (ws.connectAudio) {
            for (const stream of Object.values(remoteStreams)) {
                if (!stream.publicId || !stream.workletNode) continue;
                const line = new MessageChannel();
                stream.workletNode.port.postMessage({ port: line.port1 }, [line.port1]);
                ws.connectAudio(stream.publicId, line.port2);
                ws.setAudioActive(stream.publicId, !stream.audioMuted);
            }
        }
        if (!mediaInitialized) return;
        if (audioProcessor) {
            try { audioProcessor.close(); } catch (err) { console.warn('[Media] mic teardown failed:', err); }
            audioProcessor = null;
        }
        if (audioEncoder && audioEncoder.state !== 'closed') {
            try { audioEncoder.close(); } catch (err) { console.warn('[Media] mic encoder teardown failed:', err); }
        }
        audioEncoder = null;
        await setupAudioEncoder();
    };

    const connectWebSocket = () => {
        const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const url = `${proto}//${window.location.host}/ws/room/${COLLAB_DATA.sessionId}?token=${encodeURIComponent(COLLAB_DATA.userToken)}`;
        // No worker to be had (a policy forbidding blob workers, say -- reported
        // as a throw or as the worker dying unspoken, depending on the engine).
        // The socket then runs here and audio takes the in-page decode path.
        const usePageSocket = () => {
            console.warn('[WS] socket worker unavailable, reading on the page.');
            ws = new WebSocket(url);
            ws.binaryType = 'arraybuffer';
            attachSocketHandlers();
        };
        try {
            ws = new RoomSocket(url);
            ws.onworkerdead = usePageSocket;
            attachSocketHandlers();
        } catch (err) {
            console.warn('[WS] socket worker could not start:', err);
            usePageSocket();
        }
        function attachSocketHandlers() {

            ws.onopen = () => {
                console.log('[WS] Collaboration WebSocket connected.');
                reconnectAttempts = 0;
                adoptSocketWorker();
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

            const dispatchSocketMessage = (event) => {
                if (event.data instanceof ArrayBuffer) {
                    const publicId = new TextDecoder().decode(event.data.slice(0, 8));
                    const token = publicIdToTokenMap[publicId];
                    if (!token) return;
                    const payload = event.data.slice(8);
                    handleRemoteStream(token, payload);
                    return;
                }

                let data;
                try {
                    data = JSON.parse(event.data);
                } catch (err) {
                    console.error('[WS] dropping an unparseable frame:', err);
                    return;
                }
                if (!data || typeof data !== 'object') {
                    console.warn('[WS] dropping a frame that is not a JSON object.');
                    return;
                }
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
                        if (!Array.isArray(data.viewers)) {
                            console.warn('[WS] dropping a state_update with no viewers list.');
                            break;
                        }
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
                            if (ws && ws.setMicId) ws.setMicId(localPublicIdBytes);
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

                        const iHaveMk = (mkOwnerUser && mkOwnerUser.token === COLLAB_DATA.userToken)
                            || (!mkOwnerUser && COLLAB_DATA.userRole === 'controller');
                        gamingModeBtn.classList.toggle('hidden', COLLAB_DATA.userRole === 'controller' && !iHaveMk);

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
                                addRemoteStream(user.token, user.username, user.publicId);
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
                        while (Object.keys(messageStore).length > MAX_STORED_MESSAGES) {
                            delete messageStore[Object.keys(messageStore)[0]];
                        }
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
                    case 'app_list':
                        availableAppsList = data.apps;
                        if (document.getElementById('start-menu-modal') && !document.getElementById('start-menu-modal').classList.contains('hidden')) {
                            renderStartMenu();
                        }
                        const activeApp = availableAppsList.find(app => app.active);
                        if (activeApp) {
                            document.title = activeApp.name;
                        }
                        break;
                    case 'app_swapped': {
                        pendingActions.clear();
                        const swapIframe = document.getElementById('session-frame');
                        let urlStr = swapIframe.src;
                        const isBlank = swapIframe.getAttribute('src') === 'about:blank';

                        if (isBlank && swapIframe.dataset.src) {
                            urlStr = swapIframe.dataset.src;
                        }
                        if (urlStr && urlStr !== 'about:blank') {
                            try {
                                const currentSrc = new URL(urlStr, window.location.href);
                                currentSrc.searchParams.set('t', Date.now());

                                if (isBlank) {
                                    swapIframe.dataset.src = currentSrc.toString();
                                } else {
                                    swapIframe.src = currentSrc.toString();
                                }
                            } catch (e) {
                                console.warn("Could not reload iframe on swap:", e);
                            }
                        }
                        document.title = data.app_name;
                        ws.send(JSON.stringify({ action: 'get_apps' }));
                        showChatPeek({ sender: t('systemMessages.systemSender'), message: t('systemMessages.swappedApp', { app_name: data.app_name }) });
                        break;
                    }
                    case 'error':
                        pendingActions.clear();
                        if (document.getElementById('start-menu-modal')) renderStartMenu();
                        alert(data.message);
                        break;
                }
            };

            ws.onmessage = (event) => {
                try {
                    dispatchSocketMessage(event);
                } catch (err) {
                    console.error('[WS] dropping a frame the handler could not process:', err);
                }
            };

            ws.onclose = (event) => {
                console.log('[WS] WebSocket closed.', event ? event.code : undefined);
                if (sessionEnded || (event && event.code === 1008) || reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
                    handleControllerDisconnect();
                    return;
                }
                reconnectAttempts += 1;
                const delay = Math.min(1000 * 2 ** (reconnectAttempts - 1), 10000);
                reconnectTimer = setTimeout(connectWebSocket, delay);
            };
            ws.onerror = (err) => console.error('[WS] WebSocket error:', err);
        }
    };

    const handleControllerDisconnect = () => {
        sessionEnded = true;
        if (reconnectTimer) {
            clearTimeout(reconnectTimer);
            reconnectTimer = null;
        }
        if (animationFrameId !== null) {
            cancelAnimationFrame(animationFrameId);
            animationFrameId = null;
        }
        document.getElementById('disconnection-overlay').classList.remove('hidden');
        const iframe = document.getElementById('session-frame');
        if (iframe) iframe.remove();
        if (ws && ws.readyState !== WebSocket.CLOSED) ws.close();
        stopMedia();
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

    const renderRoom = () => {
        const hasJoined = sessionStorage.getItem('collab_hasJoined_' + COLLAB_DATA.sessionId);
        if (COLLAB_DATA.userRole === 'viewer' && !hasJoined) {
            showUsernamePrompt();
        } else {
            renderMainRoom();
        }
    };

    const showUsernamePrompt = () => {
        const usernameInput = document.getElementById('username-input');
        if (username) {
            usernameInput.value = username;
        }
        usernameModalOverlay.classList.remove('hidden');
        usernameInput.focus();
    };

    const renderMainRoom = () => {
        usernameModalOverlay.classList.add('hidden');
        const isController = COLLAB_DATA.userRole === 'controller';
        const isParticipant = COLLAB_DATA.userRole === 'viewer' && COLLAB_DATA.userPermission === 'participant';

        let localControls = '';
        if (isController) {
            localControls = `
                <button class="remote-control-btn toggle-resolution-lock" title="${t('tooltips.lockResolution')}"><i class="fas fa-lock-open"></i></button>
                <button class="remote-control-btn resize-to-client" data-token="${COLLAB_DATA.userToken}" title="${t('tooltips.resizeClient')}"><i class="fas fa-desktop"></i></button>
                <button class="remote-control-btn designate-speaker" data-token="${COLLAB_DATA.userToken}" title="${t('tooltips.designateSpeaker')}"><i class="fas fa-star"></i></button>
            `;
        }
        localContainer.querySelector('.video-overlay').innerHTML = `
            <span class="username">${escapeHTML(isController ? 'Controller' : (username || t('localUsername')))}</span>
            <div class="remote-controls">${localControls}</div>`;

        if (isController || isParticipant) {
            initGamepadControls();
        }
        const canInviteParticipant = isController && Boolean(COLLAB_DATA.participantJoinUrl);
        const canInviteReadonly = (isController || isParticipant) && Boolean(COLLAB_DATA.readonlyJoinUrl);
        inviteTile.querySelector('[data-permission="participant"]').classList.toggle('hidden', !canInviteParticipant);
        inviteTile.querySelector('[data-permission="readonly"]').classList.toggle('hidden', !canInviteReadonly);
        inviteTile.classList.toggle('hidden', !canInviteParticipant && !canInviteReadonly);
        updateMediaButtonUI();
    };

    const scrollChatToBottom = () => {
        chatScroll.scrollTop = chatScroll.scrollHeight;
    };

    const openChat = () => {
        if (isChatOpen) return;
        isChatOpen = true;
        chatTab.classList.remove('unread');
        chatDock.classList.add('open');
        chatDock.addEventListener('transitionend', scrollChatToBottom, { once: true });
        scrollChatToBottom();
    };

    const closeChat = () => {
        if (!isChatOpen) return;
        isChatOpen = false;
        chatDock.classList.remove('open');
        chatDock.addEventListener('transitionend', scrollChatToBottom, { once: true });
        if (document.activeElement === chatInput) chatInput.blur();
    };

    const isInviteOpen = () => inviteTile.classList.contains('open');
    const openInvite = () => inviteTile.classList.add('open');
    const closeInvite = () => inviteTile.classList.remove('open');

    const initInviteControls = () => {
        inviteBtn.addEventListener('click', openInvite);
        const inviteUrls = {
            participant: COLLAB_DATA.participantJoinUrl,
            readonly: COLLAB_DATA.readonlyJoinUrl,
        };
        inviteTile.querySelectorAll('.invite-copy').forEach((button) => {
            let resetTimer = null;
            const flash = (text) => {
                const label = button.textContent;
                button.textContent = text;
                clearTimeout(resetTimer);
                resetTimer = setTimeout(() => {
                    button.textContent = label;
                }, INVITE_FLASH_MS);
            };
            button.addEventListener('click', async () => {
                const url = inviteUrls[button.dataset.permission];
                if (!url) return;
                try {
                    await navigator.clipboard.writeText(url);
                    flash(t('inviteLinks.copied'));
                    setTimeout(closeInvite, INVITE_FLASH_MS);
                } catch (err) {
                    console.error('[Invite] copy failed:', err);
                    flash(t('inviteLinks.failed'));
                }
            });
        });
    };

    const initDock = () => {
        toggleMicBtn.addEventListener('click', () => handleMediaToggle('mic'));
        toggleVideoBtn.addEventListener('click', () => handleMediaToggle('video'));
        settingsBtn.addEventListener('click', () => {
            unlockAllAudio();
            populateDeviceLists();
            settingsModalOverlay.classList.remove('hidden');
        });
        localContainer.addEventListener('click', (e) => {
            if (e.target.closest('button, .gamepad-icon')) return;
            localContainer.classList.toggle('controls-shown');
        });

        chatForm.addEventListener('submit', handleChatSubmit);
        chatInput.addEventListener('focus', openChat);
        chatInput.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') closeChat();
        });
        chatScroll.addEventListener('click', handleChatAreaClick);
        document.getElementById('username-form').addEventListener('submit', handleUsernameSubmit);

        initInviteControls();
        if (COLLAB_DATA.userRole === 'controller') initStartMenu();

        reloadStreamBtn.addEventListener('click', () => {
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

        document.addEventListener('pointerdown', (e) => {
            if (isChatOpen && !e.target.closest('#chat-dock')) closeChat();
            if (isInviteOpen() && !e.target.closest('#invite-tile')) closeInvite();
            if (!e.target.closest('#local-user-container')) localContainer.classList.remove('controls-shown');
        }, true);
        const updateBarLayout = () => {
            document.documentElement.style.setProperty('--bar-height', `${videoGrid.offsetHeight}px`);
            const styles = getComputedStyle(videoGrid);
            const gap = parseFloat(styles.columnGap) || 0;
            const padding = (parseFloat(styles.paddingLeft) || 0) + (parseFloat(styles.paddingRight) || 0);
            const fullDock = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--chat-dock-width')) || 0;
            const needed = videoGridContent.scrollWidth + gap + fullDock + padding;
            document.body.classList.toggle('chat-crowded', needed > videoGrid.clientWidth);
        };
        const barObserver = new ResizeObserver(updateBarLayout);
        barObserver.observe(videoGrid);
        barObserver.observe(videoGridContent);
        chatTab.addEventListener('click', () => {
            openChat();
            chatInput.focus();
        });
        window.addEventListener('blur', () => {
            closeChat();
            closeInvite();
        });
    };

    const escapeHTML = (str) => str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#039;');

    const linkify = (text) => {
        const urlRegex = /(\b(https?|ftp|file):\/\/[-A-Z0-9+&@#\/%?=~_|!:,.;]*[-A-Z0-9+&@#\/%=~_|])/ig;
        return text.replace(urlRegex, (url) => `<a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>`);
    };

    const isOwnChatMessage = (data) =>
        data.sender === username || (COLLAB_DATA.userRole === 'controller' && data.sender === 'Controller');

    const createMessageHTML = (data) => {
        const isSelf = isOwnChatMessage(data);
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

        const isScrolledToBottom = chatScroll.scrollHeight - chatScroll.clientHeight <= chatScroll.scrollTop + 50;

        const msgEl = document.createElement('div');
        let isOwnMessage = false;

        if (type === 'chat') {
            isOwnMessage = isOwnChatMessage(data);
            msgEl.innerHTML = createMessageHTML(data);
        } else {
            let content = '';
            switch (data.type) {
                case 'user_joined': content = t('systemMessages.userJoined', { username: escapeHTML(data.username) }); break;
                case 'user_left': content = t('systemMessages.userLeft', { username: escapeHTML(data.username) }); break;
                case 'username_changed': content = t('systemMessages.usernameChanged', { old_username: escapeHTML(data.old_username), new_username: escapeHTML(data.new_username) }); break;
                case 'gamepad_change': content = escapeHTML(data.message); break;
                case 'mk_change': content = escapeHTML(data.message); break;
            }
            msgEl.className = 'system-message';
            msgEl.innerHTML = `<span>${content}</span>`;
        }

        messagesContainer.appendChild(msgEl);
        while (messagesContainer.children.length > MAX_STORED_MESSAGES) {
            messagesContainer.removeChild(messagesContainer.firstChild);
        }

        if (isScrolledToBottom) {
            scrollChatToBottom();
        }

        if (type === 'chat' && !isOwnMessage) {
            playNotificationSound();
            if (!isChatOpen) {
                showChatPeek(data);
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
            renderRoom();

            if (!mediaInitialized) {
                await startMedia();
            }

            isMicOn = true;
            if (localStream) localStream.getAudioTracks().forEach(t => t.enabled = true);
            if (ws && ws.setMicActive) ws.setMicActive(true);
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
        }
    };

    const handleChatSubmit = (e) => {
        e.preventDefault();
        const message = chatInput.value.trim();
        if (!message) return;
        if (!ws || ws.readyState !== WebSocket.OPEN) {
            console.warn('[Chat] message not sent: the room socket is not open.');
            chatForm.classList.remove('send-failed');
            requestAnimationFrame(() => chatForm.classList.add('send-failed'));
            return;
        }
        const payload = {
            action: 'send_chat_message',
            message
        };
        if (replyingTo) {
            payload.replyTo = replyingTo.messageId;
        }
        ws.send(JSON.stringify(payload));
        chatInput.value = '';
        cancelReply();
    };

    const handleChatAreaClick = (e) => {
        const replyBtn = e.target.closest('.reply-btn');
        if (replyBtn) {
            const messageEl = e.target.closest('.chat-message');
            const messageId = messageEl.dataset.messageId;
            if (messageStore[messageId]) {
                replyingTo = messageStore[messageId];
                renderReplyBanner();
                chatInput.focus();
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

    const showChatPeek = (data) => {
        const bubble = document.createElement('div');
        bubble.className = 'chat-peek-bubble';
        bubble.innerHTML = `
            <div class="chat-peek-sender">${escapeHTML(data.sender)}</div>
            <div class="chat-peek-message">${linkify(escapeHTML(data.message))}</div>
        `;
        bubble.addEventListener('click', () => {
            openChat();
            chatInput.focus();
        });
        chatTab.classList.add('unread');
        chatPeek.appendChild(bubble);
        while (chatPeek.children.length > MAX_PEEK_BUBBLES) {
            chatPeek.removeChild(chatPeek.firstChild);
        }

        setTimeout(() => {
            bubble.classList.add('closing');
            bubble.addEventListener('animationend', () => bubble.remove());
        }, PEEK_LIFETIME_MS);
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
        }
        mkIcon.innerHTML = `<i class="fas fa-keyboard"></i><i class="fas fa-mouse" style="margin-left: 3px; font-size: 0.8em;"></i>`;

        if (COLLAB_DATA.userRole === 'controller') {
            localContainer.appendChild(mkIcon);
        } else {
            sourceBox.appendChild(mkIcon);
        }

        for (let i = 1; i <= GAMEPAD_COUNT; i++) {
            const icon = document.createElement('div');
            icon.id = `gamepad-icon-${i}`;
            icon.className = 'gamepad-icon';
            if (COLLAB_DATA.userRole === 'controller') {
                icon.classList.add('draggable');
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

    const DRAG_START_PX = 5;
    let drag = null;

    const dragTargets = () => Array.from(document.querySelectorAll('.video-container')).filter((container) => {
        const user = currentUserState.find(u => u.token === container.dataset.userToken);
        return container.id === 'gamepad-source-box' || user;
    });

    const beginDrag = (pending) => {
        const { source, pointerId } = pending;
        const ghost = source.cloneNode(true);
        ghost.classList.add('drag-ghost');
        ghost.removeAttribute('id');
        const rect = source.getBoundingClientRect();
        ghost.style.width = `${rect.width}px`;
        ghost.style.height = `${rect.height}px`;
        document.body.appendChild(ghost);

        drag = { ...pending, ghost, offsetX: pending.startX - rect.left, offsetY: pending.startY - rect.top, over: null };
        source.setPointerCapture(pointerId);

        if (drag.kind === 'stream') {
            document.body.classList.add('dragging-stream');
            source.classList.add('reordering');
        } else {
            document.body.classList.add(drag.kind === 'mk' ? 'dragging-mk' : 'dragging-gamepad');
            source.classList.add('dragging');
            dragTargets().forEach(c => c.classList.add('can-drop-gamepad'));
        }
    };

    const moveGhost = (e) => {
        drag.ghost.style.transform = `translate(${e.clientX - drag.offsetX}px, ${e.clientY - drag.offsetY}px)`;
    };

    const containerUnder = (e) => {
        const el = document.elementFromPoint(e.clientX, e.clientY);
        return el ? el.closest('.video-container') : null;
    };

    const endDrag = (dropped) => {
        if (!drag) return;
        const { source, kind, ghost, over } = drag;
        drag = null;
        ghost.remove();
        document.body.classList.remove('dragging-gamepad', 'dragging-mk', 'dragging-stream');
        source.classList.remove('dragging', 'reordering');
        document.querySelectorAll('.can-drop-gamepad, .drop-target').forEach(el => el.classList.remove('can-drop-gamepad', 'drop-target'));
        if (!dropped || kind === 'stream' || !over) return;

        if (kind === 'mk') {
            const tokenToAssign = (over.id === 'gamepad-source-box') ? COLLAB_DATA.userToken : over.dataset.userToken;
            ws.send(JSON.stringify({ action: 'assign_mk', token: tokenToAssign }));
            return;
        }
        const gamepadId = parseInt(source.dataset.gamepadId, 10);
        if (over.id === 'gamepad-source-box') {
            const parentContainer = source.parentElement;
            if (parentContainer && parentContainer.id !== 'gamepad-source-box') {
                const userToken = parentContainer.dataset.userToken;
                if (userToken) ws.send(JSON.stringify({ action: 'assign_slot', viewer_token: userToken, slot: null }));
            }
        } else {
            const userToken = over.dataset.userToken;
            if (userToken) ws.send(JSON.stringify({ action: 'assign_slot', viewer_token: userToken, slot: gamepadId }));
        }
    };

    videoStrip.addEventListener('pointerdown', (e) => {
        if (e.button !== 0 || drag) return;
        const icon = e.target.closest('.gamepad-icon.draggable');
        const tile = e.target.closest('.video-container.reorderable');
        let source, kind;
        if (icon) {
            source = icon;
            kind = icon.classList.contains('mk-icon') ? 'mk' : 'gamepad';
        } else if (tile && e.pointerType === 'mouse' && !e.target.closest('button')) {
            source = tile;
            kind = 'stream';
        } else {
            return;
        }
        const pending = { source, kind, pointerId: e.pointerId, startX: e.clientX, startY: e.clientY };
        const onMove = (ev) => {
            if (ev.pointerId !== pending.pointerId) return;
            if (!drag) {
                if (Math.hypot(ev.clientX - pending.startX, ev.clientY - pending.startY) < DRAG_START_PX) return;
                beginDrag(pending);
            }
            moveGhost(ev);
            const over = containerUnder(ev);
            if (drag.kind === 'stream') {
                if (over && !over.classList.contains('pinned') && over !== drag.source) {
                    const rect = over.getBoundingClientRect();
                    const before = ev.clientX - rect.left < rect.width / 2;
                    over.parentNode.insertBefore(drag.source, before ? over : over.nextSibling);
                }
                return;
            }
            const target = over && over.classList.contains('can-drop-gamepad') ? over : null;
            if (target !== drag.over) {
                drag.over?.classList.remove('drop-target');
                target?.classList.add('drop-target');
                drag.over = target;
            }
        };
        const onEnd = (ev) => {
            if (ev.pointerId !== pending.pointerId) return;
            source.removeEventListener('pointermove', onMove);
            source.removeEventListener('pointerup', onEnd);
            source.removeEventListener('pointercancel', onEnd);
            endDrag(ev.type === 'pointerup');
        };
        source.addEventListener('pointermove', onMove);
        source.addEventListener('pointerup', onEnd);
        source.addEventListener('pointercancel', onEnd);
    });

    let isBouncing = false;
    videoStrip.addEventListener('wheel', e => {
        if (videoStrip.scrollWidth > videoStrip.clientWidth) {
            e.preventDefault();
            videoStrip.scrollLeft += e.deltaY;
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
        restartMediaForDeviceChange().catch(err => console.error('[Media] microphone switch failed:', err));
    });
    videoInputSelect.addEventListener('change', (e) => {
        preferredCamId = e.target.value;
        localStorage.setItem('collab_preferredCamId', preferredCamId);
        restartMediaForDeviceChange().catch(err => console.error('[Media] webcam switch failed:', err));
    });

    videoStrip.addEventListener('click', (e) => {
        const btn = e.target.closest('.remote-control-btn');
        if (!btn) return;

        unlockAllAudio();
        const token = btn.dataset.token;
        const stream = remoteStreams[token];

        if (btn.classList.contains('mute-audio')) {
            if (!stream) return;
            stream.audioMuted = !stream.audioMuted;
            if (stream.publicId && ws && ws.setAudioActive) ws.setAudioActive(stream.publicId, !stream.audioMuted);
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
    initDock();
    renderRoom();
    connectWebSocket();
    updateSpeakingIndicators();

    document.body.addEventListener('click', initNotificationAudio, { once: true });
    document.body.addEventListener('keydown', initNotificationAudio, { once: true });

    if (window.history.replaceState) {
        const url = new URL(window.location);
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
        iframeEl.addEventListener('load', sendVolumeToIframe);
    }

    document.addEventListener('fullscreenchange', () => {
        if (!document.fullscreenElement && COLLAB_DATA.userRole !== 'controller' && ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ action: 'force_cursor_render', state: 0 }));
        }
    });
});
