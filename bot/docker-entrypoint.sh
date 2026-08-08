#!/bin/sh
set -eu

if [ "${PROXYCHAINS_ENABLED:-0}" != "1" ]; then
    exec "$@"
fi

: "${OUTBOUND_PROXY_URL:?OUTBOUND_PROXY_URL is required}"
case "$OUTBOUND_PROXY_URL" in
    *"#"*|*[[:space:]]*)
        printf '%s\n' 'Invalid OUTBOUND_PROXY_URL' >&2
        exit 1
        ;;
esac

python_bin=${PROXYCHAINS_PYTHON_BIN:-python3}
proxy_row=$(OUTBOUND_PROXY_URL="$OUTBOUND_PROXY_URL" "$python_bin" - <<'PY'
import os
import socket
from urllib.parse import unquote, urlsplit

url = os.environ["OUTBOUND_PROXY_URL"]
try:
    parsed = urlsplit(url)
    port = parsed.port
    username = unquote(parsed.username or "")
    password = unquote(parsed.password or "")
    if (
        parsed.scheme.lower() != "http"
        or parsed.hostname != "***REMOVED***"
        or port != 49855
        or not username
        or not password
        or parsed.path
        or parsed.query
        or parsed.fragment
        or any(character.isspace() or character == "#" for character in username + password)
    ):
        raise ValueError
    address = next(
        item[4][0]
        for item in socket.getaddrinfo(parsed.hostname, port, socket.AF_INET, socket.SOCK_STREAM)
    )
except (OSError, StopIteration, ValueError):
    raise SystemExit("Invalid OUTBOUND_PROXY_URL")
print(f"http {address} {port} {username} {password}")
PY
) || exit 1

config_path=${PROXYCHAINS_CONFIG_PATH:-/tmp/proxychains4.conf}
umask 077
: > "$config_path"
chmod 600 "$config_path"
cat > "$config_path" <<EOF
strict_chain
quiet_mode
tcp_read_time_out 15000
tcp_connect_time_out 8000
localnet 127.0.0.0/255.0.0.0
localnet 10.0.0.0/255.0.0.0
localnet 172.16.0.0/255.240.0.0
localnet 192.168.0.0/255.255.0.0
[ProxyList]
$proxy_row
EOF

hosts_file=${PROXYCHAINS_HOSTS_FILE:-/etc/hosts}
refresh_resolve_hosts() {
    [ -n "${RESOLVE_HOSTS:-}" ] || return 0
    [ -w "$hosts_file" ] || return 0
    pattern=$(printf '%s' "$RESOLVE_HOSTS" | tr ',' '|')
    tmp_file="${hosts_file}.tmp.$$"
    grep -vE "[[:space:]](${pattern})\$" "$hosts_file" > "$tmp_file" 2>/dev/null || : > "$tmp_file"
    for host in $(printf '%s' "$RESOLVE_HOSTS" | tr ',' ' '); do
        address=$(getent ahostsv4 "$host" 2>/dev/null | awk 'NR == 1 { print $1 }') || true
        if [ -n "$address" ]; then
            printf '%s %s\n' "$address" "$host" >> "$tmp_file"
        fi
    done
    cat "$tmp_file" > "$hosts_file"
    rm -f "$tmp_file"
}

refresh_resolve_hosts
if [ -n "${RESOLVE_HOSTS:-}" ]; then
    interval=${RESOLVE_HOSTS_REFRESH_SECONDS:-15}
    ( while sleep "$interval"; do refresh_resolve_hosts; done ) &
fi

exec proxychains4 -f "$config_path" "$@"
