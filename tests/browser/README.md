# Browser checks

The suite uses real headless Chromium, a loopback HTTP server, the production
request/config validation, JobRunner, saved history, and trade Parquet queries.
Report rows and job work are deterministic fixtures: these checks verify browser
behavior, while the numerical inference has its own unit and integration tests.
External browser requests are prohibited and cache warming is patched to fail if
it would fetch live market data. No market credentials are needed.

Install development dependencies and the browser, then run:

```sh
python -m pip install -e '.[browser]'
python -m playwright install chromium
python -m pytest tests/browser -q
```

On Linux hosts lacking Chromium's system libraries, use
`python -m playwright install --with-deps chromium`.

The standard `pytest` run includes these tests when Playwright is installed.
If the Python package is absent, this module is explicitly skipped; if the
package is installed but Chromium is missing, the suite fails with Playwright's
installation instruction. Run `pytest -m 'not browser'` to exclude browser tests.

Each test records page errors and console errors. HTTP resource errors are
allowed only when a test explicitly marks the injected status code; unhandled
JavaScript errors always fail. Desktop and mobile screenshots are saved outside
the repository in `/tmp/candlebench-browser-qa/`.

Coverage: configuration submission and interval toggling, running-job button
states and completion, HTTP and background errors with recovery, missing versus
zero values, leaderboard sorting, server-backed sorted trade pagination, saved
run comparison, and responsive interaction. Validation checks cover the submitted
holdout/session/sweep settings, held-out
verdicts, sample-scoped detail requests and pagination, and historical reports
without validation evidence. A nonfinite profit-factor fixture guards JSON
normalization for all-winning runs.

The Browser plugin is not available
in this environment, so these checks use Python's synchronous Playwright API.
