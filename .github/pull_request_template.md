## What changed

<!-- The behaviour, not the diff. If it is a bug fix, state the bug first and
     what it did to somebody reading the site. -->

## Why

<!-- What made this worth doing. For an audit finding: how it was found, and
     what it cost while it was there. -->

## How it was verified

<!-- The pipeline has no staging environment, so say what you actually ran.
     `python -m pytest tests -q` is the floor, not the answer. Anything that
     changes a payload should say what the payload looked like before and
     after; anything that changes docs/*.js should say what a browser did. -->

## Issue links — read this before typing a `#number`

GitHub closes an issue on merge if the body contains a closing keyword next to
its number, and **it does not read negations**. `This does not close #2` closed
issue #2, and the issue was then attributed to a PR whose own text says it did
not do the work.

- To close an issue: `Closes #2`.
- To mention one without closing it: write `issue 2`, or link it as
  `https://github.com/Kejjeh/nyc-restaurant-week/issues/2` — never a closing
  keyword anywhere near the number.
- To say a PR deliberately stops short: `Leaves issue 2 open — it still needs
  the Places run.`

## Checklist

- [ ] `python -m pytest tests -q` passes
- [ ] No menu PDF is tracked (`git ls-files | grep -c '\.pdf$'` prints `0`)
- [ ] No API key, secret or personal path is in the diff
- [ ] Payloads re-exported and committed if the pipeline changed
- [ ] Any issue referenced above is left in the state you intend
