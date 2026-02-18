#!/usr/bin/env bash
# OpenCastor Post-Install Verification
set -uo pipefail

PASS=0; FAIL=0
check() {
  local name="$1"; shift
  if "$@" &>/dev/null; then
    echo "  ✅ $name"
    ((PASS++))
  else
    echo "  ❌ $name"
    ((FAIL++))
  fi
}

echo ""
echo "OpenCastor Install Check"
echo "========================"

# Find python
PY=""
for p in python3 python; do
  if command -v "$p" &>/dev/null; then PY="$p"; break; fi
done

check "Python found" test -n "$PY"
check "Python 3.10+" $PY -c "import sys; assert sys.version_info >= (3,10)"
check "pip available" $PY -m pip --version
check "venv module" $PY -c "import venv"
check "git installed" git --version

# Check key Python deps
for mod in cv2 numpy pydantic yaml; do
  check "import $mod" $PY -c "import $mod"
done

# Check castor CLI (try binary first, then python -m)
if command -v castor &>/dev/null; then
  check "castor CLI" castor --help
else
  check "castor CLI" $PY -m castor --help
fi

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] && echo "🎉 All checks passed!" || echo "⚠️  Some checks failed. Review above."
exit "$FAIL"
