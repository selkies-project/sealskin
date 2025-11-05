document.addEventListener('DOMContentLoaded', () => {
    const COLLAB_DATA = window.COLLAB_DATA;
    if (!COLLAB_DATA) {
        console.error("Collaboration data not found.");
        return;
    }

    let ws;
    let username = localStorage.getItem(`collab_username_${COLLAB_DATA.sessionId}`);
    let isSidebarVisible = false;

    const contentEl = document.getElementById('content');
    const sidebarEl = document.getElementById('sidebar');
    const toggleHandle = document.getElementById('sidebar-toggle-handle');
    const settingsModalOverlay = document.getElementById('settings-modal-overlay');
    const settingsModalCloseBtn = document.getElementById('settings-modal-close');

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
        contentEl.classList.toggle('sidebar-visible', isSidebarVisible);
        toggleHandle.classList.toggle('is-open', isSidebarVisible);
    };

    const connectWebSocket = () => {
        const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const url = `${proto}//${window.location.host}/ws/room/${COLLAB_DATA.sessionId}?token=${COLLAB_DATA.userToken}`;
        ws = new WebSocket(url);

        ws.onopen = () => {
            console.log('Collaboration WebSocket connected.');
            if (username) {
                ws.send(JSON.stringify({ action: 'set_username', username: username }));
            }
            renderSidebar();
        };

        ws.onmessage = (event) => {
            const data = JSON.parse(event.data);
            switch (data.type) {
                case 'state_update':
                    if (COLLAB_DATA.userRole === 'controller') {
                        renderControllerUsers(data.viewers);
                    }
                    break;
                case 'chat_message':
                    appendChatMessage(data);
                    break;
            }
        };

        ws.onclose = () => {
            console.log('WebSocket closed. Reconnecting in 5s...');
            setTimeout(connectWebSocket, 5000);
        };
        ws.onerror = (err) => console.error('WebSocket error:', err);
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
                        <div class="icon sun-icon"><svg viewBox="0 0 24 24"><path d="M12 2.25a.75.75 0 01.75.75v2.25a.75.75 0 01-1.5 0V3a.75.75 0 01.75-.75zM7.5 12a4.5 4.5 0 119 0 4.5 4.5 0 01-9 0zM18.894 6.106a.75.75 0 010 1.06l-1.591 1.59a.75.75 0 11-1.06-1.06l1.59-1.59a.75.75 0 011.06 0zM21.75 12a.75.75 0 01-.75.75h-2.25a.75.75 0 010-1.5h2.25a.75.75 0 01.75.75zM17.836 17.836a.75.75 0 01-1.06 0l-1.59-1.591a.75.75 0 111.06-1.06l1.59 1.59a.75.75 0 010 1.061zM12 21.75a.75.75 0 01-.75-.75v-2.25a.75.75 0 011.5 0v2.25a.75.75 0 01-.75.75zM5.636 17.836a.75.75 0 010-1.06l1.591-1.59a.75.75 0 111.06 1.06l-1.59 1.59a.75.75 0 01-1.06 0zM3.75 12a.75.75 0 01.75-.75h2.25a.75.75 0 010 1.5H4.5a.75.75 0 01-.75-.75zM6.106 6.106a.75.75 0 011.06 0l1.59 1.591a.75.75 0 11-1.06 1.06l-1.59-1.59a.75.75 0 010-1.06z"/></svg></div>
                        <div class="icon moon-icon"><svg viewBox="0 0 24 24"><path d="M21.752 15.002A9.718 9.718 0 0118 15.75c-5.385 0-9.75-4.365-9.75-9.75 0-1.33.266-2.597.748-3.752A9.753 9.753 0 003 11.25C3 16.635 7.365 21 12.75 21c3.73 0 7.01-1.939 8.71-4.922.482-.97.74-2.053.742-3.176z"/></svg></div>
                    </div>
                    <button id="settings-btn" class="settings-button"><i class="fas fa-cog"></i></button>
                </div>
            </div>
            <div id="sidebar-main-content" class="sidebar-content"></div>
            <div class="sidebar-footer">
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
        } else {
            renderViewerView();
        }
        
        sidebarEl.querySelector('.theme-toggle').addEventListener('click', toggleTheme);
        sidebarEl.querySelector('#settings-btn').addEventListener('click', () => settingsModalOverlay.classList.remove('hidden'));
        sidebarEl.querySelector('#chat-form').addEventListener('submit', handleChatSubmit);
    };

    const renderControllerView = () => {
        const container = document.getElementById('sidebar-main-content');
        container.innerHTML = `
            <div class="controller-tabs">
                <button class="tab-btn active" data-tab="chat">Chat</button>
                <button class="tab-btn" data-tab="users">Users</button>
            </div>
            <div id="tab-chat" class="tab-content active"><div id="chat-messages"></div></div>
            <div id="tab-users" class="tab-content"><div id="viewer-list-container"></div></div>`;

        document.querySelectorAll('.tab-btn').forEach(btn => btn.addEventListener('click', handleTabSwitch));
    };

    const renderControllerUsers = (viewers) => {
        const container = document.getElementById('viewer-list-container');
        if (!container) return;
        
        if (viewers.length === 0) {
            container.innerHTML = '<p>No viewers have joined yet.</p>';
            return;
        }

        container.innerHTML = viewers.map(viewer => `
            <div class="viewer-item">
                <div class="viewer-info">
                    <div class="viewer-status ${viewer.online ? 'online' : ''}"></div>
                    <span class="viewer-name">${viewer.username || 'Unnamed'}</span>
                </div>
                <div class="slot-selector">
                    <select data-viewer-token="${viewer.token}">
                        <option value="null" ${viewer.slot === null ? 'selected' : ''}>No Gamepad</option>
                        ${[1,2,3,4].map(i => `<option value="${i}" ${viewer.slot === i ? 'selected' : ''}>Player ${i}</option>`).join('')}
                    </select>
                </div>
            </div>
        `).join('');

        document.querySelectorAll('.slot-selector select').forEach(select => {
            select.addEventListener('change', (e) => {
                ws.send(JSON.stringify({
                    action: 'assign_slot',
                    viewer_token: e.target.dataset.viewerToken,
                    slot: e.target.value === 'null' ? null : parseInt(e.target.value, 10)
                }));
            });
        });
    };
    
    const renderViewerView = () => {
        document.getElementById('sidebar-main-content').innerHTML = '<div id="chat-messages"></div>';
    };

    const appendChatMessage = (data) => {
        const messagesContainer = document.getElementById('chat-messages');
        if (!messagesContainer) return;

        const msgEl = document.createElement('div');
        const isSelf = data.sender === username || (COLLAB_DATA.userRole === 'controller' && data.sender === 'Controller');
        msgEl.className = `chat-message ${isSelf ? 'self' : 'other'}`;
        
        msgEl.innerHTML = `
            <div class="sender">${isSelf ? 'You' : data.sender}</div>
            <div class="bubble">${data.message.replace(/</g, "&lt;").replace(/>/g, "&gt;")}</div>
        `;
        messagesContainer.appendChild(msgEl);
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    };

    const handleUsernameSubmit = (e) => {
        e.preventDefault();
        const input = document.getElementById('username-input');
        const newUsername = input.value.trim();
        if (newUsername) {
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
            ws.send(JSON.stringify({ action: 'send_chat_message', message: input.value.trim() }));
            input.value = '';
        }
    };
    
    const handleTabSwitch = (e) => {
        const tabId = e.target.dataset.tab;
        document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));
        e.target.classList.add('active');
        document.getElementById(`tab-${tabId}`).classList.add('active');
    };

    const closeModal = () => settingsModalOverlay.classList.add('hidden');
    
    toggleHandle.addEventListener('click', toggleSidebar);
    settingsModalCloseBtn.addEventListener('click', closeModal);
    settingsModalOverlay.addEventListener('click', (e) => {
        if (e.target === settingsModalOverlay) closeModal();
    });

    initTheme();
    connectWebSocket();

    if (window.history.replaceState) {
        const url = new URL(window.location);
        url.searchParams.delete('token');
        url.searchParams.delete('access_token');
        window.history.replaceState({ path: url.href }, '', url.href);
    }

    setTimeout(toggleSidebar, 500);
});
