# SealSkin Mobile Client

The SealSkin Mobile Client is a hybrid application built using **Capacitor**. It adapts the core logic and user interface of the SealSkin Browser Extension to run as a standalone native application on mobile devices.

## Platform Support

*   **Current Support:** Android, iOS

## Operational Overview

The application runs the existing browser extension codebase within a native WebView. It injects a compatibility layer to mock specific browser APIs (the `chrome.*` namespace) and utilizes Capacitor plugins to bridge native functionality such as file system access and deep linking.

### Functional Differences from Browser Extension

Because the mobile client operates as a sandboxed application rather than an extension integrated directly into a web browser, specific interception features are unavailable:

1.  **No Context Menu Integration:** The client cannot modify the context menus of other mobile browsers (e.g., Chrome for Android). Users cannot "right-click" a link in an external browser to open it in a secure session.
2.  **No Automatic Download Interception:** The client cannot capture or redirect downloads initiated in external mobile apps or browsers.
3.  **Session Handling:** When a secure session is launched, the client opens the session URL in the system's default browser (or a Custom Tab) rather than a new tab within the client itself, ensuring the remote session renders with full browser performance capabilities.

## Build Instructions

The build process relies on copying the live assets from the `../browser_extension` directory into the mobile build artifacts. This is automated through npm build scripts.

### Prerequisites

#### Android
*   Node.js and npm
*   Android Studio (with Android SDK installed)
*   Java Development Kit (JDK)

#### iOS
*   Node.js and npm
*   Xcode and OSX 26.2^

### Installation and Deployment

1.  **Install Dependencies:**
    ```bash
    npm install
    ```

2.  **Build and Run (Android):**
    This command compiles the compatibility layer, copies the latest extension assets, syncs the native configuration, and opens the project in Android Studio or Xcode.  
    ```bash
    npm run android
    ```
    **Build and Run (iOS):**
    ```bash
    npm run ios
    ```

### Build Scripts

*   `npm run build`: Cleans the distribution directory (`www`), generates assets, bundles the mobile-specific logic using esbuild, and copies the extension files (JS, HTML, CSS) from the browser extension directory.
*   `npm run android`: Executes the build script, syncs with Capacitor, and opens the Android native IDE.
