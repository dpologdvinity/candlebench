---
name: verify
description: Run the full test suite and the live input-rejection sweep against the web server. Use before claiming work is complete, before a commit that touches candlebench/web/, or when asked to verify this repo.
---

Two checks. Both must pass; report the actual numbers, not a summary.

## 1. The suite

```bash
python3 -m pytest -q
```

Expect **476 passed** or more. `filterwarnings = ["error::RuntimeWarning"]` means
a numeric warning over an empty selection is a failure, not a warning — if the
count drops, read the failure rather than assuming flake.

## 2. Input rejection against a live server

The server triggers real runs and real network fetches from unauthenticated
requests. Every parameter that reaches a filesystem path or a pandas frame must
be refused before it gets there, and a refusal must be a 400 or 404 — never a
500, never a traceback.

```bash
(python3 -m candlebench serve --port 8899 --no-browser > /tmp/verify-serve.log 2>&1 &)
sleep 3
bad=0; total=0
check() { total=$((total+1)); c=$(curl -s -o /dev/null -w '%{http_code}' "localhost:8899$1");
  [ "$c" = "$2" ] || { echo "MISMATCH want=$2 got=$c  $1"; bad=$((bad+1)); }; }
post() { total=$((total+1)); c=$(curl -s -o /dev/null -w '%{http_code}' -X POST localhost:8899/api/run -d "$1");
  [ "$c" = "400" ] || { echo "MISMATCH POST want=400 got=$c  $1"; bad=$((bad+1)); }; }

for u in "/api/trades?pattern=../../etc/passwd" "/api/trades?symbol=../../../etc/passwd" \
         "/api/trades?sort=../../x" "/api/trades?interval=3h" "/api/trades?limit=nope" \
         "/api/trades?offset=-1" "/api/trades?run=../../x" \
         "/api/breakdown?by=../../x" "/api/breakdown" \
         "/api/equity?pattern=../../x" "/api/equity?interval=1m" \
         "/api/runs?id=../../etc/passwd" "/api/runs?id=.." \
         "/api/session?symbol=../../../etc/passwd&session=2026-09-15&interval=1m&pattern=hammer" \
         "/api/session?symbol=AAPL&session=nope&interval=1m&pattern=hammer"; do check "$u" 400; done
for u in "/api/nope" "/static/../../../etc/passwd" "/api/runs?id=19990101T000000"; do check "$u" 404; done
for u in "/api/meta" "/api/runs" "/api/cache" "/"; do check "$u" 200; done
for b in '{"universe":{"symbols":["../../etc/passwd"]}}' '{"run":{"source":"bloomberg"}}' \
         '{"run":{"intervals":["1s"]}}' '{"run":{"lookback_days":730}}' \
         '{"costs":{"model":"vibes"}}' '{"run":{"windows":0}}' \
         '{"run":{"cache_dir":"/tmp/evil"}}'; do post "$b"; done

echo "checked $total, mismatches $bad"
echo "tracebacks in log: $(grep -ci traceback /tmp/verify-serve.log)"
pkill -f "candlebench serve --port 8899"
```

Expect **29 checked, 0 mismatches, 0 tracebacks**. `pkill` returns non-zero when
it kills the server, which is not a failure.

## Reporting

State both results with their numbers. If either fails, say so with the failing
output — do not report partial success as success. If you added an endpoint or a
parameter, add it to the sweep above in the same change; a new parameter with no
rejection check is an untested trust boundary.
