#!/bin/bash
# Post-deploy smoke test.
#
#   bash scripts/smoke-test.sh https://xxxx.lambda-url.ap-southeast-1.on.aws
#
# Deliberately needs no credentials, so it can run unattended in the deploy
# pipeline. That constrains what it can prove — and the constraint is the point:
# these five checks are things that are TRUE OR FALSE about a live deployment,
# not things that merely look reassuring.
#
# What it does NOT prove: that a real agent can register, that telemetry scores,
# that the nightly retrain fires. Those need a Cognito user and a tenant, and
# claiming a green smoke test covers them would be exactly the narration this
# project spent a phase removing.
set -u

URL="${1:?usage: smoke-test.sh <function-url>}"
URL="${URL%/}"
FAILED=0

check() {
  if [ "$1" = "0" ]; then
    echo "  PASS  $2"
  else
    echo "  FAIL  $2"
    FAILED=$((FAILED + 1))
  fi
}

echo "Smoke test against $URL"

# 1. The function is alive and its unmetered path answers.
BODY=$(curl -fsS --max-time 20 "$URL/health" 2>/dev/null)
echo "$BODY" | grep -q '"status"' && [ -n "$BODY" ]
check $? "/health responds with a status body"

# 2. The deployment can actually SERVE, not merely answer. /ready describes a
#    DynamoDB table and checks that both Cognito values are set. This is the
#    check that catches the two failure modes this project has actually had:
#    tables that were never created (they come from application code, not
#    Terraform) and a Cognito pool left unconfigured, which makes auth fail
#    closed while /health still says healthy.
READY=$(curl -sS --max-time 20 -w '
%{http_code}' "$URL/ready")
CODE=$(echo "$READY" | tail -1)
[ "$CODE" = "200" ]
check $? "/ready is 200 (got $CODE): $(echo "$READY" | head -1)"

# 3. Auth is wired and fails CLOSED. An unauthenticated call to a tenant route
#    must be refused. A 200 here would mean tenant data is public; a 500 would
#    mean Cognito is misconfigured rather than protective.
CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$URL/dashboard/v1/mitigations")
[ "$CODE" = "401" ]
check $? "unauthenticated /dashboard/v1/mitigations is 401 (got $CODE)"

# 4. The agent surface refuses an unauthenticated write.
CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 \
  -X POST -H 'content-type: application/json' -d '{"logs":[]}' "$URL/agent/v1/telemetry")
[ "$CODE" = "401" ]
check $? "unauthenticated /agent/v1/telemetry is 401 (got $CODE)"

# 5. The security headers added in Phase 4 survived deployment. Easy to lose to
#    a proxy or a middleware ordering change, and invisible until someone looks.
HEADERS=$(curl -sSI --max-time 20 "$URL/health")
echo "$HEADERS" | grep -qi 'x-content-type-options: nosniff' \
  && echo "$HEADERS" | grep -qi 'content-security-policy'
check $? "security headers present on the response"

echo
if [ "$FAILED" = "0" ]; then
  echo "=== SMOKE RESULT: PASS (5/5) ==="
else
  echo "=== SMOKE RESULT: FAIL ($FAILED of 5) ==="
fi
exit $FAILED
