# Contributing to firstpr

Thanks for being here. This project is small on purpose, so a few rules keep it
that way.

## Ground rules

- **No dependencies.** `firstpr` installs nowhere if it drags a tree behind it.
  Standard library only. If you think you need a package, open an issue first.
- **No network in tests.** Every test runs offline. HTTP boundaries are patched,
  not hit.
- **The prompt is code.** Changes to `src/firstpr/prompt.py` get reviewed harder
  than changes to anything else, because they change what gets published in
  somebody else's repository.

## Running things

```bash
python -m unittest discover -s tests -v
```

To see what the tool would do without spending anything:

```bash
firstpr doctor
firstpr review --diff some.diff --dry-run
```

## Style

- Python 3.11+, `from __future__ import annotations`, type hints on public
  functions.
- Comments explain *why*, not *what*. If a line needs a comment to say what it
  does, rename something instead.
- Tests are named for the behaviour they protect:
  `test_fails_closed_when_search_is_degraded`, not `test_contributor_count_2`.

## Pull requests

- One change per pull request. Refactors and behaviour changes go separately.
- Every bug fix lands with a test that fails without it.
- If you touched the prompt, paste a before/after of a real generated comment in
  the pull request description. Reviewers cannot evaluate prompt changes from the
  diff alone.
