const en = {
    pageTitle: 'SealSkin Collaboration',
    localUsername: 'You',
    settings: {
        title: 'Settings',
        microphoneLabel: 'Microphone',
        webcamLabel: 'Webcam',
    },
    alerts: {
        webcodecsUnsupported: 'Your browser does not support WebCodecs, which is required for this feature.',
        mediaAccessError: 'Could not access your camera or microphone: {message}',
    },
    devices: {
        unlabeledDevice: '{kind} device {number}',
    },
    tooltips: {
        toggleRemoteAudio: 'Mute/Unmute Audio',
        toggleRemoteVideo: 'Mute/Unmute Video',
        toggleLocalMic: 'Mute/Unmute Microphone',
        toggleLocalWebcam: 'Start/Stop Webcam',
        toggleSessionAudio: 'Mute/Unmute Session Audio',
        sessionVolume: 'Session Volume',
        reply: 'Reply',
        cancelReply: 'Cancel Reply',
        designateSpeaker: 'Designate as Speaker',
        stopApp: 'Stop',
        restartApp: 'Restart',
        swapApp: 'Swap to this app',
        cannotStopActive: 'Cannot stop active app',
        reloadStream: 'Reload Stream',
    },
    usernamePrompt: {
        title: 'Welcome!',
        description: 'Please choose a username to join the session.',
        placeholder: 'Your Name',
        joinButton: 'Join',
    },
    sidebar: {
        title: 'SealSkin',
    },
    chat: {
        inputPlaceholder: 'Type a message...',
        selfUsername: 'You',
        replyingTo: 'Replying to <b>{sender}</b>',
    },
    systemMessages: {
        userJoined: '<b>{username}</b> has joined the room.',
        userLeft: '<b>{username}</b> has left the room.',
        usernameChanged: '<b>{old_username}</b> is now known as <b>{new_username}</b>.',
        swappedApp: 'Swapped to application: {app_name}',
        systemSender: 'System',
    },
    inviteLinks: {
        participant: 'Collaboration User Invite',
        readonly: 'Read Only User Invite',
        readonlyParticipantView: 'Read Only Invite',
    },
    disconnect: {
        title: 'Disconnected',
        message: 'The session has ended.',
    },
    waiting: {
        title: 'Controller is Away',
        message: 'The session is active. Waiting for the controller to resume the stream.',
    },
    startMenu: {
        launchTab: 'Launch New',
        activeTab: 'Active Sessions',
        searchPlaceholder: 'Search apps...',
        appsButton: 'APPS',
        appsTitle: 'Applications',
        loading: 'Loading...',
        activeVisible: 'Active (Visible)',
        runningBackground: 'Running (Background)',
    },
};

// Spanish
const es = {
    pageTitle: 'Colaboración SealSkin',
    localUsername: 'Tú',
    settings: {
        title: 'Ajustes',
        microphoneLabel: 'Micrófono',
        webcamLabel: 'Cámara web',
    },
    alerts: {
        webcodecsUnsupported: 'Tu navegador no es compatible con WebCodecs, que es necesario para esta función.',
        mediaAccessError: 'No se pudo acceder a tu cámara o micrófono: {message}',
    },
    devices: {
        unlabeledDevice: 'Dispositivo {kind} {number}',
    },
    tooltips: {
        toggleRemoteAudio: 'Silenciar/Activar audio',
        toggleRemoteVideo: 'Silenciar/Activar video',
        toggleLocalMic: 'Silenciar/Activar micrófono',
        toggleLocalWebcam: 'Iniciar/Detener cámara web',
        toggleSessionAudio: 'Silenciar/Activar audio de la sesión',
        sessionVolume: 'Volumen de la sesión',
        reply: 'Responder',
        cancelReply: 'Cancelar respuesta',
        designateSpeaker: 'Designar como Orador',
        stopApp: 'Detener',
        restartApp: 'Reiniciar',
        swapApp: 'Cambiar a esta app',
        cannotStopActive: 'No se puede detener la app activa',
        reloadStream: 'Recargar transmisión',
    },
    usernamePrompt: {
        title: '¡Bienvenido!',
        description: 'Por favor, elige un nombre de usuario para unirte a la sesión.',
        placeholder: 'Tu nombre',
        joinButton: 'Unirse',
    },
    sidebar: {
        title: 'SealSkin',
    },
    chat: {
        inputPlaceholder: 'Escribe un mensaje...',
        selfUsername: 'Tú',
        replyingTo: 'Respondiendo a <b>{sender}</b>',
    },
    systemMessages: {
        userJoined: '<b>{username}</b> se ha unido a la sala.',
        userLeft: '<b>{username}</b> ha abandonado la sala.',
        usernameChanged: '<b>{old_username}</b> ahora es conocido como <b>{new_username}</b>.',
        swappedApp: 'Cambiado a la aplicación: {app_name}',
        systemSender: 'Sistema',
    },
    inviteLinks: {
        participant: 'Invitación de Colaborador',
        readonly: 'Invitación de Solo Lectura',
        readonlyParticipantView: 'Invitación de Solo Lectura',
    },
    disconnect: {
        title: 'Desconectado',
        message: 'La sesión ha finalizado.',
    },
    waiting: {
        title: 'El controlador está ausente',
        message: 'La sesión está activa. Esperando a que el controlador reanude la transmisión.',
    },
    startMenu: {
        launchTab: 'Iniciar Nueva',
        activeTab: 'Sesiones Activas',
        searchPlaceholder: 'Buscar apps...',
        appsButton: 'APPS',
        appsTitle: 'Aplicaciones',
        loading: 'Cargando...',
        activeVisible: 'Activo (Visible)',
        runningBackground: 'Ejecutando (Segundo plano)',
    },
};

// Chinese (Simplified)
const zh = {
    pageTitle: 'SealSkin 协作',
    localUsername: '您',
    settings: {
        title: '设置',
        microphoneLabel: '麦克风',
        webcamLabel: '网络摄像头',
    },
    alerts: {
        webcodecsUnsupported: '您的浏览器不支持 WebCodecs，此功能需要该技术。',
        mediaAccessError: '无法访问您的摄像头或麦克风：{message}',
    },
    devices: {
        unlabeledDevice: '{kind} 设备 {number}',
    },
    tooltips: {
        toggleRemoteAudio: '静音/取消静音音频',
        toggleRemoteVideo: '静音/取消静音视频',
        toggleLocalMic: '静音/取消静音麦克风',
        toggleLocalWebcam: '启动/停止网络摄像头',
        toggleSessionAudio: '静音/取消静音会话音频',
        sessionVolume: '会话音量',
        reply: '回复',
        cancelReply: '取消回复',
        designateSpeaker: '指定为发言人',
        stopApp: '停止',
        restartApp: '重启',
        swapApp: '切换到此应用',
        cannotStopActive: '无法停止活动应用',
        reloadStream: '重新加载流',
    },
    usernamePrompt: {
        title: '欢迎！',
        description: '请选择一个用户名以加入会话。',
        placeholder: '您的名字',
        joinButton: '加入',
    },
    sidebar: {
        title: 'SealSkin',
    },
    chat: {
        inputPlaceholder: '输入消息...',
        selfUsername: '您',
        replyingTo: '回复 <b>{sender}</b>',
    },
    systemMessages: {
        userJoined: '<b>{username}</b> 已加入房间。',
        userLeft: '<b>{username}</b> 已离开房间。',
        usernameChanged: '<b>{old_username}</b> 现已更名为 <b>{new_username}</b>。',
        swappedApp: '已切换到应用程序：{app_name}',
        systemSender: '系统',
    },
    inviteLinks: {
        participant: '协作用户邀请',
        readonly: '只读用户邀请',
        readonlyParticipantView: '只读邀请',
    },
    disconnect: {
        title: '已断开连接',
        message: '会话已结束。',
    },
    waiting: {
        title: '控制者已离开',
        message: '会话处于活动状态。正在等待控制者恢复流。',
    },
    startMenu: {
        launchTab: '启动新应用',
        activeTab: '活动会话',
        searchPlaceholder: '搜索应用...',
        appsButton: '应用',
        appsTitle: '应用程序',
        loading: '加载中...',
        activeVisible: '活动 (可见)',
        runningBackground: '运行中 (后台)',
    },
};

// Hindi
const hi = {
    pageTitle: 'SealSkin सहयोग',
    localUsername: 'आप',
    settings: {
        title: 'सेटिंग्स',
        microphoneLabel: 'माइक्रोफ़ोन',
        webcamLabel: 'वेबकैम',
    },
    alerts: {
        webcodecsUnsupported: 'आपका ब्राउज़र WebCodecs का समर्थन नहीं करता है, जो इस सुविधा के लिए आवश्यक है।',
        mediaAccessError: 'आपके कैमरे या माइक्रोफ़ोन तक नहीं पहुँच सका: {message}',
    },
    devices: {
        unlabeledDevice: '{kind} डिवाइस {number}',
    },
    tooltips: {
        toggleRemoteAudio: 'ऑडियो म्यूट/अनम्यूट करें',
        toggleRemoteVideo: 'वीडियो म्यूट/अनम्यूट करें',
        toggleLocalMic: 'माइक्रोफ़ोन म्यूट/अनम्यूट करें',
        toggleLocalWebcam: 'वेबकैम शुरू/बंद करें',
        toggleSessionAudio: 'सत्र ऑडियो म्यूट/अनम्यूट करें',
        sessionVolume: 'सत्र वॉल्यूम',
        reply: 'उत्तर दें',
        cancelReply: 'उत्तर रद्द करें',
        designateSpeaker: 'वक्ता के रूप में नामित करें',
        stopApp: 'रोकें',
        restartApp: 'पुनरारंभ करें',
        swapApp: 'इस ऐप पर स्वैप करें',
        cannotStopActive: 'सक्रिय ऐप को रोक नहीं सकते',
        reloadStream: 'स्ट्रीम पुनः लोड करें',
    },
    usernamePrompt: {
        title: 'स्वागत है!',
        description: 'सत्र में शामिल होने के लिए कृपया एक उपयोगकर्ता नाम चुनें।',
        placeholder: 'आपका नाम',
        joinButton: 'शामिल हों',
    },
    sidebar: {
        title: 'SealSkin',
    },
    chat: {
        inputPlaceholder: 'एक संदेश लिखें...',
        selfUsername: 'आप',
        replyingTo: '<b>{sender}</b> को उत्तर दे रहे हैं',
    },
    systemMessages: {
        userJoined: '<b>{username}</b> कमरे में शामिल हो गए हैं।',
        userLeft: '<b>{username}</b> ने कमरा छोड़ दिया है।',
        usernameChanged: '<b>{old_username}</b> को अब <b>{new_username}</b> के नाम से जाना जाता है।',
        swappedApp: 'एप्लिकेशन पर स्वैप किया गया: {app_name}',
        systemSender: 'सिस्टम',
    },
    inviteLinks: {
        participant: 'सहयोग उपयोगकर्ता आमंत्रण',
        readonly: 'केवल पढ़ने के लिए उपयोगकर्ता आमंत्रण',
        readonlyParticipantView: 'केवल पढ़ने के लिए आमंत्रण',
    },
    disconnect: {
        title: 'डिस्कनेक्ट हो गया',
        message: 'सत्र समाप्त हो गया है।',
    },
    waiting: {
        title: 'नियंत्रक अनुपस्थित है',
        message: 'सत्र सक्रिय है। नियंत्रक द्वारा स्ट्रीम फिर से शुरू करने की प्रतीक्षा की जा रही है।',
    },
    startMenu: {
        launchTab: 'नया लॉन्च करें',
        activeTab: 'सक्रिय सत्र',
        searchPlaceholder: 'ऐप्स खोजें...',
        appsButton: 'ऐप्स',
        appsTitle: 'अनुप्रयोग',
        loading: 'लोड हो रहा है...',
        activeVisible: 'सक्रिय (दृश्यमान)',
        runningBackground: 'चल रहा है (पृष्ठभूमि)',
    },
};

// Portuguese
const pt = {
    pageTitle: 'Colaboração SealSkin',
    localUsername: 'Você',
    settings: {
        title: 'Configurações',
        microphoneLabel: 'Microfone',
        webcamLabel: 'Webcam',
    },
    alerts: {
        webcodecsUnsupported: 'Seu navegador não suporta WebCodecs, que é necessário para este recurso.',
        mediaAccessError: 'Não foi possível acessar sua câmera ou microfone: {message}',
    },
    devices: {
        unlabeledDevice: 'Dispositivo {kind} {number}',
    },
    tooltips: {
        toggleRemoteAudio: 'Ativar/Desativar áudio',
        toggleRemoteVideo: 'Ativar/Desativar vídeo',
        toggleLocalMic: 'Ativar/Desativar microfone',
        toggleLocalWebcam: 'Iniciar/Parar webcam',
        toggleSessionAudio: 'Ativar/Desativar áudio da sessão',
        sessionVolume: 'Volume da sessão',
        reply: 'Responder',
        cancelReply: 'Cancelar resposta',
        designateSpeaker: 'Designar como Orador',
        stopApp: 'Parar',
        restartApp: 'Reiniciar',
        swapApp: 'Trocar para este app',
        cannotStopActive: 'Não é possível parar app ativo',
        reloadStream: 'Recarregar Transmissão',
    },
    usernamePrompt: {
        title: 'Bem-vindo(a)!',
        description: 'Por favor, escolha um nome de usuário para entrar na sessão.',
        placeholder: 'Seu nome',
        joinButton: 'Entrar',
    },
    sidebar: {
        title: 'SealSkin',
    },
    chat: {
        inputPlaceholder: 'Digite uma mensagem...',
        selfUsername: 'Você',
        replyingTo: 'Respondendo a <b>{sender}</b>',
    },
    systemMessages: {
        userJoined: '<b>{username}</b> entrou na sala.',
        userLeft: '<b>{username}</b> saiu da sala.',
        usernameChanged: '<b>{old_username}</b> agora é conhecido(a) como <b>{new_username}</b>.',
        swappedApp: 'Trocado para o aplicativo: {app_name}',
        systemSender: 'Sistema',
    },
    inviteLinks: {
        participant: 'Convite de Colaborador',
        readonly: 'Convite de Usuário Somente Leitura',
        readonlyParticipantView: 'Convite Somente Leitura',
    },
    disconnect: {
        title: 'Desconectado',
        message: 'A sessão terminou.',
    },
    waiting: {
        title: 'O controlador está ausente',
        message: 'A sessão está ativa. Aguardando o controlador retomar a transmissão.',
    },
    startMenu: {
        launchTab: 'Iniciar Novo',
        activeTab: 'Sessões Ativas',
        searchPlaceholder: 'Pesquisar apps...',
        appsButton: 'APPS',
        appsTitle: 'Aplicativos',
        loading: 'Carregando...',
        activeVisible: 'Ativo (Visível)',
        runningBackground: 'Executando (Segundo Plano)',
    },
};

// French
const fr = {
    pageTitle: 'Collaboration SealSkin',
    localUsername: 'Vous',
    settings: {
        title: 'Paramètres',
        microphoneLabel: 'Microphone',
        webcamLabel: 'Webcam',
    },
    alerts: {
        webcodecsUnsupported: 'Votre navigateur ne prend pas en charge WebCodecs, ce qui est requis pour cette fonctionnalité.',
        mediaAccessError: 'Impossible d\'accéder à votre caméra ou à votre microphone : {message}',
    },
    devices: {
        unlabeledDevice: 'Appareil {kind} {number}',
    },
    tooltips: {
        toggleRemoteAudio: 'Activer/Désactiver l\'audio',
        toggleRemoteVideo: 'Activer/Désactiver la vidéo',
        toggleLocalMic: 'Activer/Désactiver le microphone',
        toggleLocalWebcam: 'Démarrer/Arrêter la webcam',
        toggleSessionAudio: 'Activer/Désactiver l\'audio de la session',
        sessionVolume: 'Volume de la session',
        reply: 'Répondre',
        cancelReply: 'Annuler la réponse',
        designateSpeaker: 'Désigner comme Orateur',
        stopApp: 'Arrêter',
        restartApp: 'Redémarrer',
        swapApp: 'Basculer vers cette app',
        cannotStopActive: 'Impossible d\'arrêter l\'app active',
        reloadStream: 'Recharger le flux',
    },
    usernamePrompt: {
        title: 'Bienvenue !',
        description: 'Veuillez choisir un nom d\'utilisateur pour rejoindre la session.',
        placeholder: 'Votre nom',
        joinButton: 'Rejoindre',
    },
    sidebar: {
        title: 'SealSkin',
    },
    chat: {
        inputPlaceholder: 'Saisissez un message...',
        selfUsername: 'Vous',
        replyingTo: 'En réponse à <b>{sender}</b>',
    },
    systemMessages: {
        userJoined: '<b>{username}</b> a rejoint la salle.',
        userLeft: '<b>{username}</b> a quitté la salle.',
        usernameChanged: '<b>{old_username}</b> est maintenant connu(e) sous le nom de <b>{new_username}</b>.',
        swappedApp: 'Basculé vers l\'application : {app_name}',
        systemSender: 'Système',
    },
    inviteLinks: {
        participant: 'Invitation de Collaborateur',
        readonly: 'Invitation d\'Utilisateur en Lecture Seule',
        readonlyParticipantView: 'Invitation en Lecture Seule',
    },
    disconnect: {
        title: 'Déconnecté',
        message: 'La session est terminée.',
    },
    waiting: {
        title: 'Le contrôleur est absent',
        message: 'La session est active. En attente de la reprise du flux par le contrôleur.',
    },
    startMenu: {
        launchTab: 'Lancer nouveau',
        activeTab: 'Sessions actives',
        searchPlaceholder: 'Rechercher des apps...',
        appsButton: 'APPS',
        appsTitle: 'Applications',
        loading: 'Chargement...',
        activeVisible: 'Actif (Visible)',
        runningBackground: 'En cours (Arrière-plan)',
    },
};

// Russian
const ru = {
    pageTitle: 'Совместная работа SealSkin',
    localUsername: 'Вы',
    settings: {
        title: 'Настройки',
        microphoneLabel: 'Микрофон',
        webcamLabel: 'Веб-камера',
    },
    alerts: {
        webcodecsUnsupported: 'Ваш браузер не поддерживает WebCodecs, который необходим для этой функции.',
        mediaAccessError: 'Не удалось получить доступ к вашей камере или микрофону: {message}',
    },
    devices: {
        unlabeledDevice: 'Устройство {kind} {number}',
    },
    tooltips: {
        toggleRemoteAudio: 'Включить/выключить звук',
        toggleRemoteVideo: 'Включить/выключить видео',
        toggleLocalMic: 'Включить/выключить микрофон',
        toggleLocalWebcam: 'Запустить/остановить веб-камеру',
        toggleSessionAudio: 'Включить/выключить звук сеанса',
        sessionVolume: 'Громкость сеанса',
        reply: 'Ответить',
        cancelReply: 'Отменить ответ',
        designateSpeaker: 'Назначить докладчиком',
        stopApp: 'Остановить',
        restartApp: 'Перезапустить',
        swapApp: 'Переключиться на это приложение',
        cannotStopActive: 'Нельзя остановить активное приложение',
        reloadStream: 'Перезагрузить поток',
    },
    usernamePrompt: {
        title: 'Добро пожаловать!',
        description: 'Пожалуйста, выберите имя пользователя, чтобы присоединиться к сеансу.',
        placeholder: 'Ваше имя',
        joinButton: 'Присоединиться',
    },
    sidebar: {
        title: 'SealSkin',
    },
    chat: {
        inputPlaceholder: 'Введите сообщение...',
        selfUsername: 'Вы',
        replyingTo: 'Ответ пользователю <b>{sender}</b>',
    },
    systemMessages: {
        userJoined: '<b>{username}</b> присоединился(ась) к комнате.',
        userLeft: '<b>{username}</b> покинул(а) комнату.',
        usernameChanged: '<b>{old_username}</b> теперь известен(на) как <b>{new_username}</b>.',
        swappedApp: 'Переключено на приложение: {app_name}',
        systemSender: 'Система',
    },
    inviteLinks: {
        participant: 'Приглашение для участника совместной работы',
        readonly: 'Приглашение для пользователя с правами только на чтение',
        readonlyParticipantView: 'Приглашение только для чтения',
    },
    disconnect: {
        title: 'Отключено',
        message: 'Сеанс завершен.',
    },
    waiting: {
        title: 'Контроллер отошел',
        message: 'Сеанс активен. Ожидание возобновления трансляции контроллером.',
    },
    startMenu: {
        launchTab: 'Запустить новое',
        activeTab: 'Активные сеансы',
        searchPlaceholder: 'Поиск приложений...',
        appsButton: 'ПРИЛОЖЕНИЯ',
        appsTitle: 'Приложения',
        loading: 'Загрузка...',
        activeVisible: 'Активно (Видимо)',
        runningBackground: 'Работает (Фон)',
    },
};

// German
const de = {
    pageTitle: 'SealSkin Kollaboration',
    localUsername: 'Sie',
    settings: {
        title: 'Einstellungen',
        microphoneLabel: 'Mikrofon',
        webcamLabel: 'Webcam',
    },
    alerts: {
        webcodecsUnsupported: 'Ihr Browser unterstützt WebCodecs nicht, was für diese Funktion erforderlich ist.',
        mediaAccessError: 'Zugriff auf Ihre Kamera oder Ihr Mikrofon fehlgeschlagen: {message}',
    },
    devices: {
        unlabeledDevice: '{kind}-Gerät {number}',
    },
    tooltips: {
        toggleRemoteAudio: 'Audio stummschalten/aktivieren',
        toggleRemoteVideo: 'Video stummschalten/aktivieren',
        toggleLocalMic: 'Mikrofon stummschalten/aktivieren',
        toggleLocalWebcam: 'Webcam starten/stoppen',
        toggleSessionAudio: 'Sitzungs-Audio stummschalten/aktivieren',
        sessionVolume: 'Sitzungslautstärke',
        reply: 'Antworten',
        cancelReply: 'Antwort abbrechen',
        designateSpeaker: 'Als Sprecher festlegen',
        stopApp: 'Stopp',
        restartApp: 'Neustart',
        swapApp: 'Zu dieser App wechseln',
        cannotStopActive: 'Aktive App kann nicht gestoppt werden',
        reloadStream: 'Stream neu laden',
    },
    usernamePrompt: {
        title: 'Willkommen!',
        description: 'Bitte wählen Sie einen Benutzernamen, um der Sitzung beizutreten.',
        placeholder: 'Ihr Name',
        joinButton: 'Beitreten',
    },
    sidebar: {
        title: 'SealSkin',
    },
    chat: {
        inputPlaceholder: 'Nachricht eingeben...',
        selfUsername: 'Sie',
        replyingTo: 'Antwort an <b>{sender}</b>',
    },
    systemMessages: {
        userJoined: '<b>{username}</b> ist dem Raum beigetreten.',
        userLeft: '<b>{username}</b> hat den Raum verlassen.',
        usernameChanged: '<b>{old_username}</b> ist jetzt als <b>{new_username}</b> bekannt.',
        swappedApp: 'Zu Anwendung gewechselt: {app_name}',
        systemSender: 'System',
    },
    inviteLinks: {
        participant: 'Einladung für Kollaborationsbenutzer',
        readonly: 'Einladung für Benutzer mit Leseberechtigung',
        readonlyParticipantView: 'Einladung mit Leseberechtigung',
    },
    disconnect: {
        title: 'Verbindung getrennt',
        message: 'Die Sitzung wurde beendet.',
    },
    waiting: {
        title: 'Controller ist abwesend',
        message: 'Die Sitzung ist aktiv. Warten auf Wiederaufnahme des Streams durch den Controller.',
    },
    startMenu: {
        launchTab: 'Neu starten',
        activeTab: 'Aktive Sitzungen',
        searchPlaceholder: 'Apps suchen...',
        appsButton: 'APPS',
        appsTitle: 'Anwendungen',
        loading: 'Laden...',
        activeVisible: 'Aktiv (Sichtbar)',
        runningBackground: 'Läuft (Hintergrund)',
    },
};

// Turkish
const tr = {
    pageTitle: 'SealSkin İşbirliği',
    localUsername: 'Siz',
    settings: {
        title: 'Ayarlar',
        microphoneLabel: 'Mikrofon',
        webcamLabel: 'Web Kamerası',
    },
    alerts: {
        webcodecsUnsupported: 'Tarayıcınız bu özellik için gerekli olan WebCodecs\'i desteklemiyor.',
        mediaAccessError: 'Kameranıza veya mikrofonunuza erişilemedi: {message}',
    },
    devices: {
        unlabeledDevice: '{kind} cihazı {number}',
    },
    tooltips: {
        toggleRemoteAudio: 'Sesi Aç/Kapat',
        toggleRemoteVideo: 'Videoyu Aç/Kapat',
        toggleLocalMic: 'Mikrofonu Aç/Kapat',
        toggleLocalWebcam: 'Web Kamerasını Başlat/Durdur',
        toggleSessionAudio: 'Oturum Sesini Aç/Kapat',
        sessionVolume: 'Oturum Sesi',
        reply: 'Yanıtla',
        cancelReply: 'Yanıtı İptal Et',
        designateSpeaker: 'Konuşmacı Olarak Belirle',
        stopApp: 'Durdur',
        restartApp: 'Yeniden Başlat',
        swapApp: 'Bu uygulamaya geç',
        cannotStopActive: 'Aktif uygulama durdurulamaz',
        reloadStream: 'Yayını Yenile',
    },
    usernamePrompt: {
        title: 'Hoş geldiniz!',
        description: 'Oturuma katılmak için lütfen bir kullanıcı adı seçin.',
        placeholder: 'Adınız',
        joinButton: 'Katıl',
    },
    sidebar: {
        title: 'SealSkin',
    },
    chat: {
        inputPlaceholder: 'Bir mesaj yazın...',
        selfUsername: 'Siz',
        replyingTo: '<b>{sender}</b> adlı kişiye yanıt veriliyor',
    },
    systemMessages: {
        userJoined: '<b>{username}</b> odaya katıldı.',
        userLeft: '<b>{username}</b> odadan ayrıldı.',
        usernameChanged: '<b>{old_username}</b> artık <b>{new_username}</b> olarak biliniyor.',
        swappedApp: 'Uygulamaya geçildi: {app_name}',
        systemSender: 'Sistem',
    },
    inviteLinks: {
        participant: 'İşbirliği Kullanıcı Daveti',
        readonly: 'Salt Okunur Kullanıcı Daveti',
        readonlyParticipantView: 'Salt Okunur Davet',
    },
    disconnect: {
        title: 'Bağlantı Kesildi',
        message: 'Oturum sona erdi.',
    },
    waiting: {
        title: 'Kontrolcü Uzakta',
        message: 'Oturum aktif. Kontrolcünün yayını sürdürmesi bekleniyor.',
    },
    startMenu: {
        launchTab: 'Yeni Başlat',
        activeTab: 'Aktif Oturumlar',
        searchPlaceholder: 'Uygulamaları ara...',
        appsButton: 'UYGULAMALAR',
        appsTitle: 'Uygulamalar',
        loading: 'Yükleniyor...',
        activeVisible: 'Aktif (Görünür)',
        runningBackground: 'Çalışıyor (Arka Plan)',
    },
};

// Italian
const it = {
    pageTitle: 'Collaborazione SealSkin',
    localUsername: 'Tu',
    settings: {
        title: 'Impostazioni',
        microphoneLabel: 'Microfono',
        webcamLabel: 'Webcam',
    },
    alerts: {
        webcodecsUnsupported: 'Il tuo browser non supporta WebCodecs, necessario per questa funzionalità.',
        mediaAccessError: 'Impossibile accedere alla tua fotocamera o al tuo microfono: {message}',
    },
    devices: {
        unlabeledDevice: 'Dispositivo {kind} {number}',
    },
    tooltips: {
        toggleRemoteAudio: 'Attiva/Disattiva audio',
        toggleRemoteVideo: 'Attiva/Disattiva video',
        toggleLocalMic: 'Attiva/Disattiva microfono',
        toggleLocalWebcam: 'Avvia/Interrompi webcam',
        toggleSessionAudio: 'Attiva/Disattiva audio della sessione',
        sessionVolume: 'Volume della sessione',
        reply: 'Rispondi',
        cancelReply: 'Annulla risposta',
        designateSpeaker: 'Designa come Relatore',
        stopApp: 'Stop',
        restartApp: 'Riavvia',
        swapApp: 'Passa a questa app',
        cannotStopActive: 'Impossibile fermare app attiva',
        reloadStream: 'Ricarica Stream',
    },
    usernamePrompt: {
        title: 'Benvenuto!',
        description: 'Scegli un nome utente per partecipare alla sessione.',
        placeholder: 'Il tuo nome',
        joinButton: 'Partecipa',
    },
    sidebar: {
        title: 'SealSkin',
    },
    chat: {
        inputPlaceholder: 'Scrivi un messaggio...',
        selfUsername: 'Tu',
        replyingTo: 'In risposta a <b>{sender}</b>',
    },
    systemMessages: {
        userJoined: '<b>{username}</b> è entrato/a nella stanza.',
        userLeft: '<b>{username}</b> ha lasciato la stanza.',
        usernameChanged: '<b>{old_username}</b> è ora conosciuto/a come <b>{new_username}</b>.',
        swappedApp: 'Passato all\'applicazione: {app_name}',
        systemSender: 'Sistema',
    },
    inviteLinks: {
        participant: 'Invito Utente Collaboratore',
        readonly: 'Invito Utente Sola Lettura',
        readonlyParticipantView: 'Invito Sola Lettura',
    },
    disconnect: {
        title: 'Disconnesso',
        message: 'La sessione è terminata.',
    },
    waiting: {
        title: 'Il controllore è assente',
        message: 'La sessione è attiva. In attesa che il controllore riprenda lo stream.',
    },
    startMenu: {
        launchTab: 'Avvia Nuovo',
        activeTab: 'Sessioni Attive',
        searchPlaceholder: 'Cerca app...',
        appsButton: 'APP',
        appsTitle: 'Applicazioni',
        loading: 'Caricamento...',
        activeVisible: 'Attivo (Visibile)',
        runningBackground: 'In esecuzione (Background)',
    },
};

// Dutch
const nl = {
    pageTitle: 'SealSkin Samenwerking',
    localUsername: 'Jij',
    settings: {
        title: 'Instellingen',
        microphoneLabel: 'Microfoon',
        webcamLabel: 'Webcam',
    },
    alerts: {
        webcodecsUnsupported: 'Uw browser ondersteunt geen WebCodecs, wat vereist is voor deze functie.',
        mediaAccessError: 'Kon geen toegang krijgen tot uw camera of microfoon: {message}',
    },
    devices: {
        unlabeledDevice: '{kind}-apparaat {number}',
    },
    tooltips: {
        toggleRemoteAudio: 'Audio dempen/dempen opheffen',
        toggleRemoteVideo: 'Video dempen/dempen opheffen',
        toggleLocalMic: 'Microfoon dempen/dempen opheffen',
        toggleLocalWebcam: 'Webcam starten/stoppen',
        toggleSessionAudio: 'Sessieaudio dempen/dempen opheffen',
        sessionVolume: 'Sessievolume',
        reply: 'Beantwoorden',
        cancelReply: 'Antwoord annuleren',
        designateSpeaker: 'Aanwijzen als spreker',
        stopApp: 'Stoppen',
        restartApp: 'Herstarten',
        swapApp: 'Wissel naar deze app',
        cannotStopActive: 'Kan actieve app niet stoppen',
        reloadStream: 'Stream herladen',
    },
    usernamePrompt: {
        title: 'Welkom!',
        description: 'Kies een gebruikersnaam om deel te nemen aan de sessie.',
        placeholder: 'Jouw naam',
        joinButton: 'Deelnemen',
    },
    sidebar: {
        title: 'SealSkin',
    },
    chat: {
        inputPlaceholder: 'Typ een bericht...',
        selfUsername: 'Jij',
        replyingTo: 'Antwoord op <b>{sender}</b>',
    },
    systemMessages: {
        userJoined: '<b>{username}</b> is de kamer binnengekomen.',
        userLeft: '<b>{username}</b> heeft de kamer verlaten.',
        usernameChanged: '<b>{old_username}</b> is nu bekend als <b>{new_username}</b>.',
        swappedApp: 'Gewisseld naar applicatie: {app_name}',
        systemSender: 'Systeem',
    },
    inviteLinks: {
        participant: 'Uitnodiging voor samenwerkingsgebruiker',
        readonly: 'Uitnodiging voor alleen-lezen gebruiker',
        readonlyParticipantView: 'Alleen-lezen uitnodiging',
    },
    disconnect: {
        title: 'Verbinding verbroken',
        message: 'De sessie is beëindigd.',
    },
    waiting: {
        title: 'Controller is afwezig',
        message: 'De sessie is actief. Wachten tot de controller de stream hervat.',
    },
    startMenu: {
        launchTab: 'Nieuwe starten',
        activeTab: 'Actieve sessies',
        searchPlaceholder: 'Apps zoeken...',
        appsButton: 'APPS',
        appsTitle: 'Applicaties',
        loading: 'Laden...',
        activeVisible: 'Actief (Zichtbaar)',
        runningBackground: 'Draait (Achtergrond)',
    },
};

// Arabic
const ar = {
    pageTitle: 'تعاون SealSkin',
    localUsername: 'أنت',
    settings: {
        title: 'الإعدادات',
        microphoneLabel: 'الميكروفون',
        webcamLabel: 'كاميرا الويب',
    },
    alerts: {
        webcodecsUnsupported: 'متصفحك لا يدعم WebCodecs، وهو مطلوب لهذه الميزة.',
        mediaAccessError: 'تعذر الوصول إلى الكاميرا أو الميكروفون: {message}',
    },
    devices: {
        unlabeledDevice: 'جهاز {kind} {number}',
    },
    tooltips: {
        toggleRemoteAudio: 'كتم/إلغاء كتم الصوت',
        toggleRemoteVideo: 'كتم/إلغاء كتم الفيديو',
        toggleLocalMic: 'كتم/إلغاء كتم الميكروفون',
        toggleLocalWebcam: 'بدء/إيقاف كاميرا الويب',
        toggleSessionAudio: 'كتم/إلغاء كتم صوت الجلسة',
        sessionVolume: 'مستوى صوت الجلسة',
        reply: 'رد',
        cancelReply: 'إلغاء الرد',
        designateSpeaker: 'تعيين كمتحدث',
        stopApp: 'إيقاف',
        restartApp: 'إعادة تشغيل',
        swapApp: 'التبديل إلى هذا التطبيق',
        cannotStopActive: 'لا يمكن إيقاف التطبيق النشط',
        reloadStream: 'إعادة تحميل البث',
    },
    usernamePrompt: {
        title: 'أهلاً بك!',
        description: 'الرجاء اختيار اسم مستخدم للانضمام إلى الجلسة.',
        placeholder: 'اسمك',
        joinButton: 'انضمام',
    },
    sidebar: {
        title: 'SealSkin',
    },
    chat: {
        inputPlaceholder: 'اكتب رسالة...',
        selfUsername: 'أنت',
        replyingTo: 'ردًا على <b>{sender}</b>',
    },
    systemMessages: {
        userJoined: '<b>{username}</b> انضم إلى الغرفة.',
        userLeft: '<b>{username}</b> غادر الغرفة.',
        usernameChanged: '<b>{old_username}</b> يُعرف الآن باسم <b>{new_username}</b>.',
        swappedApp: 'تم التبديل إلى التطبيق: {app_name}',
        systemSender: 'النظام',
    },
    inviteLinks: {
        participant: 'دعوة مستخدم متعاون',
        readonly: 'دعوة مستخدم للقراءة فقط',
        readonlyParticipantView: 'دعوة للقراءة فقط',
    },
    disconnect: {
        title: 'انقطع الاتصال',
        message: 'انتهت الجلسة.',
    },
    waiting: {
        title: 'المتحكم غائب',
        message: 'الجلسة نشطة. في انتظار استئناف البث من قبل المتحكم.',
    },
    startMenu: {
        launchTab: 'تشغيل جديد',
        activeTab: 'الجلسات النشطة',
        searchPlaceholder: 'البحث عن تطبيقات...',
        appsButton: 'تطبيقات',
        appsTitle: 'التطبيقات',
        loading: 'جاري التحميل...',
        activeVisible: 'نشط (مرئي)',
        runningBackground: 'قيد التشغيل (في الخلفية)',
    },
};

// Korean
const ko = {
    pageTitle: 'SealSkin 협업',
    localUsername: '나',
    settings: {
        title: '설정',
        microphoneLabel: '마이크',
        webcamLabel: '웹캠',
    },
    alerts: {
        webcodecsUnsupported: '브라우저가 이 기능에 필요한 WebCodecs를 지원하지 않습니다.',
        mediaAccessError: '카메라 또는 마이크에 액세스할 수 없습니다: {message}',
    },
    devices: {
        unlabeledDevice: '{kind} 장치 {number}',
    },
    tooltips: {
        toggleRemoteAudio: '오디오 음소거/음소거 해제',
        toggleRemoteVideo: '비디오 음소거/음소거 해제',
        toggleLocalMic: '마이크 음소거/음소거 해제',
        toggleLocalWebcam: '웹캠 시작/중지',
        toggleSessionAudio: '세션 오디오 음소거/음소거 해제',
        sessionVolume: '세션 볼륨',
        reply: '답장',
        cancelReply: '답장 취소',
        designateSpeaker: '발표자로 지정',
        stopApp: '중지',
        restartApp: '재시작',
        swapApp: '이 앱으로 전환',
        cannotStopActive: '활성 앱을 중지할 수 없음',
        reloadStream: '스트림 새로 고침',
    },
    usernamePrompt: {
        title: '환영합니다!',
        description: '세션에 참여하려면 사용자 이름을 선택하세요.',
        placeholder: '이름',
        joinButton: '참여',
    },
    sidebar: {
        title: 'SealSkin',
    },
    chat: {
        inputPlaceholder: '메시지를 입력하세요...',
        selfUsername: '나',
        replyingTo: '<b>{sender}</b>에게 답장 중',
    },
    systemMessages: {
        userJoined: '<b>{username}</b>님이 방에 참여했습니다.',
        userLeft: '<b>{username}</b>님이 방을 나갔습니다.',
        usernameChanged: '<b>{old_username}</b>님의 이름이 <b>{new_username}</b>(으)로 변경되었습니다.',
        swappedApp: '애플리케이션으로 전환됨: {app_name}',
        systemSender: '시스템',
    },
    inviteLinks: {
        participant: '협업 사용자 초대',
        readonly: '읽기 전용 사용자 초대',
        readonlyParticipantView: '읽기 전용 초대',
    },
    disconnect: {
        title: '연결 끊김',
        message: '세션이 종료되었습니다.',
    },
    waiting: {
        title: '컨트롤러 부재 중',
        message: '세션이 활성 상태입니다. 컨트롤러가 스트림을 재개하기를 기다리는 중입니다.',
    },
    startMenu: {
        launchTab: '새로 시작',
        activeTab: '활성 세션',
        searchPlaceholder: '앱 검색...',
        appsButton: '앱',
        appsTitle: '애플리케이션',
        loading: '로딩 중...',
        activeVisible: '활성 (표시됨)',
        runningBackground: '실행 중 (백그라운드)',
    },
};

// Japanese
const ja = {
    pageTitle: 'SealSkin コラボレーション',
    localUsername: 'あなた',
    settings: {
        title: '設定',
        microphoneLabel: 'マイク',
        webcamLabel: 'ウェブカメラ',
    },
    alerts: {
        webcodecsUnsupported: 'お使いのブラウザは、この機能に必要なWebCodecsをサポートしていません。',
        mediaAccessError: 'カメラまたはマイクにアクセスできませんでした：{message}',
    },
    devices: {
        unlabeledDevice: '{kind}デバイス{number}',
    },
    tooltips: {
        toggleRemoteAudio: '音声のミュート/ミュート解除',
        toggleRemoteVideo: 'ビデオのミュート/ミュート解除',
        toggleLocalMic: 'マイクのミュート/ミュート解除',
        toggleLocalWebcam: 'ウェブカメラの開始/停止',
        toggleSessionAudio: 'セッション音声のミュート/ミュート解除',
        sessionVolume: 'セッションの音量',
        reply: '返信',
        cancelReply: '返信をキャンセル',
        designateSpeaker: 'スピーカーに指定',
        stopApp: '停止',
        restartApp: '再起動',
        swapApp: 'このアプリに切り替え',
        cannotStopActive: 'アクティブなアプリは停止できません',
        reloadStream: 'ストリームを再読み込み',
    },
    usernamePrompt: {
        title: 'ようこそ！',
        description: 'セッションに参加するためのユーザー名を選択してください。',
        placeholder: 'あなたの名前',
        joinButton: '参加',
    },
    sidebar: {
        title: 'SealSkin',
    },
    chat: {
        inputPlaceholder: 'メッセージを入力...',
        selfUsername: 'あなた',
        replyingTo: '<b>{sender}</b>に返信中',
    },
    systemMessages: {
        userJoined: '<b>{username}</b>がルームに参加しました。',
        userLeft: '<b>{username}</b>がルームを退出しました。',
        usernameChanged: '<b>{old_username}</b>は<b>{new_username}</b>に名前を変更しました。',
        swappedApp: 'アプリケーションに切り替えました: {app_name}',
        systemSender: 'システム',
    },
    inviteLinks: {
        participant: 'コラボレーションユーザー招待',
        readonly: '読み取り専用ユーザー招待',
        readonlyParticipantView: '読み取り専用招待',
    },
    disconnect: {
        title: '切断されました',
        message: 'セッションは終了しました。',
    },
    waiting: {
        title: 'コントローラーが離席中',
        message: 'セッションはアクティブです。コントローラーがストリームを再開するのを待機しています。',
    },
    startMenu: {
        launchTab: '新規起動',
        activeTab: 'アクティブなセッション',
        searchPlaceholder: 'アプリを検索...',
        appsButton: 'アプリ',
        appsTitle: 'アプリケーション',
        loading: '読み込み中...',
        activeVisible: 'アクティブ (表示)',
        runningBackground: '実行中 (バックグラウンド)',
    },
};

// Vietnamese
const vi = {
    pageTitle: 'Hợp tác SealSkin',
    localUsername: 'Bạn',
    settings: {
        title: 'Cài đặt',
        microphoneLabel: 'Micrô',
        webcamLabel: 'Webcam',
    },
    alerts: {
        webcodecsUnsupported: 'Trình duyệt của bạn không hỗ trợ WebCodecs, yêu cầu cho tính năng này.',
        mediaAccessError: 'Không thể truy cập máy ảnh hoặc micrô của bạn: {message}',
    },
    devices: {
        unlabeledDevice: 'Thiết bị {kind} {number}',
    },
    tooltips: {
        toggleRemoteAudio: 'Tắt/Bật âm thanh',
        toggleRemoteVideo: 'Tắt/Bật video',
        toggleLocalMic: 'Tắt/Bật micrô',
        toggleLocalWebcam: 'Bắt đầu/Dừng webcam',
        toggleSessionAudio: 'Tắt/Bật âm thanh phiên',
        sessionVolume: 'Âm lượng phiên',
        reply: 'Trả lời',
        cancelReply: 'Hủy trả lời',
        designateSpeaker: 'Chỉ định làm người phát biểu',
        stopApp: 'Dừng',
        restartApp: 'Khởi động lại',
        swapApp: 'Chuyển sang ứng dụng này',
        cannotStopActive: 'Không thể dừng ứng dụng đang hoạt động',
        reloadStream: 'Tải lại luồng',
    },
    usernamePrompt: {
        title: 'Chào mừng!',
        description: 'Vui lòng chọn tên người dùng để tham gia phiên.',
        placeholder: 'Tên của bạn',
        joinButton: 'Tham gia',
    },
    sidebar: {
        title: 'SealSkin',
    },
    chat: {
        inputPlaceholder: 'Nhập tin nhắn...',
        selfUsername: 'Bạn',
        replyingTo: 'Đang trả lời <b>{sender}</b>',
    },
    systemMessages: {
        userJoined: '<b>{username}</b> đã tham gia phòng.',
        userLeft: '<b>{username}</b> đã rời phòng.',
        usernameChanged: '<b>{old_username}</b> bây giờ được biết đến với tên <b>{new_username}</b>.',
        swappedApp: 'Đã chuyển sang ứng dụng: {app_name}',
        systemSender: 'Hệ thống',
    },
    inviteLinks: {
        participant: 'Lời mời người dùng cộng tác',
        readonly: 'Lời mời người dùng chỉ đọc',
        readonlyParticipantView: 'Lời mời chỉ đọc',
    },
    disconnect: {
        title: 'Đã ngắt kết nối',
        message: 'Phiên đã kết thúc.',
    },
    waiting: {
        title: 'Người điều khiển vắng mặt',
        message: 'Phiên đang hoạt động. Đang chờ người điều khiển tiếp tục luồng.',
    },
    startMenu: {
        launchTab: 'Khởi chạy mới',
        activeTab: 'Các phiên đang hoạt động',
        searchPlaceholder: 'Tìm kiếm ứng dụng...',
        appsButton: 'ỨNG DỤNG',
        appsTitle: 'Ứng dụng',
        loading: 'Đang tải...',
        activeVisible: 'Đang hoạt động (Hiển thị)',
        runningBackground: 'Đang chạy (Nền)',
    },
};

// Thai
const th = {
    pageTitle: 'ความร่วมมือ SealSkin',
    localUsername: 'คุณ',
    settings: {
        title: 'การตั้งค่า',
        microphoneLabel: 'ไมโครโฟน',
        webcamLabel: 'เว็บแคม',
    },
    alerts: {
        webcodecsUnsupported: 'เบราว์เซอร์ของคุณไม่รองรับ WebCodecs ซึ่งจำเป็นสำหรับฟีเจอร์นี้',
        mediaAccessError: 'ไม่สามารถเข้าถึงกล้องหรือไมโครโฟนของคุณได้: {message}',
    },
    devices: {
        unlabeledDevice: 'อุปกรณ์ {kind} {number}',
    },
    tooltips: {
        toggleRemoteAudio: 'ปิด/เปิดเสียง',
        toggleRemoteVideo: 'ปิด/เปิดวิดีโอ',
        toggleLocalMic: 'ปิด/เปิดไมโครโฟน',
        toggleLocalWebcam: 'เริ่ม/หยุดเว็บแคม',
        toggleSessionAudio: 'ปิด/เปิดเสียงเซสชัน',
        sessionVolume: 'ระดับเสียงเซสชัน',
        reply: 'ตอบกลับ',
        cancelReply: 'ยกเลิกการตอบกลับ',
        designateSpeaker: 'กำหนดเป็นผู้พูด',
        stopApp: 'หยุด',
        restartApp: 'รีสตาร์ท',
        swapApp: 'สลับไปแอปนี้',
        cannotStopActive: 'ไม่สามารถหยุดแอปที่ใช้งานอยู่',
        reloadStream: 'โหลดสตรีมใหม่',
    },
    usernamePrompt: {
        title: 'ยินดีต้อนรับ!',
        description: 'โปรดเลือกชื่อผู้ใช้เพื่อเข้าร่วมเซสชัน',
        placeholder: 'ชื่อของคุณ',
        joinButton: 'เข้าร่วม',
    },
    sidebar: {
        title: 'SealSkin',
    },
    chat: {
        inputPlaceholder: 'พิมพ์ข้อความ...',
        selfUsername: 'คุณ',
        replyingTo: 'กำลังตอบกลับ <b>{sender}</b>',
    },
    systemMessages: {
        userJoined: '<b>{username}</b> ได้เข้าร่วมห้องแล้ว',
        userLeft: '<b>{username}</b> ได้ออกจากห้องแล้ว',
        usernameChanged: '<b>{old_username}</b> ตอนนี้เป็นที่รู้จักในชื่อ <b>{new_username}</b>',
        swappedApp: 'สลับไปที่แอปพลิเคชัน: {app_name}',
        systemSender: 'ระบบ',
    },
    inviteLinks: {
        participant: 'คำเชิญผู้ใช้ร่วมทำงาน',
        readonly: 'คำเชิญผู้ใช้แบบอ่านอย่างเดียว',
        readonlyParticipantView: 'คำเชิญแบบอ่านอย่างเดียว',
    },
    disconnect: {
        title: 'ตัดการเชื่อมต่อแล้ว',
        message: 'เซสชันสิ้นสุดลงแล้ว',
    },
    waiting: {
        title: 'ผู้ควบคุมไม่อยู่',
        message: 'เซสชันกำลังทำงาน กำลังรอให้ผู้ควบคุมดำเนินการสตรีมต่อ',
    },
    startMenu: {
        launchTab: 'เปิดใหม่',
        activeTab: 'เซสชันที่ใช้งานอยู่',
        searchPlaceholder: 'ค้นหาแอป...',
        appsButton: 'แอป',
        appsTitle: 'แอปพลิเคชัน',
        loading: 'กำลังโหลด...',
        activeVisible: 'ใช้งานอยู่ (มองเห็น)',
        runningBackground: 'กำลังทำงาน (พื้นหลัง)',
    },
};

// Filipino
const fil = {
    pageTitle: 'Pakikipagtulungan sa SealSkin',
    localUsername: 'Ikaw',
    settings: {
        title: 'Mga Setting',
        microphoneLabel: 'Mikropono',
        webcamLabel: 'Webcam',
    },
    alerts: {
        webcodecsUnsupported: 'Hindi sinusuportahan ng iyong browser ang WebCodecs, na kinakailangan para sa feature na ito.',
        mediaAccessError: 'Hindi ma-access ang iyong camera o mikropono: {message}',
    },
    devices: {
        unlabeledDevice: '{kind} device {number}',
    },
    tooltips: {
        toggleRemoteAudio: 'I-mute/I-unmute ang Audio',
        toggleRemoteVideo: 'I-mute/I-unmute ang Video',
        toggleLocalMic: 'I-mute/I-unmute ang Mikropono',
        toggleLocalWebcam: 'Simulan/Itigil ang Webcam',
        toggleSessionAudio: 'I-mute/I-unmute ang Audio ng Session',
        sessionVolume: 'Volume ng Session',
        reply: 'Sumagot',
        cancelReply: 'Kanselahin ang Sagot',
        designateSpeaker: 'Italaga bilang Tagapagsalita',
        stopApp: 'Itigil',
        restartApp: 'I-restart',
        swapApp: 'Lumipat sa app na ito',
        cannotStopActive: 'Hindi mapatigil ang aktibong app',
        reloadStream: 'I-reload ang Stream',
    },
    usernamePrompt: {
        title: 'Maligayang pagdating!',
        description: 'Mangyaring pumili ng username para sumali sa session.',
        placeholder: 'Iyong Pangalan',
        joinButton: 'Sumali',
    },
    sidebar: {
        title: 'SealSkin',
    },
    chat: {
        inputPlaceholder: 'Mag-type ng mensahe...',
        selfUsername: 'Ikaw',
        replyingTo: 'Sumasagot kay <b>{sender}</b>',
    },
    systemMessages: {
        userJoined: 'Si <b>{username}</b> ay sumali sa room.',
        userLeft: 'Si <b>{username}</b> ay umalis sa room.',
        usernameChanged: 'Si <b>{old_username}</b> ay kilala na ngayon bilang <b>{new_username}</b>.',
        swappedApp: 'Lumipat sa aplikasyon: {app_name}',
        systemSender: 'Sistema',
    },
    inviteLinks: {
        participant: 'Imbitasyon ng Gumagamit ng Kolaborasyon',
        readonly: 'Imbitasyon ng Gumagamit na Read Only',
        readonlyParticipantView: 'Imbitasyon na Read Only',
    },
    disconnect: {
        title: 'Nadiskonekta',
        message: 'Nagtapos na ang session.',
    },
    waiting: {
        title: 'Wala ang Controller',
        message: 'Aktibo ang session. Hinihintay ang controller na ipagpatuloy ang stream.',
    },
    startMenu: {
        launchTab: 'Ilunsad ang Bago',
        activeTab: 'Mga Aktibong Session',
        searchPlaceholder: 'Maghanap ng mga app...',
        appsButton: 'APPS',
        appsTitle: 'Mga Aplikasyon',
        loading: 'Naglo-load...',
        activeVisible: 'Aktibo (Nakikita)',
        runningBackground: 'Tumatakbo (Background)',
    },
};

// Danish
const da = {
    pageTitle: 'SealSkin Samarbejde',
    localUsername: 'Dig',
    settings: {
        title: 'Indstillinger',
        microphoneLabel: 'Mikrofon',
        webcamLabel: 'Webcam',
    },
    alerts: {
        webcodecsUnsupported: 'Din browser understøtter ikke WebCodecs, hvilket er påkrævet for denne funktion.',
        mediaAccessError: 'Kunne ikke få adgang til dit kamera eller din mikrofon: {message}',
    },
    devices: {
        unlabeledDevice: '{kind}-enhed {number}',
    },
    tooltips: {
        toggleRemoteAudio: 'Slå lyd til/fra',
        toggleRemoteVideo: 'Slå video til/fra',
        toggleLocalMic: 'Slå mikrofon til/fra',
        toggleLocalWebcam: 'Start/Stop webcam',
        toggleSessionAudio: 'Slå sessionslyd til/fra',
        sessionVolume: 'Sessionslydstyrke',
        reply: 'Svar',
        cancelReply: 'Annuller svar',
        designateSpeaker: 'Udpeg som taler',
        stopApp: 'Stop',
        restartApp: 'Genstart',
        swapApp: 'Skift til denne app',
        cannotStopActive: 'Kan ikke stoppe aktiv app',
        reloadStream: 'Genindlæs stream',
    },
    usernamePrompt: {
        title: 'Velkommen!',
        description: 'Vælg venligst et brugernavn for at deltage i sessionen.',
        placeholder: 'Dit navn',
        joinButton: 'Deltag',
    },
    sidebar: {
        title: 'SealSkin',
    },
    chat: {
        inputPlaceholder: 'Skriv en besked...',
        selfUsername: 'Dig',
        replyingTo: 'Svarer til <b>{sender}</b>',
    },
    systemMessages: {
        userJoined: '<b>{username}</b> er kommet ind i rummet.',
        userLeft: '<b>{username}</b> har forladt rummet.',
        usernameChanged: '<b>{old_username}</b> er nu kendt som <b>{new_username}</b>.',
        swappedApp: 'Skiftede til applikation: {app_name}',
        systemSender: 'System',
    },
    inviteLinks: {
        participant: 'Invitation til samarbejdsbruger',
        readonly: 'Invitation til skrivebeskyttet bruger',
        readonlyParticipantView: 'Skrivebeskyttet invitation',
    },
    disconnect: {
        title: 'Forbindelse afbrudt',
        message: 'Sessionen er afsluttet.',
    },
    waiting: {
        title: 'Controlleren er væk',
        message: 'Sessionen er aktiv. Venter på at controlleren genoptager streamen.',
    },
    startMenu: {
        launchTab: 'Start ny',
        activeTab: 'Aktive sessioner',
        searchPlaceholder: 'Søg apps...',
        appsButton: 'APPS',
        appsTitle: 'Applikationer',
        loading: 'Indlæser...',
        activeVisible: 'Aktiv (Synlig)',
        runningBackground: 'Kører (Baggrund)',
    },
};


const translations = {
    en,
    es,
    zh,
    hi,
    pt,
    fr,
    ru,
    de,
    tr,
    it,
    nl,
    ar,
    ko,
    ja,
    vi,
    th,
    fil,
    da,
};

function getTranslator(langCode = 'en') {
    const baseLang = langCode.split('-')[0].toLowerCase();
    const langDict = translations[baseLang] || translations.en;
    const fallbackDict = translations.en;

    const t = (key, variables = {}) => {
        const keys = key.split('.');
        let value = keys.reduce((obj, k) => (obj && obj[k] !== undefined) ? obj[k] : undefined, langDict);

        if (value === undefined) {
            value = keys.reduce((obj, k) => (obj && obj[k] !== undefined) ? obj[k] : undefined, fallbackDict);
        }

        if (value === undefined) {
            console.warn(`Translation key not found: ${key}`);
            return key;
        }

        if (typeof value !== 'string') {
            return value;
        }
        
        let processedText = value.replace(/\{(\w+),\s*plural,\s*(.*)\}/g, (match, varName, rulesStr) => {
            if (!variables.hasOwnProperty(varName)) return match;
            const count = variables[varName];
            const rules = {};
            const ruleRegex = /(\w+)\s*\{((?:[^{}]|{[^{}]*})*)\}/g;
            let ruleMatch;
            while ((ruleMatch = ruleRegex.exec(rulesStr)) !== null) {
                rules[ruleMatch[1]] = ruleMatch[2];
            }
            let resultText;
            if (count === 1 && rules.one) resultText = rules.one;
            else if (rules.other) resultText = rules.other;
            else return match;
            return resultText;
        });

        for (const placeholder in variables) {
            const regex = new RegExp(`\\{${placeholder}\\}`, 'g');
            processedText = processedText.replace(regex, variables[placeholder]);
        }
        
        return processedText;
    };

    return { t };
};
