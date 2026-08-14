#!/usr/bin/env bash
# Puts a `nightwatch` command on your PATH. That is all it does.
#
# It writes one 3-line shim to ~/.local/bin/nightwatch that runs this checkout.
# No copying, no site-packages, no virtualenv — update the repo and the command
# updates with it. Delete the shim to uninstall.
set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
BIN="${NIGHTWATCH_BIN:-$HOME/.local/bin}"
PY="$(command -v python3)"

mkdir -p "$BIN"
cat > "$BIN/nightwatch" <<EOF
#!/usr/bin/env bash
PYTHONPATH="$REPO\${PYTHONPATH:+:\$PYTHONPATH}" exec "$PY" -m nightwatch "\$@"
EOF
chmod +x "$BIN/nightwatch"

echo "installed $BIN/nightwatch -> $REPO"
case ":$PATH:" in
  *":$BIN:"*) ;;
  *) echo "note: $BIN is not on your PATH. Add it, or call $BIN/nightwatch directly." ;;
esac
echo "try: nightwatch init && nightwatch run --dry-run"
