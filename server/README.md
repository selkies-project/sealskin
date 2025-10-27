# SealSkin Server

## Overview

The server is the central component of the platform, acting as a secure gateway for managing and accessing remote applications. It is designed to be robust and secure, separating management tasks from live application traffic. Its core responsibilities include user and application management, orchestrating application sessions, and securely proxying all traffic between the end-user and the running application.

## Dual-Port Architecture

The server operates on two distinct network ports to create a clear separation between the control plane (management) and the data plane (application traffic).

### API Server (Control Plane)

*   **Default Port:** `8000`
*   **Purpose:** This port exposes a secure REST API for all management and session orchestration tasks. All communication with this API is protected by end-to-end encryption.

**Key Functions:**
*   **User & Group Management:** Creating, updating, and deleting users, admins, and groups.
*   **Application Management:** Installing, configuring, and updating available applications.
*   **Session Orchestration:** Handling user requests to launch, list, and terminate application sessions.
*   **Secure File Uploads:** Provides endpoints for securely uploading files to be used by an application.

### Session Proxy Server (Data Plane)

*   **Default Port:** `8443`
*   **Purpose:** This port acts as a reverse proxy for all live application sessions. It is the sole entry point for users to interact with their running applications. **This port requires an SSL/TLS certificate to function.**

**How it Works:**
When a user connects to a session, the proxy authenticates their connection, establishes a secure session cookie, and then transparently forwards all subsequent HTTP and WebSocket traffic to the isolated backend application container. This ensures that the internal application network is never exposed to the outside world.

## Core Functionality

### End-to-End Encrypted API

To ensure the confidentiality of all management operations, the API implements end-to-end encryption (E2EE). Before a client can send commands, it must perform a cryptographic handshake with the server. This process establishes a shared symmetric key that is used to encrypt the entire body of all subsequent API requests and responses for that session. This protects sensitive information such as application launch parameters and user data from being intercepted.

### Authentication and Authorization

*   **Authentication:** The system uses a public/private key cryptographic challenge for authentication. Users authenticate by signing a JSON Web Token (JWT) with their private key. The server then verifies this signature against the user's registered public key, providing a secure, passwordless authentication method.
*   **Authorization:** The server enforces a role-based access control model:
    *   **Admins:** Have full control over the system, including user management, group configuration, and application installation.
    *   **Users:** Can launch applications they have been granted access to, manage their active sessions, and manage their own persistent storage ("home directories").
    *   **Groups:** Users can be assigned to groups, which apply a common set of permissions and settings (e.g., enabling GPU access, setting session limits).

### Application Backend Provider

The server uses a pluggable "provider" model to run applications. The primary built-in provider is **Docker**. When a user requests to launch an application, the server instructs the Docker provider to:

1.  Pull the latest version of the application's container image.
2.  Create and start a new, isolated container.
3.  Configure the container's environment with necessary settings, user-specific data, and volume mounts for storage.
4.  Map hardware resources, such as specific GPUs (NVIDIA or DRI3), into the container if requested and authorized.

### Session Lifecycle and Reverse Proxy

The process of starting and connecting to an application is fully orchestrated by the server.

1.  A user sends a launch request to the secure **API Server**.
2.  The server authenticates the user and verifies they have permission to launch the requested application.
3.  The server instructs the backend provider (Docker) to start the application container.
4.  After the container is running and has passed a health check, the server registers the new session internally.
5.  The server returns a unique, single-use URL to the user.
6.  The user's browser connects to the **Session Proxy Server** using this URL.
7.  The proxy validates the connection, sets a secure session cookie, and from that point on, forwards all traffic between the user and the isolated application container until the session is terminated.
