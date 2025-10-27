const getAppTemplateSettings = (t) => [
  // UI Category
  {
    name: "TITLE",
    label: t('options.appTemplates.settings.TITLE.label'),
    default: "Selkies",
    description: t('options.appTemplates.settings.TITLE.description'),
    category: "ui",
    type: "text"
  },
  {
    name: "SELKIES_UI_TITLE",
    label: t('options.appTemplates.settings.SELKIES_UI_TITLE.label'),
    default: "Selkies",
    description: t('options.appTemplates.settings.SELKIES_UI_TITLE.description'),
    category: "ui",
    type: "text"
  },
  {
    name: "SELKIES_UI_SHOW_SIDEBAR",
    label: t('options.appTemplates.settings.SELKIES_UI_SHOW_SIDEBAR.label'),
    default: "true",
    description: t('options.appTemplates.settings.SELKIES_UI_SHOW_SIDEBAR.description'),
    category: "ui",
    type: "boolean"
  },
  {
    name: "SELKIES_UI_SHOW_LOGO",
    label: t('options.appTemplates.settings.SELKIES_UI_SHOW_LOGO.label'),
    default: "true",
    description: t('options.appTemplates.settings.SELKIES_UI_SHOW_LOGO.description'),
    category: "ui",
    type: "boolean"
  },
  {
    name: "SELKIES_UI_SHOW_CORE_BUTTONS",
    label: t('options.appTemplates.settings.SELKIES_UI_SHOW_CORE_BUTTONS.label'),
    default: "true",
    description: t('options.appTemplates.settings.SELKIES_UI_SHOW_CORE_BUTTONS.description'),
    category: "ui",
    type: "boolean"
  },
  {
    name: "SELKIES_UI_SIDEBAR_SHOW_VIDEO_SETTINGS",
    label: t('options.appTemplates.settings.SELKIES_UI_SIDEBAR_SHOW_VIDEO_SETTINGS.label'),
    default: "true",
    description: t('options.appTemplates.settings.SELKIES_UI_SIDEBAR_SHOW_VIDEO_SETTINGS.description'),
    category: "ui",
    type: "boolean"
  },
  {
    name: "SELKIES_UI_SIDEBAR_SHOW_SCREEN_SETTINGS",
    label: t('options.appTemplates.settings.SELKIES_UI_SIDEBAR_SHOW_SCREEN_SETTINGS.label'),
    default: "true",
    description: t('options.appTemplates.settings.SELKIES_UI_SIDEBAR_SHOW_SCREEN_SETTINGS.description'),
    category: "ui",
    type: "boolean"
  },
  {
    name: "SELKIES_UI_SIDEBAR_SHOW_AUDIO_SETTINGS",
    label: t('options.appTemplates.settings.SELKIES_UI_SIDEBAR_SHOW_AUDIO_SETTINGS.label'),
    default: "true",
    description: t('options.appTemplates.settings.SELKIES_UI_SIDEBAR_SHOW_AUDIO_SETTINGS.description'),
    category: "ui",
    type: "boolean"
  },
  {
    name: "SELKIES_UI_SIDEBAR_SHOW_STATS",
    label: t('options.appTemplates.settings.SELKIES_UI_SIDEBAR_SHOW_STATS.label'),
    default: "true",
    description: t('options.appTemplates.settings.SELKIES_UI_SIDEBAR_SHOW_STATS.description'),
    category: "ui",
    type: "boolean"
  },
  {
    name: "SELKIES_UI_SIDEBAR_SHOW_CLIPBOARD",
    label: t('options.appTemplates.settings.SELKIES_UI_SIDEBAR_SHOW_CLIPBOARD.label'),
    default: "true",
    description: t('options.appTemplates.settings.SELKIES_UI_SIDEBAR_SHOW_CLIPBOARD.description'),
    category: "ui",
    type: "boolean"
  },
  {
    name: "SELKIES_UI_SIDEBAR_SHOW_FILES",
    label: t('options.appTemplates.settings.SELKIES_UI_SIDEBAR_SHOW_FILES.label'),
    default: "true",
    description: t('options.appTemplates.settings.SELKIES_UI_SIDEBAR_SHOW_FILES.description'),
    category: "ui",
    type: "boolean"
  },
  {
    name: "SELKIES_UI_SIDEBAR_SHOW_APPS",
    label: t('options.appTemplates.settings.SELKIES_UI_SIDEBAR_SHOW_APPS.label'),
    default: "true",
    description: t('options.appTemplates.settings.SELKIES_UI_SIDEBAR_SHOW_APPS.description'),
    category: "ui",
    type: "boolean"
  },
  {
    name: "SELKIES_UI_SIDEBAR_SHOW_SHARING",
    label: t('options.appTemplates.settings.SELKIES_UI_SIDEBAR_SHOW_SHARING.label'),
    default: "true",
    description: t('options.appTemplates.settings.SELKIES_UI_SIDEBAR_SHOW_SHARING.description'),
    category: "ui",
    type: "boolean"
  },
  {
    name: "SELKIES_UI_SIDEBAR_SHOW_GAMEPADS",
    label: t('options.appTemplates.settings.SELKIES_UI_SIDEBAR_SHOW_GAMEPADS.label'),
    default: "true",
    description: t('options.appTemplates.settings.SELKIES_UI_SIDEBAR_SHOW_GAMEPADS.description'),
    category: "ui",
    type: "boolean"
  },
  {
    name: "SELKIES_UI_SIDEBAR_SHOW_KEYBOARD_BUTTON",
    label: t('options.appTemplates.settings.SELKIES_UI_SIDEBAR_SHOW_KEYBOARD_BUTTON.label'),
    default: "true",
    description: t('options.appTemplates.settings.SELKIES_UI_SIDEBAR_SHOW_KEYBOARD_BUTTON.description'),
    category: "ui",
    type: "boolean"
  },
  {
    name: "SELKIES_UI_SIDEBAR_SHOW_SOFT_BUTTONS",
    label: t('options.appTemplates.settings.SELKIES_UI_SIDEBAR_SHOW_SOFT_BUTTONS.label'),
    default: "true",
    description: t('options.appTemplates.settings.SELKIES_UI_SIDEBAR_SHOW_SOFT_BUTTONS.description'),
    category: "ui",
    type: "boolean"
  },
  {
    name: "WATERMARK_PNG",
    label: t('options.appTemplates.settings.WATERMARK_PNG.label'),
    default: "",
    description: t('options.appTemplates.settings.WATERMARK_PNG.description'),
    category: "ui",
    type: "text"
  },
  {
    name: "WATERMARK_LOCATION",
    label: t('options.appTemplates.settings.WATERMARK_LOCATION.label'),
    default: "-1",
    description: t('options.appTemplates.settings.WATERMARK_LOCATION.description'),
    category: "ui",
    type: "select",
    options: {
      "-1": t('options.appTemplates.settings.WATERMARK_LOCATION.options.disabled'),
      "1": t('options.appTemplates.settings.WATERMARK_LOCATION.options.topLeft'),
      "2": t('options.appTemplates.settings.WATERMARK_LOCATION.options.topRight'),
      "3": t('options.appTemplates.settings.WATERMARK_LOCATION.options.bottomLeft'),
      "4": t('options.appTemplates.settings.WATERMARK_LOCATION.options.bottomRight'),
      "5": t('options.appTemplates.settings.WATERMARK_LOCATION.options.centered'),
      "6": t('options.appTemplates.settings.WATERMARK_LOCATION.options.animated')
    }
  },
  {
    name: "DASHBOARD",
    label: t('options.appTemplates.settings.DASHBOARD.label'),
    default: "",
    description: t('options.appTemplates.settings.DASHBOARD.description'),
    category: "ui",
    type: "select",
    options: {
      "": t('options.appTemplates.settings.DASHBOARD.options.default'),
      "selkies-dashboard": t('options.appTemplates.settings.DASHBOARD.options.selkiesDefault'),
      "selkies-dashboard-zinc": t('options.appTemplates.settings.DASHBOARD.options.zinc'),
      "selkies-dashboard-wish": t('options.appTemplates.settings.DASHBOARD.options.wish')
    }
  },

  // App Category
  {
    name: "SELKIES_AUDIO_ENABLED",
    label: t('options.appTemplates.settings.SELKIES_AUDIO_ENABLED.label'),
    default: "true",
    description: t('options.appTemplates.settings.SELKIES_AUDIO_ENABLED.description'),
    category: "app",
    type: "boolean"
  },
  {
    name: "SELKIES_MICROPHONE_ENABLED",
    label: t('options.appTemplates.settings.SELKIES_MICROPHONE_ENABLED.label'),
    default: "true",
    description: t('options.appTemplates.settings.SELKIES_MICROPHONE_ENABLED.description'),
    category: "app",
    type: "boolean"
  },
  {
    name: "SELKIES_GAMEPAD_ENABLED",
    label: t('options.appTemplates.settings.SELKIES_GAMEPAD_ENABLED.label'),
    default: "true",
    description: t('options.appTemplates.settings.SELKIES_GAMEPAD_ENABLED.description'),
    category: "app",
    type: "boolean"
  },
  {
    name: "SELKIES_CLIPBOARD_ENABLED",
    label: t('options.appTemplates.settings.SELKIES_CLIPBOARD_ENABLED.label'),
    default: "true",
    description: t('options.appTemplates.settings.SELKIES_CLIPBOARD_ENABLED.description'),
    category: "app",
    type: "boolean"
  },
  {
    name: "SELKIES_ENABLE_BINARY_CLIPBOARD",
    label: t('options.appTemplates.settings.SELKIES_ENABLE_BINARY_CLIPBOARD.label'),
    default: "false",
    description: t('options.appTemplates.settings.SELKIES_ENABLE_BINARY_CLIPBOARD.description'),
    category: "app",
    type: "boolean"
  },
  {
    name: "SELKIES_COMMAND_ENABLED",
    label: t('options.appTemplates.settings.SELKIES_COMMAND_ENABLED.label'),
    default: "true",
    description: t('options.appTemplates.settings.SELKIES_COMMAND_ENABLED.description'),
    category: "app",
    type: "boolean"
  },
  {
    name: "SELKIES_FILE_TRANSFERS",
    label: t('options.appTemplates.settings.SELKIES_FILE_TRANSFERS.label'),
    default: "upload,download",
    description: t('options.appTemplates.settings.SELKIES_FILE_TRANSFERS.description'),
    category: "app",
    type: "text"
  },
  {
    name: "SELKIES_ENABLE_SHARING",
    label: t('options.appTemplates.settings.SELKIES_ENABLE_SHARING.label'),
    default: "true",
    description: t('options.appTemplates.settings.SELKIES_ENABLE_SHARING.description'),
    category: "app",
    type: "boolean"
  },
  {
    name: "SELKIES_SECOND_SCREEN",
    label: t('options.appTemplates.settings.SELKIES_SECOND_SCREEN.label'),
    default: "true",
    description: t('options.appTemplates.settings.SELKIES_SECOND_SCREEN.description'),
    category: "app",
    type: "boolean"
  },
  {
    name: "SELKIES_USE_BROWSER_CURSORS",
    label: t('options.appTemplates.settings.SELKIES_USE_BROWSER_CURSORS.label'),
    default: "false",
    description: t('options.appTemplates.settings.SELKIES_USE_BROWSER_CURSORS.description'),
    category: "app",
    type: "boolean"
  },
  {
    name: "SELKIES_USE_CSS_SCALING",
    label: t('options.appTemplates.settings.SELKIES_USE_CSS_SCALING.label'),
    default: "false",
    description: t('options.appTemplates.settings.SELKIES_USE_CSS_SCALING.description'),
    category: "app",
    type: "boolean"
  },
  {
    name: "SELKIES_IS_MANUAL_RESOLUTION_MODE",
    label: t('options.appTemplates.settings.SELKIES_IS_MANUAL_RESOLUTION_MODE.label'),
    default: "false",
    description: t('options.appTemplates.settings.SELKIES_IS_MANUAL_RESOLUTION_MODE.description'),
    category: "app",
    type: "boolean"
  },
  {
    name: "SELKIES_MANUAL_WIDTH",
    label: t('options.appTemplates.settings.SELKIES_MANUAL_WIDTH.label'),
    default: "0",
    description: t('options.appTemplates.settings.SELKIES_MANUAL_WIDTH.description'),
    category: "app",
    type: "text"
  },
  {
    name: "SELKIES_MANUAL_HEIGHT",
    label: t('options.appTemplates.settings.SELKIES_MANUAL_HEIGHT.label'),
    default: "0",
    description: t('options.appTemplates.settings.SELKIES_MANUAL_HEIGHT.description'),
    category: "app",
    type: "text"
  },
  {
    name: "SELKIES_SCALING_DPI",
    label: t('options.appTemplates.settings.SELKIES_SCALING_DPI.label'),
    default: "96",
    description: t('options.appTemplates.settings.SELKIES_SCALING_DPI.description'),
    category: "app",
    type: "select",
    options: {
      "96": "100%",
      "120": "125%",
      "144": "150%",
      "168": "175%",
      "192": "200%",
      "216": "225%",
      "240": "250%",
      "264": "275%",
      "288": "300%"
    }
  },
  {
    name: "SELKIES_AUDIO_BITRATE",
    label: t('options.appTemplates.settings.SELKIES_AUDIO_BITRATE.label'),
    default: "320000",
    description: t('options.appTemplates.settings.SELKIES_AUDIO_BITRATE.description'),
    category: "app",
    type: "select",
    options: {
      "64000": "64kbps",
      "128000": "128kbps",
      "265000": "265kbps",
      "320000": "320kbps"
    }
  },
  {
    name: "SELKIES_ENCODER",
    label: t('options.appTemplates.settings.SELKIES_ENCODER.label'),
    default: "x264enc,x264enc-striped,jpeg",
    description: t('options.appTemplates.settings.SELKIES_ENCODER.description'),
    category: "app",
    type: "text"
  },
  {
    name: "SELKIES_USE_CPU",
    label: t('options.appTemplates.settings.SELKIES_USE_CPU.label'),
    default: "false",
    description: t('options.appTemplates.settings.SELKIES_USE_CPU.description'),
    category: "app",
    type: "boolean"
  },
  {
    name: "SELKIES_FRAMERATE",
    label: t('options.appTemplates.settings.SELKIES_FRAMERATE.label'),
    default: "8-120",
    description: t('options.appTemplates.settings.SELKIES_FRAMERATE.description'),
    category: "app",
    type: "text"
  },
  {
    name: "SELKIES_H264_CRF",
    label: t('options.appTemplates.settings.SELKIES_H264_CRF.label'),
    default: "5-50",
    description: t('options.appTemplates.settings.SELKIES_H264_CRF.description'),
    category: "app",
    type: "text"
  },
  {
    name: "SELKIES_JPEG_QUALITY",
    label: t('options.appTemplates.settings.SELKIES_JPEG_QUALITY.label'),
    default: "1-100",
    description: t('options.appTemplates.settings.SELKIES_JPEG_QUALITY.description'),
    category: "app",
    type: "text"
  },
  {
    name: "SELKIES_H264_FULLCOLOR",
    label: t('options.appTemplates.settings.SELKIES_H264_FULLCOLOR.label'),
    default: "false",
    description: t('options.appTemplates.settings.SELKIES_H264_FULLCOLOR.description'),
    category: "app",
    type: "boolean"
  },
  {
    name: "SELKIES_H264_STREAMING_MODE",
    label: t('options.appTemplates.settings.SELKIES_H264_STREAMING_MODE.label'),
    default: "false",
    description: t('options.appTemplates.settings.SELKIES_H264_STREAMING_MODE.description'),
    category: "app",
    type: "boolean"
  },
  {
    name: "SELKIES_USE_PAINT_OVER_QUALITY",
    label: t('options.appTemplates.settings.SELKIES_USE_PAINT_OVER_QUALITY.label'),
    default: "true",
    description: t('options.appTemplates.settings.SELKIES_USE_PAINT_OVER_QUALITY.description'),
    category: "app",
    type: "boolean"
  },
  {
    name: "SELKIES_PAINT_OVER_JPEG_QUALITY",
    label: t('options.appTemplates.settings.SELKIES_PAINT_OVER_JPEG_QUALITY.label'),
    default: "1-100",
    description: t('options.appTemplates.settings.SELKIES_PAINT_OVER_JPEG_QUALITY.description'),
    category: "app",
    type: "text"
  },
  {
    name: "SELKIES_H264_PAINTOVER_CRF",
    label: t('options.appTemplates.settings.SELKIES_H264_PAINTOVER_CRF.label'),
    default: "5-50",
    description: t('options.appTemplates.settings.SELKIES_H264_PAINTOVER_CRF.description'),
    category: "app",
    type: "text"
  },
  {
    name: "SELKIES_H264_PAINTOVER_BURST_FRAMES",
    label: t('options.appTemplates.settings.SELKIES_H264_PAINTOVER_BURST_FRAMES.label'),
    default: "1-30",
    description: t('options.appTemplates.settings.SELKIES_H264_PAINTOVER_BURST_FRAMES.description'),
    category: "app",
    type: "text"
  },

  // Hardening Category
  {
    name: "HARDEN_DESKTOP",
    label: t('options.appTemplates.settings.HARDEN_DESKTOP.label'),
    default: "false",
    description: t('options.appTemplates.settings.HARDEN_DESKTOP.description'),
    category: "hardening",
    type: "boolean"
  },
  {
    name: "HARDEN_OPENBOX",
    label: t('options.appTemplates.settings.HARDEN_OPENBOX.label'),
    default: "false",
    description: t('options.appTemplates.settings.HARDEN_OPENBOX.description'),
    category: "hardening",
    type: "boolean"
  },
  {
    name: "DISABLE_OPEN_TOOLS",
    label: t('options.appTemplates.settings.DISABLE_OPEN_TOOLS.label'),
    default: "false",
    description: t('options.appTemplates.settings.DISABLE_OPEN_TOOLS.description'),
    category: "hardening",
    type: "boolean"
  },
  {
    name: "DISABLE_SUDO",
    label: t('options.appTemplates.settings.DISABLE_SUDO.label'),
    default: "false",
    description: t('options.appTemplates.settings.DISABLE_SUDO.description'),
    category: "hardening",
    type: "boolean"
  },
  {
    name: "DISABLE_TERMINALS",
    label: t('options.appTemplates.settings.DISABLE_TERMINALS.label'),
    default: "false",
    description: t('options.appTemplates.settings.DISABLE_TERMINALS.description'),
    category: "hardening",
    type: "boolean"
  },
  {
    name: "DISABLE_CLOSE_BUTTON",
    label: t('options.appTemplates.settings.DISABLE_CLOSE_BUTTON.label'),
    default: "false",
    description: t('options.appTemplates.settings.DISABLE_CLOSE_BUTTON.description'),
    category: "hardening",
    type: "boolean"
  },
  {
    name: "DISABLE_MOUSE_BUTTONS",
    label: t('options.appTemplates.settings.DISABLE_MOUSE_BUTTONS.label'),
    default: "false",
    description: t('options.appTemplates.settings.DISABLE_MOUSE_BUTTONS.description'),
    category: "hardening",
    type: "boolean"
  },
  {
    name: "HARDEN_KEYBINDS",
    label: t('options.appTemplates.settings.HARDEN_KEYBINDS.label'),
    default: "false",
    description: t('options.appTemplates.settings.HARDEN_KEYBINDS.description'),
    category: "hardening",
    type: "boolean"
  },
  {
    name: "RESTART_APP",
    label: t('options.appTemplates.settings.RESTART_APP.label'),
    default: "false",
    description: t('options.appTemplates.settings.RESTART_APP.description'),
    category: "hardening",
    type: "boolean"
  },

  // General Category
  {
    name: "MAX_RES",
    label: t('options.appTemplates.settings.MAX_RES.label'),
    default: "15360x8640",
    description: t('options.appTemplates.settings.MAX_RES.description'),
    category: "general",
    type: "text"
  },
  {
    name: "START_DOCKER",
    label: t('options.appTemplates.settings.START_DOCKER.label'),
    default: "true",
    description: t('options.appTemplates.settings.START_DOCKER.description'),
    category: "general",
    type: "boolean"
  },
  {
    name: "DISABLE_IPV6",
    label: t('options.appTemplates.settings.DISABLE_IPV6.label'),
    default: "false",
    description: t('options.appTemplates.settings.DISABLE_IPV6.description'),
    category: "general",
    type: "boolean"
  },
  {
    name: "DISABLE_ZINK",
    label: t('options.appTemplates.settings.DISABLE_ZINK.label'),
    default: "false",
    description: t('options.appTemplates.settings.DISABLE_ZINK.description'),
    category: "general",
    type: "boolean"
  },
  {
    name: "DISABLE_DRI3",
    label: t('options.appTemplates.settings.DISABLE_DRI3.label'),
    default: "false",
    description: t('options.appTemplates.settings.DISABLE_DRI3.description'),
    category: "general",
    type: "boolean"
  },
  {
    name: "NO_DECOR",
    label: t('options.appTemplates.settings.NO_DECOR.label'),
    default: "false",
    description: t('options.appTemplates.settings.NO_DECOR.description'),
    category: "general",
    type: "boolean"
  },
  {
    name: "NO_FULL",
    label: t('options.appTemplates.settings.NO_FULL.label'),
    default: "false",
    description: t('options.appTemplates.settings.NO_FULL.description'),
    category: "general",
    type: "boolean"
  },
  {
    name: "NO_GAMEPAD",
    label: t('options.appTemplates.settings.NO_GAMEPAD.label'),
    default: "false",
    description: t('options.appTemplates.settings.NO_GAMEPAD.description'),
    category: "general",
    type: "boolean"
  },
  {
    name: "SELKIES_DEBUG",
    label: t('options.appTemplates.settings.SELKIES_DEBUG.label'),
    default: "false",
    description: t('options.appTemplates.settings.SELKIES_DEBUG.description'),
    category: "general",
    type: "boolean"
  },
];
