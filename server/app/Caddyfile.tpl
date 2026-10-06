{
        auto_https off
        log {
                level ERROR
        }
{{TRUSTED_PROXIES}}
}

# What a client may never say for itself, and what tells the API a request came through this proxy.
(marks) {
        request_header -X-Upstream-Host
        request_header -X-Upstream-Auth
        request_header -X-Upstream-Peer
        request_header X-SealSkin-Secret "{{PROXY_SECRET}}"
        request_header X-SealSkin-Remote {remote_host}
        # The browser's address: the connection's, or what a trusted proxy says it took the request from.
        request_header X-SealSkin-Client {client_ip}
}

# To the node that runs the session, on its peer listener, trusting the approved nodes' certificates alone.
(to_peer) {
        reverse_proxy {http.request.header.X-Upstream-Peer} {
                transport http {
                        tls
                        tls_trust_pool file {{PEER_TRUST_PATH}}
                        tls_server_name sealskin-peer
                }
                # The node tells a session's own origin by the name the browser asked for.
                header_up Host {http.request.hostport}
                header_up -X-Upstream-Peer
                header_up -X-SealSkin-Secret
                header_up -X-SealSkin-Remote
                header_up -X-SealSkin-Client
                # A config reload, as when a node joins, leaves running streams be.
                stream_close_delay 24h
        }
}

# Everything addressed by a session id: the room, its socket, and the session's own path.
(session_routes) {
        @room path_regexp room ^/(?:ws/)?room/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})(/.*)?$
        handle @room {
                route {
                        forward_auth 127.0.0.1:{{API_PORT}} {
                                uri /internal/route/{re.room.1}
                                copy_headers X-Upstream-Peer
                                header_up -Upgrade
                                header_up -Connection
                        }
                        @peer header X-Upstream-Peer *
                        handle @peer {
                                import to_peer
                        }
                        handle {
                                reverse_proxy 127.0.0.1:{{API_PORT}}
                        }
                }
        }

        @session_path path_regexp session_id ^/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})(/.*)?$

        handle @session_path {
                # A session answers its own pages and navigations to it, never another origin's
                # requests or WebSockets: those carry its cookie wherever the two are same-site.
                @foreign_request expression `{http.request.header.Sec-Fetch-Site} != "" && {http.request.header.Sec-Fetch-Site} != "same-origin" && {http.request.header.Sec-Fetch-Site} != "none" && {http.request.header.Sec-Fetch-Mode} != "navigate"`
                @foreign_socket expression `{http.request.header.Sec-WebSocket-Version} != "" && {http.request.header.Origin} != "" && {http.request.header.Origin} != "https://" + {http.request.host} && !{http.request.header.Origin}.startsWith("https://" + {http.request.host} + ":")`
                handle @foreign_request {
                        respond "Forbidden" 403
                }
                handle @foreign_socket {
                        respond "Forbidden" 403
                }

                @initial_auth query access_token=*
                handle @initial_auth {
                        route {
                                forward_auth 127.0.0.1:{{API_PORT}} {
                                        uri /internal/route/{re.session_id.1}
                                        copy_headers X-Upstream-Peer
                                }
                                @peer header X-Upstream-Peer *
                                handle @peer {
                                        import to_peer
                                }
                                handle {
                                        reverse_proxy 127.0.0.1:{{API_PORT}}
                                }
                        }
                }

                handle {
                        route {
                                forward_auth 127.0.0.1:{{API_PORT}} {
                                        uri /internal/resolve_session/{re.session_id.1}
                                        copy_headers X-Upstream-Host X-Upstream-Auth X-Upstream-Peer
                                        header_up -Upgrade
                                        header_up -Connection
                                }

                                @peer header X-Upstream-Peer *
                                handle @peer {
                                        import to_peer
                                }

                                handle {
                                        reverse_proxy {http.request.header.X-Upstream-Host} {
                                                header_up Host {http.reverse_proxy.upstream.hostport}
                                                header_up Authorization {http.request.header.X-Upstream-Auth}

                                                header_up -X-Upstream-Host
                                                header_up -X-Upstream-Auth
                                                header_up -X-SealSkin-Secret
                                                header_up -X-SealSkin-Remote
                                                header_up -X-SealSkin-Client

                                                # A session's service worker stays under its own path, off the web app at /ui/.
                                                header_down -Service-Worker-Allowed
                                                stream_close_delay 24h
                                        }
                                }
                        }
                }
        }
}

# What browsers reach: the web app, the API, and the sessions.
(entrance) {
        import marks
        request_header -X-SealSkin-Listener

        # Other origins get uncredentialed reads only: a session path authenticates on its cookie alone.
        header {
                Access-Control-Allow-Origin "{header.Origin}"
                Access-Control-Allow-Methods "GET, POST, PUT, DELETE, OPTIONS"
                Access-Control-Allow-Headers "Origin, Accept, Content-Type, X-Requested-With, X-Session-ID, X-Idempotency-Key, Authorization"
                defer
        }

        @options {
                method OPTIONS
        }
        handle @options {
                respond "" 204
        }

        # A session's own origin, whose name starts with its id, serves that session alone, and
        # answers the probe the shells send before opening a session there.
        @own_origin_other {
                header_regexp Host ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\.
                not path_regexp ^/(?:(?:ws/)?room/)?[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}(/.*)?$
                # The room of a web sign-in's session is served there too, with the assets its page loads.
                not path /ui/*
        }
        handle @own_origin_other {
                handle /sealskin-origin {
                        respond "sealskin"
                }
                handle {
                        respond 404
                }
        }

        # forward_auth reaches /internal/* directly on the loopback API port, and nodes reach
        # /peer/* on the peer listener; never expose either to clients.
        handle /internal/* {
                respond "Forbidden" 403
        }
        handle /peer/* {
                respond "Forbidden" 403
        }

        handle /public/* {
                reverse_proxy 127.0.0.1:{{API_PORT}}
        }

        import session_routes

        handle {
                reverse_proxy 127.0.0.1:{{API_PORT}}
        }
}

https://:{{SESSION_PORT}} {
        tls {{PROXY_CERT_PATH}} {{PROXY_KEY_PATH}}

        import entrance
}

{{HTTP_SITE}}
# The peer listener: the other nodes' calls, and the session traffic the frontends proxy here.
https://:{{PEER_PORT}} {
        tls {{PEER_CERT_PATH}} {{PEER_KEY_PATH}}

        import marks
        request_header X-SealSkin-Listener "peer"

        handle /internal/* {
                respond "Forbidden" 403
        }
        handle /peer/* {
                reverse_proxy 127.0.0.1:{{API_PORT}}
        }
        handle /api/* {
                reverse_proxy 127.0.0.1:{{API_PORT}}
        }

        import session_routes

        handle {
                respond 404
        }
}
