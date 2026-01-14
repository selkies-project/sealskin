# SealSkin

SealSkin is a self-hosted, client-server platform that enables users to run powerful, containerized desktop applications streamed directly to a web browser. It uses a browser extension to intercept user actions—such as clicking a link or downloading a file and redirects them to a secure, isolated application environment running on a remote server.

Install the extension [For Chrome HERE](https://chromewebstore.google.com/detail/sealskin-isolation/lclgfmnljgacfdpmmmjmfpdelndbbfhk)
Install the extension [For Firefox HERE](https://addons.mozilla.org/en-US/firefox/addon/sealskin-isolation/)

Get the server [HERE](https://github.com/linuxserver/docker-sealskin)

## Primary Functions

1.  **Remote Application Access:**
    Stream demanding desktop applications (e.g., video editors, IDEs, 3D modeling software, office suites) to any device with a web browser. This allows any file or link on the web to be opened directly in a full-featured application. It leverages the server's hardware (CPU, GPU, RAM) and provides access to powerful tools without local installation.

2.  **Secure Browser & File Isolation:**
    Isolate the local machine from the public internet. All web content, downloads, and application processes are executed within a sandboxed container on the remote server. This prevents malware, exploits, and trackers from ever reaching the client device, ensuring a clean and secure local environment.

3.  **Centralized Data Management:**
    Intercept file downloads and redirect them directly to persistent storage on the server. This keeps the local machine free of downloaded data and makes files immediately available to your suite of remote applications for editing and processing linked through multiple configurable home directories.

## Core Architecture

The system is composed of two primary components that communicate over a secure, end-to-end encrypted channel.

### [SealSkin Server](./server/README.md)

The server is the central hub of the platform. It is responsible for all management, orchestration, and traffic proxying. Its key design feature is a **Dual-Port Architecture** that separates management tasks from live application traffic, enhancing security and stability.

*   **Control Plane (API Server):** Handles user authentication, application management, and the orchestration of new application sessions. All communication is protected by end-to-end encryption.
*   **Data Plane (Session Proxy Server):** Acts as a secure reverse proxy for all live application traffic (HTTP and WebSockets). It ensures that the internal application containers are never directly exposed to the internet.

### [SealSkin Browser Extension](./browser_extension/README.md)

The browser extension is the user's entry point into the SealSkin environment. It integrates directly into the browser to capture user actions (e.g., right-clicking a link, downloading a file) and provides the UI for launching and managing remote sessions. It is responsible for all client-side cryptographic operations, including the E2EE handshake and signing authentication tokens.

## How It Works: A Typical Workflow

The interaction between the browser extension and the server follows a secure and orchestrated process:

1.  **User Action:** A user initiates an action in their browser, such as right-clicking a link and selecting "Open in Secure Session" from the context menu.
2.  **Extension Capture:** The **Browser Extension** captures this intent and opens its popup UI, presenting the user with a list of compatible remote applications.
3.  **Secure Communication:** The extension establishes an End-to-End Encrypted (E2EE) channel with the server's API. It then authenticates the user by sending a JSON Web Token (JWT) signed with the user's private key.
4.  **Server Orchestration:** The **Server** receives the encrypted launch request. After authenticating the user and verifying permissions, it instructs its backend provider (e.g., Docker) to launch a new, isolated application container.
5.  **Proxy Connection:** Once the container is running, the server returns a unique, single-use URL. The extension opens this URL in a new tab, connecting the user to the running application through the server's secure **Session Proxy**. All subsequent traffic for that session flows through the proxy.

## Key Features

*   **End-to-End Encrypted API:** All management communication between the client and server is encrypted, protecting sensitive data like launch parameters and user information.
*   **Passwordless Authentication:** Securely authenticates users via a public/private key cryptographic challenge (signed JWTs), eliminating the need for passwords.
*   **Dual-Port Architecture:** A strict separation between the management control plane and the application data plane enhances security.
*   **Containerized Application Backend:** Uses Docker as the primary provider to run applications in isolated, sandboxed environments.
*   **Role-Based Access Control (RBAC):** A clear distinction between Admins (full system control) and Users (can only launch and manage their own sessions).
*   **Seamless Browser Integration:** Deep integration with browser context menus and download flows for a smooth user experience.
*   **Full Admin UI:** The extension's options page transforms into a complete management dashboard for administrators, allowing for user, group, and application management directly from the browser.

## Details

For detailed information on each component, please see their respective README files:
*   **[SealSkin Server README](./server/README.md)**
*   **[SealSkin Browser Extension README](./browser_extension/README.md)**
