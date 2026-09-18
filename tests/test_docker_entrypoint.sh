#!/bin/sh
set -eu

entrypoint=${1:?entrypoint path is required}
tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT
mkdir "$tmpdir/bin" "$tmpdir/site"
real_python=$(command -v python3)
cat > "$tmpdir/bin/proxychains4" <<'EOF'
#!/bin/sh
set -eu
[ "$1" = "-f" ]
cp "$2" "$TEST_CAPTURE"
shift 2
exec "$@"
EOF
cat > "$tmpdir/bin/getent" <<'EOF'
#!/bin/sh
set -eu
[ "$1" = "ahostsv4" ]
case "$2" in
    api) printf '%s\n' '172.20.0.2 STREAM api' ;;
    redis) printf '%s\n' '10.0.0.2 STREAM redis' ;;
    *) exit 2 ;;
esac
EOF
cat > "$tmpdir/bin/python3" <<'EOF'
#!/bin/sh
set -eu
PYTHONPATH="$TEST_SITE_DIR${PYTHONPATH:+:$PYTHONPATH}" exec "$TEST_REAL_PYTHON_BIN" "$@"
EOF
cat > "$tmpdir/site/sitecustomize.py" <<'EOF'
import os
import socket

_original_getaddrinfo = socket.getaddrinfo


def _getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    if host == "proxy.example.test":
        if os.environ.get("TEST_SOCKET_MODE") == "error":
            raise OSError("test resolver failure")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.44", port))]
    return _original_getaddrinfo(host, port, family, type, proto, flags)


socket.getaddrinfo = _getaddrinfo
EOF
chmod +x "$tmpdir/bin/proxychains4" "$tmpdir/bin/getent" "$tmpdir/bin/python3"

config="$tmpdir/proxychains.conf"
capture="$tmpdir/captured.conf"
hosts="$tmpdir/hosts"
touch "$config" "$hosts"
chmod 644 "$config"
TEST_CAPTURE="$capture" TEST_REAL_PYTHON_BIN="$real_python" TEST_SITE_DIR="$tmpdir/site" \
PATH="$tmpdir/bin:$PATH" PROXYCHAINS_ENABLED=1 \
OUTBOUND_PROXY_URL='http://user%40name:secret%40value@proxy.example.test:49855' \
OUTBOUND_PROXY_HOST='proxy.example.test' OUTBOUND_PROXY_PORT=49855 \
PROXYCHAINS_PYTHON_BIN="$tmpdir/bin/python3" PROXYCHAINS_CONFIG_PATH="$config" \
PROXYCHAINS_HOSTS_FILE="$hosts" RESOLVE_HOSTS='api,redis' sh "$entrypoint" /bin/true

[ "$(stat -c %a "$config")" = "600" ]
grep -qx 'http 192.0.2.44 49855 user@name secret@value' "$capture"
grep -qx 'strict_chain' "$capture"
grep -qx 'localnet 127.0.0.0/255.0.0.0' "$capture"
grep -qx 'localnet 10.0.0.0/255.0.0.0' "$capture"
grep -qx 'localnet 172.16.0.0/255.240.0.0' "$capture"
grep -qx 'localnet 192.168.0.0/255.255.0.0' "$capture"
! grep -q '^proxy_dns$' "$capture"
grep -qx '172.20.0.2 api' "$hosts"
grep -qx '10.0.0.2 redis' "$hosts"

if TEST_REAL_PYTHON_BIN="$real_python" TEST_SITE_DIR="$tmpdir/site" PATH="$tmpdir/bin:$PATH" \
PROXYCHAINS_ENABLED=1 OUTBOUND_PROXY_URL='http://user:secret@proxy.test:49855' \
OUTBOUND_PROXY_HOST='proxy.example.test' \
PROXYCHAINS_PYTHON_BIN="$tmpdir/bin/python3" PROXYCHAINS_CONFIG_PATH="$tmpdir/invalid.conf" \
sh "$entrypoint" /bin/true; then
    printf '%s\n' 'invalid proxy was accepted' >&2
    exit 1
fi

if TEST_REAL_PYTHON_BIN="$real_python" TEST_SITE_DIR="$tmpdir/site" TEST_SOCKET_MODE=error \
PATH="$tmpdir/bin:$PATH" PROXYCHAINS_ENABLED=1 \
OUTBOUND_PROXY_URL='http://user:secret@proxy.example.test:49855' \
OUTBOUND_PROXY_HOST='proxy.example.test' OUTBOUND_PROXY_PORT=49855 \
PROXYCHAINS_PYTHON_BIN="$tmpdir/bin/python3" PROXYCHAINS_CONFIG_PATH="$tmpdir/resolver-error.conf" \
sh "$entrypoint" /bin/true; then
    printf '%s\n' 'resolver failure was accepted' >&2
    exit 1
fi

marker="$tmpdir/disabled"
PROXYCHAINS_ENABLED=0 sh "$entrypoint" sh -c "touch '$marker'"
[ -f "$marker" ]
