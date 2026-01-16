{
        auto_https off
        log {
                level ERROR
        }
}

https://:{{SESSION_PORT}} {
        tls {{PROXY_CERT_PATH}} {{PROXY_KEY_PATH}}

        header {
                Access-Control-Allow-Origin "{header.Origin}"
                Access-Control-Allow-Methods "GET, POST, PUT, DELETE, OPTIONS"
                Access-Control-Allow-Headers "Origin, Accept, Content-Type, X-Requested-With, X-Session-ID, Authorization"
                Access-Control-Allow-Credentials "true"
                defer 
        }

        @options {
                method OPTIONS
        }
        handle @options {
                respond "" 204
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
                        }
                }
        }

        handle {
                reverse_proxy 127.0.0.1:{{API_PORT}}
        }
}
