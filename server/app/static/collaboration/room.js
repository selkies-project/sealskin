if (typeof MediaStreamTrackProcessor === 'undefined') {
    window.MediaStreamTrackProcessor = class MediaStreamTrackProcessor {
        constructor({ track }) {
            if (track.kind === 'video') {
                this.readable = new ReadableStream({
                    start: async (controller) => {
                        const video = document.createElement('video');
                        video.muted = true;
                        video.playsInline = true;
                        video.width = 640; 
                        video.height = 480;
                        video.style.cssText = 'position:fixed; top:-9999px; left:-9999px;';
                        document.body.appendChild(video);

                        video.srcObject = new MediaStream([track]);
                        try { await video.play(); } catch (e) { return; }

                        const canvas = document.createElement('canvas');
                        const ctx = canvas.getContext('2d', { desynchronized: true });
                        
                        const process = () => {
                            if (track.readyState === 'ended') {
                                video.remove();
                                controller.close();
                                return;
                            }
                            if (video.readyState >= 2) {
                                if (canvas.width !== video.videoWidth || canvas.height !== video.videoHeight) {
                                    canvas.width = video.videoWidth;
                                    canvas.height = video.videoHeight;
                                }
                                ctx.drawImage(video, 0, 0);
                                const frame = new VideoFrame(canvas, { timestamp: performance.now() * 1000 });
                                controller.enqueue(frame);
                            }
                            if (video.requestVideoFrameCallback) {
                                video.requestVideoFrameCallback(process);
                            } else {
                                requestAnimationFrame(process);
                            }
                        };
                        process();
                    }
                });
            } else if (track.kind === 'audio') {
                this.readable = new ReadableStream({
                    async start(controller) {
                        const ctx = new AudioContext();
                        const workletCode = `registerProcessor("mstp-shim",class extends AudioWorkletProcessor{process(i){if(i[0].length>0)this.port.postMessage(i[0]);return true}})`
                        await ctx.audioWorklet.addModule(`data:text/javascript,${workletCode}`).catch(e => console.error(e));
                        
                        const src = ctx.createMediaStreamSource(new MediaStream([track]));
                        const node = new AudioWorkletNode(ctx, "mstp-shim");
                        src.connect(node);
                        
                        node.port.onmessage = ({data: channels}) => {
                             if (!channels || channels.length === 0) return;
                             
                             const length = channels[0].length;
                             const numChannels = channels.length;
                             const flattened = new Float32Array(length * numChannels);
                             
                             for (let i = 0; i < length; i++) {
                                 for (let ch = 0; ch < numChannels; ch++) {
                                     flattened[i * numChannels + ch] = channels[ch][i];
                                 }
                             }

                             controller.enqueue(new AudioData({
                                 format: "f32",
                                 sampleRate: ctx.sampleRate,
                                 numberOfFrames: length,
                                 numberOfChannels: numChannels,
                                 timestamp: ctx.currentTime * 1e6,
                                 data: flattened
                             }));
                        };
                    }
                });
            }
        }
    };
}

if (typeof MediaStreamTrackGenerator === 'undefined') {
    window.MediaStreamTrackGenerator = class MediaStreamTrackGenerator {
        constructor({ kind }) {
            if (kind === 'video') {
                const canvas = document.createElement('canvas');
                const ctx = canvas.getContext('2d', { desynchronized: true });
                const stream = canvas.captureStream(0);
                const track = stream.getVideoTracks()[0];
                track.writable = new WritableStream({
                    write(frame) {
                        if (canvas.width !== frame.displayWidth || canvas.height !== frame.displayHeight) {
                            canvas.width = frame.displayWidth;
                            canvas.height = frame.displayHeight;
                        }
                        ctx.drawImage(frame, 0, 0);
                        frame.close(); 
                    }
                });
                return track;
            }
        }
    };
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

            setupAudioEncoder();

            if (localStream.getVideoTracks().length > 0) {
                setupVideoEncoder();
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
        if (videoEncoder && videoEncoder.state !== 'closed') videoEncoder.close();
        if (audioEncoder && audioEncoder.state !== 'closed') audioEncoder.close();
        videoEncoder = null;
        audioEncoder = null;
        localAudioAnalyser = null;
        mediaInitialized = false;
    };

    const setupVideoEncoder = () => {
        const [videoTrack] = localStream.getVideoTracks();
        if (!videoTrack) return;

        const videoProcessor = new MediaStreamTrackProcessor({ track: videoTrack });
        const videoReader = videoProcessor.readable.getReader();
        
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

    const setupAudioEncoder = () => {
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

            const audioProcessor = new MediaStreamTrackProcessor({ track: audioTrack });
            const audioReader = audioProcessor.readable.getReader();

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
        const ctx = canvas.getContext('2d');
        ctx.fillStyle = '#222';
        ctx.fillRect(0, 0, 240, 180);
        
        let controllerControls = '';
        if (COLLAB_DATA.userRole === 'controller') {
            controllerControls = `<button class="remote-control-btn designate-speaker" data-token="${token}" title="${t('tooltips.designateSpeaker')}"><i class="fas fa-star"></i></button>`;
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
        container.appendChild(overlay);
        videoGridContent.appendChild(container);

        const videoDecoder = new VideoDecoder({
            output: (frame) => {
                ctx.drawImage(frame, 0, 0, canvas.width, canvas.height);
                frame.close();
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

        remoteStreams[token] = {
            username, videoDecoder, audioDecoder, audioContext, workletNode, container, analyser,
            videoMuted: false, audioMuted: false,
            isConfigured: true,
            hasReceivedKeyFrame: false
        };
    };

    const removeRemoteStream = (token) => {
        const stream = remoteStreams[token];
        if (stream) {
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

    const connectWebSocket = () => {
        const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const url = `${proto}//${window.location.host}/ws/room/${COLLAB_DATA.sessionId}?token=${COLLAB_DATA.userToken}`;
        ws = new WebSocket(url);
        ws.binaryType = 'arraybuffer';

        ws.onopen = () => {
            console.log('[WS] Collaboration WebSocket connected.');
            if (COLLAB_DATA.userRole === 'controller') {
                ws.send(JSON.stringify({ action: 'get_apps' }));
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
                case 'state_update':
                    const hasJoined = sessionStorage.getItem('collab_hasJoined_' + COLLAB_DATA.sessionId);
                    if (COLLAB_DATA.userRole === 'viewer' && !hasJoined) {
                        return;
                    }

                    currentUserState = data.viewers;
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
                    handleControllerDisconnect();
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
                case 'app_swapped':
                    pendingActions.clear();
                    const iframe = document.getElementById('session-frame');
                    const currentSrc = new URL(iframe.src);
                    currentSrc.searchParams.set('t', Date.now());
                    iframe.src = currentSrc.toString();
                    const titleEl = document.getElementById('sidebar-app-title');
                    if (titleEl) titleEl.textContent = data.app_name;
                    document.title = data.app_name;
                    ws.send(JSON.stringify({ action: 'get_apps' }));
                    showToast({ sender: t('systemMessages.systemSender'), message: t('systemMessages.swappedApp', { app_name: data.app_name }) });
                    break;
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
            localControls = `<button class="remote-control-btn designate-speaker" data-token="${COLLAB_DATA.userToken}" title="${t('tooltips.designateSpeaker')}"><i class="fas fa-star"></i></button>`;
        }
        document.querySelector('#local-user-container .video-overlay').innerHTML = `
            <span class="username">${isController ? 'Controller' : (username || 'You')}</span>
            <div class="remote-controls">${localControls}</div>`;


        sidebarEl.innerHTML = `
            <div class="sidebar-header">
                <h2 id="sidebar-app-title">${t('sidebar.title')}</h2>
                <div class="header-controls">
                    <div class="theme-toggle">
                        <div class="icon sun-icon"><svg viewBox="0 0 24 24"><path d="M12 2.25a.75.75 0 01.75.75v2.25a.75.75 0 01-1.5 0V3a.75.75 0 01.75-.75zM7.5 12a4.5 4.5 0 119 0 4.5 4.5 0 01-9 0zM18.894 6.106a.75.75 0 010 1.06l-1.591 1.59a.75.75 0 11-1.06-1.06l1.59-1.59a.75.75 0 011.06 0zM21.75 12a.75.75 0 01-.75.75h-2.25a.75.75 0 010-1.5h2.25a.75.75 0 01.75.75zM17.836 17.836a.75.75 0 01-1.06 0l-1.59-1.591a.75.75 0 111.06-1.06l1.59 1.59a.75.75 0 010 1.061zM12 21.75a.75.75 0 01-.75-.75v-2.25a.75.75 0 011.5 0v2.25a.75.75 0 01-.75-.75zM5.636 17.836a.75.75 0 010-1.06l1.591-1.59a.75.75 0 111.06 1.06l-1.59 1.59a.75.75 0 01-1.06 0zM3.75 12a.75.75 0 01.75-.75h2.25a.75.75 0 010 1.5H4.5a.75.75 0 01-.75-.75zM6.106 6.106a.75.75 0 011.06 0l1.59 1.591a.75.75 0 11-1.06 1.06l-1.59-1.59a.75.75 0 010-1.06z"/></svg></div>
                        <div class="icon moon-icon"><svg viewBox="0 0 24 24"><path d="M21.752 15.002A9.718 9.718 0 0118 15.75c-5.385 0-9.75-4.365-9.75-9.75 0-1.33.266-2.597.748-3.752A9.753 9.753 0 003 11.25C3 16.635 7.365 21 12.75 21c3.73 0 7.01-1.939 8.71-4.922.482-.97.74-2.053.742-3.176z"/></svg></div>
                    </div>
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

        sidebarEl.querySelector('.theme-toggle').addEventListener('click', toggleTheme);
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
                const canvas = stream.container.querySelector('canvas');
                if (canvas) {
                    const ctx = canvas.getContext('2d');
                    ctx.fillStyle = '#222';
                    ctx.fillRect(0, 0, canvas.width, canvas.height);
                }
            } else {
                stream.hasReceivedKeyFrame = false;
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

    setTimeout(toggleSidebar, 500);
});
