#!/bin/bash
# Build the Lambda deployment package.
#
#   bash scripts/build-lambda-package.sh [outdir]     # default: dist/
#
# One script, used by both the deploy workflow and anyone verifying locally —
# so "it built in CI" and "it built on my machine" mean the same thing. The
# first version of this lived inline in the workflow and again in a local
# command, and the local copy silently produced an EMPTY requirements file
# through a quoting layer, which built a 1MB package that looked fine.
set -euo pipefail

cd "$(dirname "$0")/.."
OUT="${1:-dist}"
BUILD=build/package

# MUST BUILD ON LINUX. pip resolves wheels for the machine it runs on, and
# scipy/numpy/scikit-learn/cryptography all ship compiled binaries. Built on
# Windows this script produces win_amd64 .pyd files; on macOS, macosx .so
# files. Either one packages, uploads and applies without a single error, and
# then the function dies at import on the first real request - the most
# expensive place to find out.
#
# On a Windows checkout, run it from WSL:  wsl bash scripts/build-lambda-package.sh
# If you genuinely must cross-build, pip can do it with
#   --platform manylinux2014_x86_64 --only-binary=:all:
# but that is not wired in here, because nothing in this project needs it.
OS=$(uname -s)
case "$OS" in
  Linux) ;;
  *)
    echo "ERROR: runtime is Linux; this is $OS. The wheels pip picks here will" >&2
    echo "       not load on Lambda, and nothing before production would say so." >&2
    echo "       Run it under WSL:  wsl bash scripts/build-lambda-package.sh" >&2
    exit 1
    ;;
esac

# Runtime dependencies only. requirements.txt carries a "# dev/test only"
# marker; pytest, moto and httpx2 exist to test the thing, not to run it.
# Cutting at the marker keeps one source of truth instead of a second
# requirements file that drifts out of step.
sed '/# dev\/test only/,$d' services/backend/requirements.txt > /tmp/runtime-reqs.txt

if [ ! -s /tmp/runtime-reqs.txt ]; then
  echo "ERROR: runtime requirements came out empty - check the dev/test marker" >&2
  exit 1
fi

echo "--- runtime dependencies ---"
cat /tmp/runtime-reqs.txt

rm -rf "$BUILD" && mkdir -p "$BUILD" "$OUT"
pip install -r /tmp/runtime-reqs.txt --target "$BUILD" --quiet
cp -r services "$BUILD/services"

# Trimming, measured rather than assumed. The untrimmed package is 234MB
# unzipped / 77MB zipped; these three cuts bring it to 200MB / 62MB and the
# app still imports afterwards (verified, not inferred).
#
#  - boto3/botocore/s3transfer: the Lambda Python runtime ships them already.
#    ~21MB of the package was a second copy.
#  - tests and __pycache__: dead weight in a deployment artifact.
#  - .so debug symbols: strip is safe and reclaims several MB from scipy/numpy.
#
# NOT removed, deliberately: *.dist-info. Deleting it saves ~3MB and breaks
# importlib.metadata for any dependency that reads its own version at runtime.
# Three megabytes is not worth a failure mode that only appears in production.
rm -rf "$BUILD"/boto3 "$BUILD"/botocore "$BUILD"/s3transfer
rm -rf "$BUILD"/boto3-*.dist-info "$BUILD"/botocore-*.dist-info "$BUILD"/s3transfer-*.dist-info
find "$BUILD" -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$BUILD" -name "tests" -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$BUILD" -name "*.so" -exec strip --strip-unneeded {} + 2>/dev/null || true

# Belt and braces: even on Linux, a stray Windows or macOS wheel in the tree
# (a leftover build directory, a wheel cache copied between machines) would
# ship silently. Assert what actually landed rather than trusting the check
# above.
if find "$BUILD" -name "*.pyd" | grep -q .; then
  echo "ERROR: Windows .pyd extensions in the package - it will not import on Lambda" >&2
  exit 1
fi
if find "$BUILD" -name "*.so" | grep -qv -- "-linux-gnu.so$"; then
  echo "NOTE: some .so files are not named *-linux-gnu.so; check them before deploying:" >&2
  find "$BUILD" -name "*.so" | grep -v -- "-linux-gnu.so$" | head -5 >&2
fi

UNZIPPED=$(du -sm "$BUILD" | cut -f1)
echo "--- size ---"
echo "unzipped: ${UNZIPPED}MB (Lambda hard limit 250MB)"

# Fail here with a clear message rather than having the upload rejected part
# way through an apply. scikit-learn plus numpy get uncomfortably close.
if [ "$UNZIPPED" -ge 250 ]; then
  echo "ERROR: package exceeds Lambda's 250MB unzipped limit" >&2
  exit 1
fi

# python -m zipfile rather than the zip binary: zip is not installed
# everywhere, python is, and this script has to work in CI and on a laptop.
python3 -m zipfile -c "$OUT/backend.zip" "$BUILD"/*
ZIPPED=$(( $(stat -c%s "$OUT/backend.zip") / 1048576 ))
echo "zipped:   ${ZIPPED}MB"

# Lambda accepts at most 50MB zipped on a DIRECT upload. This package is ~62MB
# even fully trimmed, which is why terraform ships it through S3 rather than
# with `filename` — see docs/adr/004-lambda-artifact-via-s3.md. If a future
# trim ever gets under 50MB, direct upload becomes possible again and that ADR
# can be revisited.
if [ "$ZIPPED" -lt 50 ]; then
  echo "NOTE: now under 50MB zipped - direct upload is possible again, see ADR-004"
fi
echo "OK"
