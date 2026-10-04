{
        auto_https off
        log {
                level ERROR
        }
}

https://:{{SESSION_PORT}} {
        tls {{PROXY_CERT_PATH}} {{PROXY_KEY_PATH}}

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
                not path_regexp ^/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}(/.*)?$
        }
        handle @own_origin_other {
                handle /sealskin-origin {
                        respond "sealskin"
                }
                handle {
                        respond 404
                }
        }

        # forward_auth reaches /internal/* directly on the loopback API port;
        # never expose it to clients.
        handle /internal/* {
                respond "Forbidden" 403
        }

        handle /public/* {
                reverse_proxy 127.0.0.1:{{API_PORT}}
        }
        handle /room/* {
                reverse_proxy 127.0.0.1:{{API_PORT}}
        }
        handle /ws/room/* {
                reverse_proxy 127.0.0.1:{{API_PORT}}
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
                        reverse_proxy 127.0.0.1:{{API_PORT}}
                }

                handle {
                        forward_auth 127.0.0.1:{{API_PORT}} {
                                uri /internal/resolve_session/{re.session_id.1}
                                copy_headers X-Upstream-Host X-Upstream-Auth
                                header_up -Upgrade
                                header_up -Connection
                        }

                        reverse_proxy {http.request.header.X-Upstream-Host} {
                                header_up Host {http.reverse_proxy.upstream.hostport}
                                header_up Authorization {http.request.header.X-Upstream-Auth}

                                header_up -X-Upstream-Host
                                header_up -X-Upstream-Auth

                                # A session's service worker stays under its own path, off the web app at /ui/.
                                header_down -Service-Worker-Allowed
                        }
                }
        }

        handle {
                reverse_proxy 127.0.0.1:{{API_PORT}}
        }
}
