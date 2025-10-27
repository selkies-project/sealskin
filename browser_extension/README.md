# SealSkin Browser Extension

## Overview

The SealSkin Browser Extension is the client-side component of the platform, serving as the primary user entry point for interacting with the secure remote environment. It integrates directly into the Chrome browser to capture user intent—such as opening a link, file, or search query—and securely forwards these actions to the SealSkin server for processing within an isolated application. The extension is responsible for all client-side cryptographic operations, state management, and user interface rendering.

## Core Components and Architecture

The extension is built using Chrome's Manifest V3 architecture and is divided into several key components, each with a distinct responsibility.

### Service Worker (background.js)

This is the central control hub of the extension. As a persistent background script, it manages all core logic that does not require direct user interaction.

**Key Functions:**
*   **Secure Communication:** Manages the cryptographic handshake with the server to establish an end-to-end encrypted (E2EE) session. It handles the encryption of all outgoing API request bodies and the decryption of incoming responses.
*   **Browser Integration:** Creates and manages all context menu items (e.g., "Open link in secure session"). It listens for user clicks on these menus to initiate a workflow.
*   **Download Interception:** Implements the logic to optionally intercept file downloads and redirect them into the secure environment.
*   **API Broker:** Provides a `secureFetch` interface for other parts of the extension (like the popup and options page) to make secure, authenticated, and encrypted calls to the server API.

### Action Popup (popup.js)

This component provides the primary, quick-access user interface when the extension's icon is clicked. It is context-aware and serves as the main launchpad for applications.

**Key Functions:**
*   **Application Launcher:** Fetches and displays a list of applications available to the user.
*   **Context-Aware UI:** The UI adapts based on the context. For a simple launch, it shows all apps. If triggered from a file or URL, it may recommend specific applications and present relevant options (e.g., "Open file on launch").
*   **Session Management:** Displays a list of the user's active sessions, allowing them to be reopened or terminated.
*   **Launch Configuration:** Allows the user to select launch parameters such as a persistent home directory, language, or a specific GPU (if permitted).

### Options Page (options.js)

This is the comprehensive configuration and management dashboard for the extension. Its functionality is dynamically rendered based on the user's role (User or Admin).

**Key Functions:**
*   **Initial Configuration:** Allows a user to configure the extension by providing their connection details and cryptographic keys, either manually or by importing a JSON file.
*   **User Dashboard:** Provides standard users with tools to manage their home directories and view active sessions.
*   **Administrator Dashboard:** For users with admin privileges, this page transforms into a full management console for the entire platform, including user/group management, application installation from app stores, and application template configuration.

## Core Functionality

### Secure Communication and Authentication

*   **E2EE Handshake:** On first contact with the server, the extension's service worker performs a cryptographic handshake. It validates the server's identity by verifying a signed nonce, then generates a symmetric AES-GCM session key, encrypts it with the server's public key, and sends it to the server to establish the secure channel.
*   **JWT Authentication:** For all authenticated API endpoints, the extension generates a short-lived JSON Web Token (JWT). The JWT payload is signed using the user's private key via the Web Crypto API. This signed token is included in the `Authorization` header of the request, allowing the server to verify the user's identity.

### Browser Integration and Workflow

The extension deeply integrates with the browser to provide a seamless user experience for redirecting content to the secure environment.

1.  **User Action:** The workflow is initiated by the user in one of several ways:
    *   Clicking the extension icon in the toolbar (simple launch).
    *   Right-clicking a link, image, or selected text and choosing a SealSkin context menu option.
    *   Triggering a file download after enabling the "intercept next download" option.
    *   Uploading a file from their local machine via the `upload.html` page.
2.  **Context Capture:** The service worker captures the context of the action (e.g., the target URL, the filename) and stores it in local storage.
3.  **Popup Interaction:** The Action Popup is displayed. It reads the stored context and presents the user with a list of compatible applications and relevant launch options.
4.  **Secure Launch Request:** Once the user confirms their selection, the extension constructs a launch request. The request body, containing the application ID and any contextual data (like a URL to open), is encrypted using the established E2EE session key.
5.  **Session Initiation:** The encrypted request is sent to the server's API port. The server decrypts the request, orchestrates the application container launch, and returns a unique, single-use session URL in an encrypted response.
6.  **Redirection:** The extension decrypts the response, extracts the session URL, and opens it in a new browser tab, connecting the user to their isolated application via the server's Session Proxy.

### Role-Based User Interface

The Options Page provides a powerful interface that adapts based on the logged-in user's role, as determined by the server.

#### Standard User View

*   **Configuration:** Manage client-side settings and export the configuration file for backup or use on another device.
*   **Home Directories:** Create and delete persistent storage volumes for use across sessions.
*   **Active Sessions:** View and terminate their own active application sessions.
*   **Pinned Behaviors:** Manage saved launch preferences for specific file types or URL actions.

#### Administrator View

Includes all standard user functionality, plus a full suite of administrative panels:
*   **User and Group Management:** Create, edit, and delete users and groups, and manage their permissions.
*   **Application Store Management:** Add and manage remote repositories (App Stores) from which applications can be installed.
*   **Installed Application Management:** View, update, and configure all applications installed on the server, including assigning them to specific users or groups.
*   **Application Template Editor:** A comprehensive UI for creating and modifying application templates. These templates control hundreds of potential environment variables within the remote session, governing everything from UI appearance and feature availability to security hardening and performance tuning.
*   **Global Session View:** View and terminate active sessions for any user on the platform.
