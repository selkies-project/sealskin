# Privacy Policy for SealSkin

**Last Updated: 11-02-25**

## Introduction

Welcome to SealSkin. This privacy policy explains how the SealSkin browser extension ("the Extension") handles your information. SealSkin is a client for a self-hosted platform designed to give you secure, remote access to applications. The fundamental principle of our design is user control and privacy.

**The SealSkin extension communicates ONLY with the server instance that you configure and control. We, the developers of SealSkin, do not have access to your server, your data, or any information transmitted between the Extension and your server.**

## Information We Collect and Why

The Extension stores a limited amount of data locally on your computer using the browser's built-in storage. This data is necessary for the Extension to function:

*   **Configuration Data:** To connect to your server, the Extension stores the Server IP/Domain, API Port, Session Port, your Username, your Private Key, and the Server's Public Key. This information is required to establish a secure, authenticated connection with your self-hosted SealSkin server.
*   **User Preferences:** The Extension may store your saved launch preferences (e.g., automatically using a specific application for a certain file type) to improve your user experience.

When you use the Extension, certain data is transmitted to YOUR server to perform actions you initiate:

*   **Authentication Information:** Your username and a securely signed token (JWT) generated using your private key are sent to your server to verify your identity.
*   **Website Content:** When you right-click on a link, image, or selected text and choose a SealSkin action, the corresponding URL or text is sent to your server so it can be opened in the remote application.
*   **Files:** When you upload a file or intercept a download, the file's content is streamed directly and securely to your server.
*   **IP Address:** As with any internet connection, your IP address will be visible to your server when the Extension communicates with it.

## How Your Information is Used

All collected information is used exclusively for the single purpose of the Extension: to provide a secure and functional connection to your self-hosted SealSkin platform. We do not use this data for any other purpose, such as advertising, analytics, or tracking.

## Data Storage and Security

All configuration data, including your private key, is stored locally on your device using `chrome.storage.local`. All communication between the Extension and your server's API is protected with End-to-End Encryption (E2EE), ensuring that the data in transit is secure.

## Data Sharing and Third Parties

We do not sell, trade, or transfer your data to any third parties. The Extension is designed to communicate solely with the server you specify. You are in complete control of where your data is sent.

## Changes to This Privacy Policy

We may update this privacy policy from time to time. We will notify you of any changes by posting the new privacy policy on this page. You are advised to review this Privacy Policy periodically for any changes.

## Contact Us

If you have any questions about this Privacy Policy, please contact us [HERE](https://github.com/selkies-project/sealskin/issues).
