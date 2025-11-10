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
