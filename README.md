# firstpr

[![CI](https://github.com/OWNER/firstpr/actions/workflows/ci.yml/badge.svg)](https://github.com/OWNER/firstpr/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org)
[![Dependencies](https://img.shields.io/badge/dependencies-0-brightgreen)](pyproject.toml)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

A contributor's first pull request is the most expensive review you do. It is also
the one you write by hand, at 1am, for the fortieth time.

`firstpr` writes that reply — using your repository's own rules, not a model's
imagination about what a repository probably wants.

---

## The problem

A first-time contributor cannot know that your project runs `make check` rather
than `pytest`, that a new public function needs an `__all__` entry, or that the
changelog is generated from commit prefixes and must not be edited by hand. So
they find out from you, in prose, one pull request at a time.

A template cannot fix this. `CONTRIBUTING.md` cannot either — it is written for
everyone, so it is too general to be useful at the moment somebody needs it. What
they need is the intersection of *this diff* and *this repository's conventions*,
which is exactly the kind of judgement that does not fit in a checklist.

## What it does

1. Reads what your repository says about itself: `CONTRIBUTING.md`, the pull
   request template, the code of conduct, and the commands actually used in
   `.github/workflows/`. Under a hard character budget, so cost is predictable.
2. Reads the diff.
3. Writes one comment: what they changed, up to three specific things to fix, and
   the commands this repository actually uses.

## What it will not do

These are enforced in the prompt and covered by tests, because a review bot is
only tolerated if its failure modes are boring:

- **It cannot invent rules.** Every statement about how your project works must be
  traceable to the convention excerpts it was given. Unknown means silent.
- **It cannot invent commands.** Commands are copied verbatim from your config. If
  no test command was found, it writes none rather than guessing `pytest`.
- **It will not manufacture feedback.** Zero friction points is an allowed output.
  A small, correct pull request gets one line of acknowledgement.
- **It will not praise-pad.** No "Great work!", no emoji.
- **It will not comment twice.** The comment carries a hidden marker and is edited
  in place on later pushes.
- **It fails closed.** If contributor history cannot be read, it skips rather than
  guess.

## Example

```markdown
### Welcome

You added a `--json` flag to `firstpr review` and wired it into the argument
parser in `src/firstpr/cli.py`.

### Before this merges

- `CONTRIBUTING.md` asks for a test with every behaviour change, and there is no
  test covering the new output branch yet.
- The new flag is documented nowhere in `README.md`; the configuration section
  lists every other flag.

### Running it locally

\`\`\`bash
python -m unittest discover -s tests -v
\`\`\`
```

## Quickstart

Store the API key first:

```bash
gh secret set FIRSTPR_API_KEY
```

Then add `.github/workflows/firstpr.yml`:

```yaml
name: firstpr

on:
  pull_request_target:
    types: [opened, reopened, synchronize]

permissions:
  contents: read
  pull-requests: write

jobs:
  welcome:
    if: >-
      contains(fromJSON('["FIRST_TIME_CONTRIBUTOR","NONE"]'), github.event.pull_request.author_association)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{ github.event.pull_request.base.sha }}
          fetch-depth: 0

      - run: git fetch --no-tags origin ${{ github.event.pull_request.head.sha }}

      - uses: OWNER/firstpr@v0
        with:
          api-key: ${{ secrets.FIRSTPR_API_KEY }}
          base: ${{ github.event.pull_request.base.sha }}
          head: ${{ github.event.pull_request.head.sha }}
```

Two details in there matter and are explained below: `author_association` keeps
the comment off repeat contributors, and the checkout stays on the base commit.

## Why `pull_request_target`, and what it costs

Most first pull requests come from forks, and GitHub does not give fork pull
requests your secrets — so a plain `pull_request` workflow cannot call a model.
`pull_request_target` does have secrets, which is precisely why it is also the
usual way these bots get compromised: the tempting idiom is to check out the
contributor's branch and run your workflow against it.

`firstpr` is built so that this is not necessary. The working tree stays on the
maintainers' commit, so the convention files that reach the model are yours. The
contributor's commit is fetched and passed as `--head`, where it is treated purely
as diff text. Nothing from the pull request is ever executed, and no file from it
is ever read as guidance.

That still leaves prompt injection through the diff itself — a comment in the diff
saying "ignore previous instructions" is just text the model sees. The prompt does
not give the diff authority, and the blast radius is a bad comment on one pull
request. If that is not acceptable to you, run the tool on `pull_request` instead
and restrict it to branches inside your repository.

## Local use

Zero dependencies, so there is nothing to install:

```bash
git clone https://github.com/OWNER/firstpr
cd firstpr

export FIRSTPR_API_KEY=sk-...

# What did it actually find? Costs nothing.
python -m firstpr doctor

# See the exact prompt, still costs nothing.
python -m firstpr review --base origin/main --dry-run

# Write the comment to a file.
python -m firstpr review --base origin/main --out comment.md

# Post it to a pull request.
python -m firstpr review --base origin/main --post --pr 42
```

## Configuration

Flags beat `.firstpr.toml` beat environment variables beat defaults. Run
`firstpr init` to drop a commented template.

| Key | Environment | Default | Meaning |
|---|---|---|---|
| `model` | `FIRSTPR_MODEL` | `gpt-4.1-mini` | Any chat-completions model |
| `base_url` | `FIRSTPR_BASE_URL` | OpenAI | Any OpenAI-compatible endpoint |
| `language` | `FIRSTPR_LANGUAGE` | `auto` | `auto` follows the pull request title |
| `only_first_time` | — | `true` | Skip repeat contributors |

The endpoint is not hardcoded to OpenAI. Anything speaking `/chat/completions`
works — a local model, a proxy, a different vendor. Set `FIRSTPR_BASE_URL` and
`FIRSTPR_MODEL` and it does not know or care.

## Cost

One request per qualifying pull request. A typical prompt is 2–6k input tokens:
conventions are capped at 16k characters, the diff at 20k. `doctor --diff FILE`
prints the estimate before you run anything.

## Design constraints

- **Standard library only.** A tool that runs in someone else's CI must not be able
  to break their lockfile.
- **No network in tests.** HTTP boundaries are patched, never hit.
- **The prompt is code.** Changes to `prompt.py` are reviewed harder than anything
  else, because they change what gets published in somebody else's repository.
- **Three commands.** `review`, `doctor`, `init`. A tool that needs a config schema
  to be useful will not be adopted.

## Development

```bash
python -m unittest discover -s tests -v
```

Contributions welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). The one rule that
matters: no dependencies.

## License

MIT.
