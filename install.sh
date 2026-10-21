#!/bin/bash
# 幂等安装/重装常驻 (先测试后部署)。macOS=launchd, Linux=systemd --user — 同一入口两平台。
set -euo pipefail
cd "$(dirname "$0")"
python3 tests/test_lib.py
mkdir -p logs
if [[ "$(uname)" == "Darwin" ]]; then
  PLIST="$HOME/Library/LaunchAgents/io.github.willabel4112.review-board.plist"
  sed "s|__ROOT__|$PWD|g" launchd/io.github.willabel4112.review-board.plist > "$PLIST"
  launchctl bootout "gui/$(id -u)/io.github.willabel4112.review-board" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  launchctl kickstart -k "gui/$(id -u)/io.github.willabel4112.review-board"
else
  UNIT="$HOME/.config/systemd/user/review-board.service"
  mkdir -p "$(dirname "$UNIT")"
  sed "s|__ROOT__|$PWD|g" systemd/review-board.service > "$UNIT"
  systemctl --user daemon-reload
  systemctl --user enable --now review-board.service
  systemctl --user restart review-board.service
fi
# B3 (v2.3): 可执行入口进 PATH (~/.local/bin 两平台默认都在 PATH 里; 没有就自己加)
mkdir -p "$HOME/.local/bin"
ln -sfn "$PWD/bin/boardctl" "$HOME/.local/bin/boardctl"
sleep 1
curl -sf http://127.0.0.1:8798/api/docs >/dev/null
echo "OK http://127.0.0.1:8798  (boardctl -> ~/.local/bin/boardctl)"
