#!/usr/bin/env bash
# Set up a local film-maker-ai environment (macOS / Linux).
#   bash setup_local.sh                 -> ~/Desktop/Projects/film-maker-ai
#   TARGET=/some/dir bash setup_local.sh
set -euo pipefail

REPO="${REPO:-https://github.com/napro8509/film-maker-ai}"
BRANCH="${BRANCH:-claude/relaxed-bohr-q25gvh}"
TARGET="${TARGET:-$HOME/Desktop/Projects/film-maker-ai}"

step() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m!  %s\033[0m\n' "$*"; }

step "Kiểm tra công cụ cần có"
command -v git >/dev/null || { echo "Cần cài git"; exit 1; }
PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then PY="$c"; break; fi
done
[ -n "$PY" ] || { echo "Cần Python 3.10+ (https://www.python.org/downloads/)"; exit 1; }
echo "Python: $($PY --version)"
if ! command -v ffmpeg >/dev/null; then
  warn "Chưa có ffmpeg (cần cho bước ghép phim). macOS: brew install ffmpeg | Ubuntu: sudo apt install ffmpeg"
fi

step "Lấy code vào $TARGET"
if [ -d "$TARGET/.git" ]; then
  git -C "$TARGET" fetch origin "$BRANCH"
  git -C "$TARGET" checkout "$BRANCH"
  git -C "$TARGET" pull --ff-only origin "$BRANCH"
else
  mkdir -p "$(dirname "$TARGET")"
  git clone -b "$BRANCH" "$REPO" "$TARGET"
fi
cd "$TARGET"

step "Tạo môi trường Python (.venv) và cài filmmaker"
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q -e '.[ai,dev]'

step "Cài gflow-cli"
if ! command -v uv >/dev/null; then
  echo "Cài uv (trình quản lý gói Python)..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi
if command -v gflow >/dev/null; then
  uv tool upgrade gflow-cli || true
else
  uv tool install gflow-cli
fi
export PATH="$HOME/.local/bin:$PATH"
uv tool update-shell >/dev/null 2>&1 || true   # keep gflow on PATH in new terminals
uv tool run --from gflow-cli playwright install chromium \
  || warn "Chưa tải được Chromium. Chạy lại sau: uv tool run --from gflow-cli playwright install chromium"

step "Chạy test (dùng gflow giả, không tốn credit)"
if command -v ffmpeg >/dev/null; then .venv/bin/pytest -q; else warn "Bỏ qua test vì chưa có ffmpeg"; fi

step "Xong!"
cat <<MSG
Thư mục: $TARGET

Bước tiếp theo:
  cd "$TARGET" && source .venv/bin/activate
  gflow auth login --browser chrome      # đăng nhập Google (một lần)
  filmmaker check
  filmmaker template test.yaml && filmmaker run test.yaml --dry-run
MSG
