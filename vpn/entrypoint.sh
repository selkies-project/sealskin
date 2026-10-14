#!/bin/sh
# SealSkin VPN gateway.
#
# `gateway` (the default, Docker) and `hub` (Kubernetes) read the rendered profile under
# /run/sealskin-vpn, which the server mounts read-only:
#   env          KIND (wireguard|openvpn), ENDPOINT_HOST, ENDPOINT_PORT, ENDPOINT_PROTO (udp|tcp),
#                DNS_UPSTREAM (space separated, may be empty), WG_ADDRESS, WG_MTU; for a hub also
#                HUB_ADDRESS (address/prefix of the hub interface), HUB_PORT, and LOCAL_CIDRS, the
#                cluster's networks, which the hub reaches beside the tunnel
#   wg0.conf     the WireGuard interface and its one peer, as `wg setconf` reads it
#   client.ovpn  the OpenVPN client config without its remote lines
#   auth.txt     the OpenVPN username and password, when the profile has them
#   hub.key      a hub's own WireGuard private key
#   peers        a hub's spokes, one `<public key> <address>` per line, as the server last wrote them
# The container has an egress interface, the one with the default route, which reaches the
# endpoint. A gateway has a second interface on the internal network the sessions sit on; a hub
# has instead a WireGuard interface the sessions' spokes connect to. Only the sessions' traffic
# bound for the tunnel is forwarded, so a tunnel that is down leaves the sessions with nothing,
# and the gateway answers the sessions' DNS through the tunnel.
#
# `spoke` runs beside a session on Kubernetes, in its pod: it connects to the hub named by
# HUB_ENDPOINT and HUB_PUBLIC_KEY with SPOKE_PRIVATE_KEY and SPOKE_ADDRESS, makes that the pod's
# default route, keeps LOCAL_CIDRS (the cluster's private networks) on the pod's own route, and
# lets nothing else out.
# `route <address>` replaces the default route of the current network namespace.
# `health` is the container health check.
set -eu
trap 'rc=$?; [ "$rc" -eq 0 ] || echo "[sealskin-vpn] failed with status $rc at line $LINENO"' EXIT

CONF=/run/sealskin-vpn
STATE=/run/sealskin-vpn-state
mkdir -p "$STATE"
MODE=${1:-gateway}

log() { echo "[sealskin-vpn] $*"; }

case "$MODE" in
  route)
    exec ip route replace default via "$2"
    ;;
  health)
    [ -f "$STATE/healthy" ] || exit 1
    for link in $(cat "$STATE/links"); do
      ip link show "$link" >/dev/null 2>&1 || exit 1
    done
    exit 0
    ;;
  gateway|hub|spoke)
    ;;
  *)
    exec "$@"
    ;;
esac

# The egress network may be attached a moment after the container starts.
waited=0
until ip -4 route show default | grep -q .; do
  sleep 1
  waited=$((waited + 1))
  [ "$waited" -lt 15 ] || break
done
EGRESS_IF=$(ip -4 route show default | awk '$2 == "via" {print $5; exit}')
EGRESS_GW=$(ip -4 route show default | awk '$2 == "via" {print $3; exit}')
if [ -z "$EGRESS_IF" ]; then
  # Started again in a namespace a previous run already routed: the gateway is on the routes it added.
  EGRESS_IF=$(ip -4 route show | awk '$2 == "via" && $5 != "wg0" && $5 != "hub0" {print $5; exit}')
  EGRESS_GW=$(ip -4 route show | awk '$2 == "via" && $5 != "wg0" && $5 != "hub0" {print $3; exit}')
fi
[ -n "$EGRESS_IF" ] || { log "no route to reach anything through"; exit 1; }

# --- A spoke: the pod's traffic goes to the hub, the cluster stays direct -------------------
if [ "$MODE" = spoke ]; then
  : "${SPOKE_PRIVATE_KEY:?}" "${SPOKE_ADDRESS:?}" "${HUB_PUBLIC_KEY:?}" "${HUB_ENDPOINT:?}" "${HUB_ADDRESS:?}"
  LOCAL_CIDRS=$(echo "${LOCAL_CIDRS:-10.0.0.0/8,172.16.0.0/12,192.168.0.0/16}" | tr ',' ' ')
  HUB_IP=${HUB_ENDPOINT%:*}
  HUB_UDP=${HUB_ENDPOINT##*:}
  umask 077
  echo "$SPOKE_PRIVATE_KEY" > "$STATE/spoke.key"
  # The pod's namespace outlives a restart of this container.
  ip link del wg0 2>/dev/null || true
  ip link add wg0 type wireguard
  wg set wg0 private-key "$STATE/spoke.key" peer "$HUB_PUBLIC_KEY" endpoint "$HUB_ENDPOINT" allowed-ips 0.0.0.0/0 persistent-keepalive 25
  ip -4 addr add "$SPOKE_ADDRESS" dev wg0
  ip link set wg0 mtu "${WG_MTU:-1340}" up
  log "spoke interface up; hub $HUB_ENDPOINT via $EGRESS_IF ($EGRESS_GW)"
  for cidr in $LOCAL_CIDRS; do
    ip route replace "$cidr" via "$EGRESS_GW" dev "$EGRESS_IF"
  done
  ip route replace "$HUB_IP/32" via "$EGRESS_GW" dev "$EGRESS_IF"
  # The hub's inner address sits inside the private ranges and goes through the tunnel.
  ip route replace "$HUB_ADDRESS/32" dev wg0
  ip route replace default dev wg0
  LOCAL_SET=$(echo "$LOCAL_CIDRS" | tr ' ' ',')
  nft -f - <<RULES
flush ruleset
table inet sealskin {
  chain output {
    type filter hook output priority 0; policy drop;
    oif lo accept
    ct state established,related accept
    oifname "wg0" accept
    oifname "$EGRESS_IF" ip daddr { $LOCAL_SET } accept
    oifname "$EGRESS_IF" ip daddr $HUB_IP udp dport $HUB_UDP accept
  }
}
RULES
  echo "wg0" > "$STATE/links"
  waited=0
  until ping -c 1 -W 2 "$HUB_ADDRESS" >/dev/null 2>&1; do
    waited=$((waited + 1))
    [ "$waited" -lt 30 ] || { log "the hub at $HUB_ENDPOINT does not answer"; exit 1; }
    sleep 2
  done
  log "connected to the hub at $HUB_ENDPOINT; the pod leaves through it, $LOCAL_SET stay direct"
  touch "$STATE/healthy"
  while ip link show wg0 >/dev/null 2>&1; do sleep 5; done
  log "the spoke interface is gone"
  exit 1
fi

# --- A gateway or a hub: the tunnel to the provider, and the sessions' way into it -----------
[ -f "$CONF/env" ] || { log "no profile under $CONF"; exit 1; }
# shellcheck disable=SC1091
. "$CONF/env"

if [ "$MODE" = hub ]; then
  # Forwarding is inherited from the node's network namespace; a cluster that turns it off needs
  # the sysctl allowed on the pod.
  [ "$(cat /proc/sys/net/ipv4/ip_forward)" = 1 ] || { log "net.ipv4.ip_forward is off in this pod; allow the sysctl or forward on the node"; exit 1; }
  : "${HUB_ADDRESS:?}" "${HUB_PORT:=51820}"
  SESSIONS_IF=hub0
  SESSIONS_IP=${HUB_ADDRESS%/*}
  ip link del hub0 2>/dev/null || true
  ip link add hub0 type wireguard
  wg set hub0 private-key "$CONF/hub.key" listen-port "$HUB_PORT"
  if [ -f "$CONF/peers" ]; then
    while read -r peer address; do
      [ -n "$peer" ] && wg set hub0 peer "$peer" allowed-ips "$address"
    done < "$CONF/peers"
  fi
  ip -4 addr add "$HUB_ADDRESS" dev hub0
  ip link set hub0 mtu 1340 up
else
  SESSIONS_IF=$(ip -o link show | awk -F': ' '{print $2}' | cut -d@ -f1 | grep -v -e '^lo$' -e "^$EGRESS_IF$" | head -n1)
  [ -n "$SESSIONS_IF" ] || { log "need an internal interface for the sessions"; exit 1; }
  SESSIONS_IP=$(ip -4 -o addr show dev "$SESSIONS_IF" | awk '{print $4}' | cut -d/ -f1)
fi

# The endpoint is resolved once, before the firewall closes, and reached by address from then on.
ENDPOINT_IP=$(getent ahostsv4 "$ENDPOINT_HOST" | awk '{print $1; exit}')
[ -n "$ENDPOINT_IP" ] || { log "cannot resolve $ENDPOINT_HOST"; exit 1; }
log "endpoint $ENDPOINT_HOST is $ENDPOINT_IP:$ENDPOINT_PORT/$ENDPOINT_PROTO via $EGRESS_IF, sessions on $SESSIONS_IF ($SESSIONS_IP)"

case "$KIND" in
  wireguard) TUN=wg0 ;;
  openvpn) TUN=tun0 ;;
  *) log "unknown kind $KIND"; exit 1 ;;
esac
if [ "$MODE" = hub ]; then echo "$TUN hub0" > "$STATE/links"; else echo "$TUN" > "$STATE/links"; fi

HUB_IN=""
[ "$MODE" = hub ] && HUB_IN="iifname \"$EGRESS_IF\" udp dport $HUB_PORT accept"
nft -f - <<RULES
flush ruleset
table inet sealskin {
  chain input {
    type filter hook input priority 0; policy drop;
    iif lo accept
    ct state established,related accept
    $HUB_IN
    iifname "$SESSIONS_IF" udp dport 53 accept
    iifname "$SESSIONS_IF" tcp dport 53 accept
    iifname "$SESSIONS_IF" icmp type echo-request accept
  }
  chain forward {
    type filter hook forward priority 0; policy drop;
    iifname "$SESSIONS_IF" oifname "$TUN" accept
    iifname "$TUN" oifname "$SESSIONS_IF" ct state established,related accept
  }
  chain output {
    type filter hook output priority 0; policy drop;
    oif lo accept
    ct state established,related accept
    oifname "$TUN" accept
    oifname "$SESSIONS_IF" accept
    oifname "$EGRESS_IF" ip daddr $ENDPOINT_IP $ENDPOINT_PROTO dport $ENDPOINT_PORT accept
  }
  chain postrouting {
    type nat hook postrouting priority 100;
    oifname "$TUN" masquerade
  }
}
RULES

ip route replace "$ENDPOINT_IP/32" via "$EGRESS_GW" dev "$EGRESS_IF"
if [ "$MODE" = hub ]; then
  # The spokes are on the cluster's network, which stays reachable beside the tunnel.
  for cidr in $(echo "${LOCAL_CIDRS:-10.0.0.0/8,172.16.0.0/12,192.168.0.0/16}" | tr ',' ' '); do
    ip route replace "$cidr" via "$EGRESS_GW" dev "$EGRESS_IF"
  done
fi
rm -f "$STATE/dns"

if [ "$KIND" = wireguard ]; then
  ip link del "$TUN" 2>/dev/null || true
  ip link add "$TUN" type wireguard
  sed "s|^Endpoint *=.*|Endpoint = $ENDPOINT_IP:$ENDPOINT_PORT|" "$CONF/wg0.conf" > "$STATE/wg0.conf"
  wg setconf "$TUN" "$STATE/wg0.conf"
  for address in $(echo "$WG_ADDRESS" | tr ',' ' '); do
    case "$address" in *:*) ;; *) ip -4 addr add "$address" dev "$TUN" ;; esac
  done
  [ -n "${WG_MTU:-}" ] && ip link set "$TUN" mtu "$WG_MTU"
  ip link set "$TUN" up
  ip route replace default dev "$TUN"
else
  cat > "$STATE/up.sh" <<'UP'
#!/bin/sh
# OpenVPN's up script: keep the DNS servers the server pushed for dnsmasq.
i=1
: > /run/sealskin-vpn-state/dns
while eval "option=\${foreign_option_$i:-}"; [ -n "$option" ]; do
  case "$option" in "dhcp-option DNS "*) echo "${option#dhcp-option DNS }" >> /run/sealskin-vpn-state/dns ;; esac
  i=$((i + 1))
done
touch /run/sealskin-vpn-state/tun-up
UP
  chmod 0755 "$STATE/up.sh"
  AUTH=""
  [ -f "$CONF/auth.txt" ] && AUTH="--auth-user-pass $CONF/auth.txt"
  # shellcheck disable=SC2086
  openvpn --config "$CONF/client.ovpn" --remote "$ENDPOINT_IP" "$ENDPOINT_PORT" "$ENDPOINT_PROTO" \
    --dev "$TUN" --dev-type tun $AUTH --auth-nocache --redirect-gateway def1 \
    --pull-filter ignore "route-ipv6" --pull-filter ignore "ifconfig-ipv6" --pull-filter ignore "redirect-gateway ipv6" \
    --script-security 2 --up "$STATE/up.sh" --log "$STATE/openvpn.log" --daemon --writepid "$STATE/openvpn.pid"
  waited=0
  until [ -f "$STATE/tun-up" ]; do
    sleep 1
    waited=$((waited + 1))
    if [ "$waited" -ge 60 ] || ! kill -0 "$(cat "$STATE/openvpn.pid" 2>/dev/null || echo 0)" 2>/dev/null; then
      log "OpenVPN did not come up"
      tail -n 20 "$STATE/openvpn.log" || true
      exit 1
    fi
  done
fi

UPSTREAM=$(cat "$STATE/dns" 2>/dev/null | tr '\n' ' ')
[ -n "$(echo "$UPSTREAM" | tr -d ' ')" ] || UPSTREAM="$DNS_UPSTREAM"
[ -n "$(echo "$UPSTREAM" | tr -d ' ')" ] || UPSTREAM="1.1.1.1 1.0.0.1"
: > /etc/resolv.conf
SERVERS=""
for server in $UPSTREAM; do
  echo "nameserver $server" >> /etc/resolv.conf
  SERVERS="$SERVERS --server=$server"
done
# shellcheck disable=SC2086
dnsmasq --no-resolv --no-hosts --cache-size=1000 --bind-interfaces --listen-address="$SESSIONS_IP" --listen-address=127.0.0.1 $SERVERS
log "tunnel $TUN is up; sessions resolve through $UPSTREAM"

waited=0
until exit_ip=$(curl -4 -s --max-time 10 https://1.1.1.1/cdn-cgi/trace | awk -F= '/^ip=/{print $2}') && [ -n "$exit_ip" ]; do
  waited=$((waited + 1))
  [ "$waited" -lt 6 ] || { log "nothing answers through the tunnel"; exit 1; }
  sleep 5
done
log "traffic leaves as $exit_ip"
touch "$STATE/healthy"

if [ "$KIND" = openvpn ]; then
  while kill -0 "$(cat "$STATE/openvpn.pid")" 2>/dev/null; do sleep 5; done
  log "OpenVPN exited"
  tail -n 20 "$STATE/openvpn.log" || true
  exit 1
fi
while ip link show "$TUN" >/dev/null 2>&1; do sleep 5; done
log "the tunnel interface is gone"
exit 1
