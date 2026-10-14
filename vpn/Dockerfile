# syntax=docker/dockerfile:1
# The VPN gateway SealSkin starts beside the sessions that chose a VPN: it holds the tunnel,
# forwards the sessions' traffic into it and nothing anywhere else, and answers their DNS.
# The same image, run as `sealskin-vpn route <address>`, is the one-shot helper that points a
# session's default route at the gateway from inside the session's network namespace.
FROM alpine:3.21
RUN apk add --no-cache wireguard-tools openvpn nftables dnsmasq iproute2 musl-utils curl
COPY entrypoint.sh /usr/local/bin/sealskin-vpn
RUN chmod 0755 /usr/local/bin/sealskin-vpn
HEALTHCHECK --interval=15s --timeout=5s --start-period=90s --retries=3 CMD ["/usr/local/bin/sealskin-vpn", "health"]
ENTRYPOINT ["/usr/local/bin/sealskin-vpn"]
CMD ["gateway"]
