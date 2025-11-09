document.addEventListener('DOMContentLoaded', () => {
    const COLLAB_DATA = window.COLLAB_DATA;
    if (!COLLAB_DATA) {
        console.error("Collaboration data not found.");
        return;
    }

    if (COLLAB_DATA.userRole === 'viewer') {
        const sourceBox = document.getElementById('gamepad-source-box');
        if (sourceBox) {
            sourceBox.style.display = 'none';
        }
    }

    // --- Media & WebCodecs State ---
    let localStream = null;
    let audioEncoder = null;
    let videoEncoder = null;
    let remoteStreams = {};
    let mediaInitialized = false;
    let isMicOn = false;
    let isWebcamOn = false;
    let preferredMicId = null;
    let preferredCamId = null;
    let localAudioAnalyser = null;
    let animationFrameId = null;

    // --- WebSocket & UI State ---
    let ws;
    if (COLLAB_DATA.userRole === 'viewer') {
        localStorage.removeItem(`collab_username_${COLLAB_DATA.sessionId}`);
    }
    let username = localStorage.getItem(`collab_username_${COLLAB_DATA.sessionId}`);
    let isSidebarVisible = false;
    let messageStore = {};
    let replyingTo = null;
    let notificationAudioCtx;
    let gamepadIcons = {};
    const GAMEPAD_COUNT = 4;
    let currentUserState = [];

    // --- DOM Elements ---
    const sidebarEl = document.getElementById('sidebar');
    const toggleHandle = document.getElementById('sidebar-toggle-handle');
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
          this.MAX_BUFFER_PACKETS = 10; 

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
        if (!('VideoEncoder' in window && 'AudioEncoder' in window)) {
            alert("Your browser does not support WebCodecs, which is required for this feature.");
            return false;
        }
        if (mediaInitialized) {
            stopMedia(); 
        }

        try {
            const constraints = {
                audio: { 
                    deviceId: preferredMicId ? { exact: preferredMicId } : undefined, 
                    echoCancellation: true, 
                    noiseSuppression: true,
                    autoGainControl: true,
                    sampleRate: 48000,
                    channelCount: 1,
                },
                video: { 
                    deviceId: preferredCamId ? { exact: preferredCamId } : undefined, 
                    width: 320, 
                    height: 240, 
                    frameRate: 30 
                }
            };
            localStream = await navigator.mediaDevices.getUserMedia(constraints);
            localVideo.srcObject = localStream;
            
            localVideo.onloadedmetadata = () => {
                localVideo.play().catch(e => console.warn("Local video autoplay was blocked.", e));
            };

            const audioCtx = new AudioContext();
            const source = audioCtx.createMediaStreamSource(localStream);
            localAudioAnalyser = audioCtx.createAnalyser();
            localAudioAnalyser.fftSize = 512;
            source.connect(localAudioAnalyser);

            localStream.getAudioTracks().forEach(t => t.enabled = isMicOn);
            localStream.getVideoTracks().forEach(t => t.enabled = isWebcamOn);

            setupAudioEncoder();
            setupVideoEncoder();
            mediaInitialized = true;
            return true;
        } catch (err) {
            console.error("Error getting user media:", err);
            alert(`Could not access your camera or microphone: ${err.message}`);
            mediaInitialized = false;
            return false;
        }
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
                if (!ws || ws.readyState !== WebSocket.OPEN || !isWebcamOn) return;

                if (meta && meta.decoderConfig && meta.decoderConfig.description) {
                    const description = meta.decoderConfig.description;
                    const message = new Uint8Array(1 + description.byteLength);
                    message[0] = MSG_TYPE.VIDEO_CONFIG;
                    message.set(new Uint8Array(description), 1);
                    ws.send(message.buffer);
                }
                
                if (chunk.byteLength === 0) return;

                const isKeyFrame = chunk.type === 'key';
                const chunkData = new Uint8Array(chunk.byteLength + 2);
                chunkData[0] = MSG_TYPE.VIDEO_FRAME;
                chunkData[1] = isKeyFrame ? 0x01 : 0x00;
                chunk.copyTo(chunkData.subarray(2));
                ws.send(chunkData.buffer);
            },
            error: (e) => console.error('[Encoder] VideoEncoder error:', e),
        });

        videoEncoder.configure({
            codec: 'avc1.42001E',
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
                    const needsKeyFrame = (frameCounter % 90 === 0);
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
        const [audioTrack] = localStream.getAudioTracks();
        if (!audioTrack) return;

        const audioProcessor = new MediaStreamTrackProcessor({ track: audioTrack });
        const audioReader = audioProcessor.readable.getReader();

        audioEncoder = new AudioEncoder({
            output: (chunk, meta) => {
                if (ws && ws.readyState === WebSocket.OPEN && isMicOn) {
                    const chunkData = new Uint8Array(chunk.byteLength + 2);
                    chunkData[0] = MSG_TYPE.AUDIO_FRAME;
                    chunkData[1] = 0x00;
                    chunk.copyTo(chunkData.subarray(2));
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
                if(frame) frame.close();
                readFrame();
            }).catch(e => console.error("[Encoder] Audio reader error", e));
        };
        readFrame();
    };

    const handleRemoteStream = (token, data) => {
        const stream = remoteStreams[token];
        if (!stream) return;

        const mediaType = new Uint8Array(data, 0, 1)[0];

        try {
            switch (mediaType) {
                case MSG_TYPE.VIDEO_CONFIG:
                    const description = data.slice(1);
                    const config = { codec: 'avc1.42001E', description: description };
                    if (stream.videoDecoder.state !== 'closed') {
                        stream.videoDecoder.configure(config);
                        stream.isConfigured = true;
                    }
                    break;

                case MSG_TYPE.VIDEO_FRAME:
                    if (!stream.isConfigured || stream.videoDecoder.state !== 'configured' || stream.videoMuted) return;
                    const frameType = new Uint8Array(data, 1, 1)[0];
                    const isKeyFrame = frameType === 0x01;
                    if (!stream.hasReceivedKeyFrame) {
                        if (isKeyFrame) { stream.hasReceivedKeyFrame = true; } 
                        else { return; }
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
            }
        } catch (e) {
            console.error(`[Decoder:${token}] Error handling remote stream data:`, e);
        }
    };

    const addRemoteStream = async (token, username) => {
        if (remoteStreams[token]) return;
        console.log(`[System] Adding remote stream for user ${username} (${token})`);

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

        const overlay = document.createElement('div');
        overlay.className = 'video-overlay';
        overlay.innerHTML = `
            <span class="username">${username}</span>
            <div class="remote-controls">
                <button class="remote-control-btn mute-audio" data-token="${token}" title="Mute/Unmute Audio"><i class="fas fa-microphone"></i></button>
                <button class="remote-control-btn mute-video" data-token="${token}" title="Mute/Unmute Video"><i class="fas fa-video"></i></button>
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
        videoDecoder.configure({ codec: 'avc1.42001E' });

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
            isConfigured: false,
            hasReceivedKeyFrame: false
        };
    };

    const removeRemoteStream = (token) => {
        const stream = remoteStreams[token];
        if (stream) {
            console.log(`[System] Removing remote stream for user ${stream.username} (${token})`);
            if (stream.videoDecoder.state !== 'closed') stream.videoDecoder.close();
            if (stream.audioDecoder.state !== 'closed') stream.audioDecoder.close();
            if (stream.audioContext.state !== 'closed') {
                stream.audioContext.close();
            }
            stream.workletNode.disconnect();
            stream.container.remove();
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
                option.textContent = device.label || `${device.kind} device ${audioInputSelect.length + 1}`;
                if (device.kind === 'audioinput') {
                    audioInputSelect.appendChild(option);
                } else if (device.kind === 'videoinput') {
                    videoInputSelect.appendChild(option);
                }
            });
        } catch (err) {
            console.error("Could not enumerate devices:", err);
        }
    };
    
    const updateSpeakingIndicators = () => {
        const speakingThreshold = 5; 
        
        if (localAudioAnalyser && isMicOn) {
            const dataArray = new Uint8Array(localAudioAnalyser.frequencyBinCount);
            localAudioAnalyser.getByteFrequencyData(dataArray);
            const avg = dataArray.reduce((a, b) => a + b, 0) / dataArray.length;
            localContainer.classList.toggle('speaking', avg > speakingThreshold);
        } else {
            localContainer.classList.remove('speaking');
        }

        Object.values(remoteStreams).forEach(stream => {
            if (stream.analyser && !stream.audioMuted && stream.container) {
                const dataArray = new Uint8Array(stream.analyser.frequencyBinCount);
                stream.analyser.getByteFrequencyData(dataArray);
                const avg = dataArray.reduce((a, b) => a + b, 0) / dataArray.length;
                stream.container.classList.toggle('speaking', avg > speakingThreshold);
            } else if (stream.container) {
                stream.container.classList.remove('speaking');
            }
        });

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
        toggleHandle.classList.toggle('is-open', isSidebarVisible);
    };

    const connectWebSocket = () => {
        const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const url = `${proto}//${window.location.host}/ws/room/${COLLAB_DATA.sessionId}?token=${COLLAB_DATA.userToken}`;
        ws = new WebSocket(url);
        ws.binaryType = 'arraybuffer';

        ws.onopen = () => {
            console.log('[WS] Collaboration WebSocket connected.');
            if (username) {
                ws.send(JSON.stringify({ action: 'set_username', username: username }));
            }
            renderSidebar();
        };

        ws.onmessage = (event) => {
            if (event.data instanceof ArrayBuffer) {
                const dataView = new DataView(event.data);
                const tokenLen = dataView.getUint8(0);
                const token = new TextDecoder().decode(event.data.slice(1, 1 + tokenLen));
                const payload = event.data.slice(1 + tokenLen);
                handleRemoteStream(token, payload);
                return;
            }

            const data = JSON.parse(event.data);
            switch (data.type) {
                case 'state_update':
                    currentUserState = data.viewers;
                    const serverOnlineUsers = data.viewers.filter(u => u.online && u.token !== COLLAB_DATA.userToken);
                    const serverOnlineTokens = new Set(serverOnlineUsers.map(u => u.token));
                    const clientTokens = new Set(Object.keys(remoteStreams));

                    for (const token of clientTokens) {
                        if (!serverOnlineTokens.has(token)) {
                            removeRemoteStream(token);
                        }
                    }

                    for (const user of serverOnlineUsers) {
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
                    appendChatMessage(data, 'system');
                    break;
                case 'control':
                    handleControlMessage(data.payload);
                    break;
            }
        };

        ws.onclose = () => {
            console.log('[WS] WebSocket closed. Reconnecting in 5s...');
            Object.keys(remoteStreams).forEach(removeRemoteStream);
            setTimeout(connectWebSocket, 5000);
        };
        ws.onerror = (err) => console.error('[WS] WebSocket error:', err);
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
        if (COLLAB_DATA.userRole === 'viewer' && !username) {
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
                    <h3>Welcome!</h3>
                    <p>Please choose a username to join the session.</p>
                    <form id="username-form">
                        <input type="text" id="username-input" placeholder="Your Name" maxlength="25" required>
                        <button type="submit">Join</button>
                    </form>
                </div>
            </div>`;
        document.getElementById('username-form').addEventListener('submit', handleUsernameSubmit);
    };

    const renderMainSidebar = () => {
        const isController = COLLAB_DATA.userRole === 'controller';
        const titleHtml = isController ? `
            <div class="header-title-link">
                <input type="text" id="viewer-link-input" value="${COLLAB_DATA.viewerJoinUrl}" readonly>
                <button id="copy-link-btn"><i class="fas fa-copy"></i></button>
            </div>
        ` : `<h2>SealSkin</h2>`;

        sidebarEl.innerHTML = `
            <div class="sidebar-header">
                ${titleHtml}
                <div class="header-controls">
                    <div class="theme-toggle">
                        <div class="icon sun-icon"><svg viewBox="0 0 24 24"><path d="M12 2.25a.75.75 0 01.75.75v2.25a.75.75 0 01-1.5 0V3a.75.75 0 01.75-.75zM7.5 12a4.5 4.5 0 119 0 4.5 4.5 0 01-9 0zM18.894 6.106a.75.75 0 010 1.06l-1.591 1.59a.75.75 0 11-1.06-1.06l1.59-1.59a.75.75 0 011.06 0zM21.75 12a.75.75 0 01-.75.75h-2.25a.75.75 0 010-1.5h2.25a.75.75 0 01.75.75zM17.836 17.836a.75.75 0 01-1.06 0l-1.59-1.591a.75.75 0 111.06-1.06l1.59 1.59a.75.75 0 010 1.061zM12 21.75a.75.75 0 01-.75-.75v-2.25a.75.75 0 011.5 0v2.25a.75.75 0 01-.75-.75zM5.636 17.836a.75.75 0 010-1.06l1.591-1.59a.75.75 0 111.06 1.06l-1.59 1.59a.75.75 0 01-1.06 0zM3.75 12a.75.75 0 01.75-.75h2.25a.75.75 0 010 1.5H4.5a.75.75 0 01-.75-.75zM6.106 6.106a.75.75 0 011.06 0l1.59 1.591a.75.75 0 11-1.06 1.06l-1.59-1.59a.75.75 0 010-1.06z"/></svg></div>
                        <div class="icon moon-icon"><svg viewBox="0 0 24 24"><path d="M21.752 15.002A9.718 9.718 0 0118 15.75c-5.385 0-9.75-4.365-9.75-9.75 0-1.33.266-2.597.748-3.752A9.753 9.753 0 003 11.25C3 16.635 7.365 21 12.75 21c3.73 0 7.01-1.939 8.71-4.922.482-.97.74-2.053.742-3.176z"/></svg></div>
                    </div>
                    <button id="settings-btn" class="settings-button"><i class="fas fa-cog"></i></button>
                </div>
            </div>
            <div class="sidebar-media-controls">
                <button id="toggle-mic-btn" class="control-btn" title="Mute/Unmute Microphone">
                    <i class="fas fa-microphone"></i>
                </button>
                <button id="toggle-video-btn" class="control-btn" title="Start/Stop Webcam">
                    <i class="fas fa-video"></i>
                </button>
                <div class="iframe-audio-controls">
                    <button id="iframe-mute-btn" class="control-btn" title="Mute/Unmute Session Audio">
                        <i class="fas fa-volume-up"></i>
                    </button>
                    <input type="range" id="iframe-volume-slider" min="0" max="1" step="0.01" value="1" title="Session Volume">
                </div>
            </div>
            <div id="sidebar-main-content" class="sidebar-content"></div>
            <div id="chat-reply-banner"></div>
            <div id="chat-form-container">
                <form id="chat-form">
                    <input type="text" id="chat-input" placeholder="Type a message..." autocomplete="off" maxlength="500">
                    <button type="submit"><i class="fas fa-paper-plane"></i></button>
                </form>
            </div>`;

        if (isController) {
            renderControllerView();
            document.getElementById('copy-link-btn').addEventListener('click', () => {
                const input = document.getElementById('viewer-link-input');
                navigator.clipboard.writeText(input.value).then(() => {
                    const btn = document.getElementById('copy-link-btn');
                    const originalIcon = btn.innerHTML;
                    btn.innerHTML = '<i class="fas fa-check"></i>';
                    setTimeout(() => { btn.innerHTML = originalIcon; }, 2000);
                });
            });
            initGamepadControls();
        } else {
            renderViewerView();
            initGamepadControls();
        }

        toggleMicBtn = document.getElementById('toggle-mic-btn');
        toggleVideoBtn = document.getElementById('toggle-video-btn');
        iframeMuteBtn = document.getElementById('iframe-mute-btn');
        iframeVolumeSlider = document.getElementById('iframe-volume-slider');
        toggleMicBtn.addEventListener('click', () => handleMediaToggle('mic'));
        toggleVideoBtn.addEventListener('click', () => handleMediaToggle('video'));
        updateMediaButtonUI();

        const gameIframe = document.getElementById('session-frame');
        let isIframeMuted = false;
        let lastKnownVolume = parseFloat(localStorage.getItem(`iframe_volume_${COLLAB_DATA.sessionId}`)) || 1.0;
        iframeVolumeSlider.value = lastKnownVolume;

        gameIframe.addEventListener('load', () => {
            gameIframe.contentWindow.postMessage({ type: 'setVolume', value: lastKnownVolume }, '*');
        });

        iframeMuteBtn.addEventListener('click', () => {
            isIframeMuted = !isIframeMuted;
            
            gameIframe.contentWindow.postMessage({ type: 'setMute', value: isIframeMuted }, '*');

            iframeMuteBtn.querySelector('i').className = isIframeMuted ? 'fas fa-volume-mute' : 'fas fa-volume-up';
            
            if (isIframeMuted) {
                iframeVolumeSlider.value = 0;
            } else {
                iframeVolumeSlider.value = lastKnownVolume;
            }
        });

        iframeVolumeSlider.addEventListener('input', (e) => {
            const newVolume = parseFloat(e.target.value);
            
            gameIframe.contentWindow.postMessage({ type: 'setVolume', value: newVolume }, '*');

            if (newVolume > 0) {
                lastKnownVolume = newVolume;
                localStorage.setItem(`iframe_volume_${COLLAB_DATA.sessionId}`, lastKnownVolume);
                isIframeMuted = false;
                iframeMuteBtn.querySelector('i').className = 'fas fa-volume-up';
            } else {
                isIframeMuted = true;
                iframeMuteBtn.querySelector('i').className = 'fas fa-volume-mute';
            }
        });

        sidebarEl.querySelector('.theme-toggle').addEventListener('click', toggleTheme);
        sidebarEl.querySelector('#settings-btn').addEventListener('click', () => {
            unlockAllAudio();
            populateDeviceLists();
            settingsModalOverlay.classList.remove('hidden')
        });
        sidebarEl.querySelector('#chat-form').addEventListener('submit', handleChatSubmit);
        document.getElementById('sidebar-main-content').addEventListener('click', handleChatAreaClick);
    };

    const renderControllerView = () => {
        const mainContentEl = document.getElementById('sidebar-main-content');
        if (!mainContentEl) return;
        mainContentEl.innerHTML = `<div id="chat-messages" style="flex-grow: 1;"></div>`;
    };
    
    const renderViewerView = () => {
        document.getElementById('sidebar-main-content').innerHTML = '<div id="chat-messages"></div>';
    };
    
    const escapeHTML = (str) => str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#039;');

    const linkify = (text) => {
        const urlRegex = /(\b(https?|ftp|file):\/\/[-A-Z0-9+&@#\/%?=~_|!:,.;]*[-A-Z0-9+&@#\/%=~_|])/ig;
        return text.replace(urlRegex, (url) => `<a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>`);
    };

    const createMessageHTML = (data) => {
        const isSelf = data.sender === username || (COLLAB_DATA.userRole === 'controller' && data.sender === 'Controller');
        const senderName = isSelf ? 'You' : escapeHTML(data.sender);
        
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
                        <button class="reply-btn" title="Reply"><i class="fas fa-reply"></i></button>
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
                case 'user_joined': content = `<b>${escapeHTML(data.username)}</b> has joined the room.`; break;
                case 'user_left': content = `<b>${escapeHTML(data.username)}</b> has left the room.`; break;
                case 'username_changed': content = `<b>${escapeHTML(data.old_username)}</b> is now known as <b>${escapeHTML(data.new_username)}</b>.`; break;
                case 'gamepad_change': content = data.message; break;
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

    const handleUsernameSubmit = (e) => {
        e.preventDefault();
        const input = document.getElementById('username-input');
        const newUsername = input.value.trim();
        if (newUsername) {
            unlockAllAudio();
            username = newUsername;
            localStorage.setItem(`collab_username_${COLLAB_DATA.sessionId}`, username);
            ws.send(JSON.stringify({ action: 'set_username', username: username }));
            renderSidebar();
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
                <span class="reply-target-text">Replying to <b>${escapeHTML(replyingTo.sender)}</b></span>
                <button id="cancel-reply-btn" title="Cancel Reply">&times;</button>
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

    const handleMediaToggle = async (type) => {
        unlockAllAudio();

        if (!mediaInitialized) {
            const success = await startMedia();
            if (!success) return;
        }

        if (type === 'mic') {
            isMicOn = !isMicOn;
            if (localStream) localStream.getAudioTracks().forEach(t => t.enabled = isMicOn);
            sendControlMessage('audio_state', isMicOn);
        } else if (type === 'video') {
            isWebcamOn = !isWebcamOn;
            if (localStream) localStream.getVideoTracks().forEach(t => t.enabled = isWebcamOn);
            localContainer.style.display = isWebcamOn ? 'flex' : 'none';
            sendControlMessage('video_state', isWebcamOn);
        }

        updateMediaButtonUI();
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
        });

        for (let i = 1; i <= GAMEPAD_COUNT; i++) {
            if (!assignedGamepadIds.has(i)) {
                const icon = gamepadIcons[i];
                if (icon && icon.parentElement !== sourceBox) {
                    sourceBox.appendChild(icon);
                }
            }
        }
    };

    // --- Drag and Drop Logic ---
    let draggedElement = null;

    videoGridContent.addEventListener('dragstart', (e) => {
        const target = e.target.closest('.draggable, .reorderable');
        if (!target) {
            e.preventDefault();
            return;
        }
        draggedElement = target;

        if (target.classList.contains('gamepad-icon')) {
            document.body.classList.add('dragging-gamepad');
            e.dataTransfer.setData('text/plain', target.dataset.gamepadId);
            e.dataTransfer.effectAllowed = 'move';
            
            setTimeout(() => target.classList.add('dragging'), 0);
            
            document.querySelectorAll('.video-container').forEach(container => {
                const userToken = container.dataset.userToken;
                const user = currentUserState.find(u => u.token === userToken);
                if (container.id === 'gamepad-source-box' || (user && user.slot === null)) {
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

    videoGridContent.addEventListener('dragend', (e) => {
        document.body.className = '';
        draggedElement?.classList.remove('dragging', 'reordering');
        document.querySelectorAll('.can-drop-gamepad').forEach(el => el.classList.remove('can-drop-gamepad'));
        draggedElement = null;
    });

    videoGridContent.addEventListener('dragover', (e) => {
        e.preventDefault();
        const dropTarget = e.target.closest('.video-container');

        if (document.body.classList.contains('dragging-gamepad')) {
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

    videoGridContent.addEventListener('drop', (e) => {
        e.preventDefault();
        if (!document.body.classList.contains('dragging-gamepad')) return;

        const dropTarget = e.target.closest('.video-container.can-drop-gamepad');
        if (!dropTarget) return;

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
    });

    // --- Bottom Bar Scroll Logic ---
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

    // --- Event Listeners ---
    toggleHandle.addEventListener('click', toggleSidebar);
    settingsModalCloseBtn.addEventListener('click', closeModal);
    settingsModalOverlay.addEventListener('click', (e) => {
        if (e.target === settingsModalOverlay) closeModal();
    });

    audioInputSelect.addEventListener('change', (e) => {
        preferredMicId = e.target.value;
        if(mediaInitialized) startMedia();
    });
    videoInputSelect.addEventListener('change', (e) => {
        preferredCamId = e.target.value;
        if(mediaInitialized) startMedia();
    });
    
    videoGrid.addEventListener('click', (e) => {
        const btn = e.target.closest('.remote-control-btn');
        if (!btn) return;
    
        unlockAllAudio();
        const token = btn.dataset.token;
        const stream = remoteStreams[token];
        if (!stream) return;
    
        if (btn.classList.contains('mute-audio')) {
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
        }
    });

    // --- Initializations ---
    initTheme();
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
