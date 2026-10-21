#!/bin/bash
# v2.3 F1: 常驻隧道 + 登记 peer —  ./tunnel.sh <ssh别名> <本地端口> [显示名]
#   例: ./tunnel.sh remote-host 8799 remote-workstation  ⇒ launchd 常驻 ssh -N -L 8799:127.0.0.1:8798 remote-host,
#       peers.local.json 加 {name:"remote-workstation", url:"http://127.0.0.1:8799"}; 首页出「remote-workstation 的板」一节。
# Linux 暂无 systemd 模板: 手起 autossh 后只登记 peer (./tunnel.sh --peer-only …)。
set -euo pipefail
cd "$(dirname "$0")"
PEER_ONLY=0
if [[ "${1:-}" == "--peer-only" ]]; then PEER_ONLY=1; shift; fi
HOST="${1:?ssh 别名}"; LPORT="${2:?本地端口}"; NAME="${3:-$HOST}"
mkdir -p logs
if [[ $PEER_ONLY -eq 0 && "$(uname)" == "Darwin" ]]; then
  LABEL="io.github.willabel4112.review-board-tunnel-$HOST"
  PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
  sed -e "s|__HOST__|$HOST|g" -e "s|__LPORT__|$LPORT|g" -e "s|__LABEL__|$LABEL|g" -e "s|__ROOT__|$PWD|g" \
    launchd/io.github.willabel4112.review-board-tunnel.plist > "$PLIST"
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  launchctl kickstart -k "gui/$(id -u)/$LABEL"
  sleep 2
elif [[ $PEER_ONLY -eq 0 ]]; then
  echo "非 macOS: 请自己常驻 'ssh -N -L $LPORT:127.0.0.1:8798 $HOST' (autossh/systemd), 这里只登记 peer" >&2
fi
python3 - "$NAME" "http://127.0.0.1:$LPORT" <<'PY'
import json, os, sys
p = "peers.local.json"
try:
    ps = json.load(open(p))
except (OSError, ValueError):
    ps = []
name, url = sys.argv[1], sys.argv[2]
ps = [x for x in ps if x.get("url") != url and x.get("name") != name] + [{"name": name, "url": url}]
json.dump(ps, open(p + ".tmp", "w"), ensure_ascii=False, indent=2); os.replace(p + ".tmp", p)
print("peers.local.json:", ps)
PY
if curl -sf "http://127.0.0.1:$LPORT/api/docs" >/dev/null; then echo "OK peer $NAME @ http://127.0.0.1:$LPORT"; else echo "⚠ peer $NAME 还不通 (隧道起了但 ssh 没握上? 看 logs/tunnel-$HOST.log)"; fi
