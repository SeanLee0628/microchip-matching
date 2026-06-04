#!/bin/bash
set -e
echo "[prebuild] Installing Korean CJK fonts..."
dnf install -y google-noto-sans-cjk-fonts 2>/dev/null \
    || dnf install -y google-noto-cjk-fonts 2>/dev/null \
    || true
fc-cache -f 2>/dev/null || true
echo "[prebuild] Done."
