#!/bin/bash
# BBB Scraper — proxy relay via mitmproxy
#
# Starts a local mitmproxy relay that adds authentication to the upstream
# residential proxy, then runs bbb_uc.py through it.
#
# One-time setup:
#   pip install mitmproxy undetected-chromedriver
#   # Trust mitmproxy cert so Chrome doesn't prompt:
#   mitmdump --listen-port 18080 &  # run once, Ctrl-C after a second
#   sudo security add-trusted-cert -d -r trustRoot \
#       -k /Library/Keychains/System.keychain \
#       ~/.mitmproxy/mitmproxy-ca-cert.pem
#
# Usage:
#   chmod +x run_with_proxy.sh
#   ./run_with_proxy.sh 'http://user:pass@host:port' 'restaurants' 'New York, NY'
#   ./run_with_proxy.sh 'http://user:pass@host:port' 'lawyers' 'Chicago, IL' \
#       --output lawyers.json --max-pages 10

UPSTREAM_PROXY="$1"
KEYWORD="$2"
LOCATION="$3"
LOCAL_PORT=18080

if [ -z "$UPSTREAM_PROXY" ] || [ -z "$KEYWORD" ] || [ -z "$LOCATION" ]; then
    echo "Usage: $0 'http://user:pass@host:port' 'keyword' 'location' [extra flags]"
    echo "Example: $0 'http://USER:PASS@PROXY_HOST:PORT' 'restaurants' 'New York, NY'"
    exit 1
fi

# Parse proxy URL parts
PROXY_USER=$(echo "$UPSTREAM_PROXY" | sed 's|https\?://||' | cut -d: -f1)
PROXY_PASS=$(echo "$UPSTREAM_PROXY" | sed 's|https\?://[^:]*:||' | cut -d@ -f1)
PROXY_HOST=$(echo "$UPSTREAM_PROXY" | sed 's|.*@||' | cut -d: -f1)
PROXY_PORT=$(echo "$UPSTREAM_PROXY" | awk -F: '{print $NF}')

echo "[proxy] Starting local relay on port $LOCAL_PORT..."
echo "[proxy] Upstream: $PROXY_HOST:$PROXY_PORT"

# mitmproxy 11+ syntax: credentials via --upstream-auth
mitmdump \
    --listen-port $LOCAL_PORT \
    --mode "upstream:http://$PROXY_HOST:$PROXY_PORT" \
    --upstream-auth "$PROXY_USER:$PROXY_PASS" \
    --ssl-insecure \
    --quiet &
MITM_PID=$!

sleep 3

if ! kill -0 $MITM_PID 2>/dev/null; then
    echo "[proxy] ERROR: mitmdump failed to start"
    exit 1
fi
echo "[proxy] mitmdump running (PID=$MITM_PID)"

echo "[scraper] Starting BBB scraper..."
python bbb_uc.py \
    --mode search \
    --keyword "$KEYWORD" \
    --location "$LOCATION" \
    --proxy "http://localhost:$LOCAL_PORT" \
    "${@:4}"

EXIT_CODE=$?

echo "[proxy] Stopping mitmproxy..."
kill $MITM_PID 2>/dev/null
wait $MITM_PID 2>/dev/null

exit $EXIT_CODE
