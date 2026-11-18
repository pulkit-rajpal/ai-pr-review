# PR Reviewer

An open-source pull-request reviewer that runs with **any LLM** (including free ones).
Use it from the command line, or install it as a GitHub Action so every PR is reviewed automatically.

## Features

| Feature | CLI | GitHub Action |
|---|---|---|
| High / medium / low **risk label** with reason | ✅ | ✅ |
| Estimated **% of AI-generated code** (heuristic) | ✅ | ✅ |
| Structured JSON output, validated before use | ✅ | ✅ |
| **Walkthrough** table (what changed per file) | ✅ | ✅ |
| **Mermaid sequence diagram** when the PR changes interactions | ✅ | ✅ |
| **Inline line-level comments** on the PR diff | `--post` | ✅ |
| **One-click suggested fixes** (GitHub `suggestion` blocks) | `--post` | ✅ |
| **Auto-review** on PR open / new commits | n/a | ✅ |
| **Incremental reviews**: only new commits after the first review; summary updated in place | `--post` | ✅ |
| **Chat in the PR**: `@pr-reviewer <question>` | `--ask` | ✅ |
| Generate **tests**, **docstrings**, **release notes** | `--task tests` etc. | `@pr-reviewer tests` etc. |
| **Static security checks** (secrets, `eval`, `shell=True`, ...) merged into the review | ✅ | ✅ |
| **Extra context**: full text of changed files + linked `Fixes #123` issues | ✅ | ✅ |
| **Per-repo config** (`.pr-reviewer.yml`): custom rules, path rules, ignores, standing "learnings" | ✅ | ✅ |
| **CI gating**: `--fail-on high` | ✅ | ✅ |
| Works with Gemini, Groq, OpenRouter, Anthropic, OpenAI, Ollama or any OpenAI-compatible API | ✅ | ✅ |

**Not included** (compared with commercial tools): whole-repo code-graph indexing, learning automatically from
👍/👎 feedback (use `learnings:` in the config instead), Jira/Linear, GitLab/Bitbucket/Azure, IDE plugin, dashboards.

## Free API keys

| Provider | Get a key | Key looks like |
|---|---|---|
| Google Gemini (free) | https://aistudio.google.com/apikey | `AIza...` |
| Groq (free) | https://console.groq.com/keys | `gsk_...` |
| OpenRouter (`:free` models) | https://openrouter.ai/keys | `sk-or-...` |
| Anthropic Claude (paid) | https://console.anthropic.com | `sk-ant-...` |
| OpenAI (paid) | https://platform.openai.com | `sk-...` |
| Ollama / LM Studio (local) | no key | `--provider custom` |

The provider is detected from the key automatically.

## Quick start (CLI)

```bash
git clone <this-repo> && cd pr-reviewer
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python pr_reviewer.py https://github.com/OWNER/REPO/pull/123 --api-key YOUR_KEY
```

Public PRs need no GitHub token. By default this only **prints** the review; nothing is posted.

```bash
# post the review (summary + inline comments) to the PR; needs a GitHub token with PR write access
export GITHUB_TOKEN=ghp_...
python pr_reviewer.py OWNER/REPO#123 --api-key YOUR_KEY --post

# ask a question / generate things
python pr_reviewer.py OWNER/REPO#123 --api-key YOUR_KEY --ask "Why is the retry loop needed?"
python pr_reviewer.py OWNER/REPO#123 --api-key YOUR_KEY --task tests
python pr_reviewer.py OWNER/REPO#123 --api-key YOUR_KEY --task notes

# fail a script / CI job on risky PRs
python pr_reviewer.py OWNER/REPO#123 --api-key YOUR_KEY --fail-on high

# local model
python pr_reviewer.py OWNER/REPO#123 --provider custom --base-url http://localhost:11434/v1 --model llama3.1
```

### CLI options

| Flag / env | Meaning |
|---|---|
| `--api-key` / `LLM_API_KEY` | Your LLM key (`GEMINI_API_KEY`, `GROQ_API_KEY`, ... also work) |
| `--provider`, `--model`, `--base-url` | Override auto-detection / default model / custom endpoint |
| `--github-token` / `GITHUB_TOKEN` | Needed for `--post` and private repos |
| `--post` | Post the review to the PR |
| `--no-inline` | With `--post`: summary comment only |
| `--force` | Re-review even if this commit was already reviewed |
| `--task review\|ask\|tests\|notes\|docstrings`, `--ask "..."` | What to do (default: review) |
| `--config PATH` | Use a local config file instead of the repo's |
| `--fail-on medium\|high` | Exit code 1 if risk is at least this level |
| `--json` | Print the raw JSON review |

## Automatic reviews with GitHub Actions

Use it on **any repo**, no server needed:

1. Put this project on GitHub (e.g. `YOUR_USERNAME/pr-reviewer`).
2. In the repo you want reviewed, copy [`examples/pr-review-workflow.yml`](examples/pr-review-workflow.yml) to
   `.github/workflows/pr-review.yml` and replace `YOUR_USERNAME`.
3. Add a repository secret **`LLM_API_KEY`**: Settings -> Secrets and variables -> Actions -> New secret.
4. Open a PR. You get a summary comment and inline comments. Push more commits and only the new changes are reviewed.

This repository already includes a working workflow ([`.github/workflows/pr-review.yml`](.github/workflows/pr-review.yml)),
so just adding the `LLM_API_KEY` secret makes it review its own PRs.

### Chat commands (in a PR comment)

Only repo owners, members and collaborators can trigger these.

- `@pr-reviewer review`: full re-review
- `@pr-reviewer <question>` or `@pr-reviewer ask <question>`
- `@pr-reviewer tests` / `docstrings` / `notes`
- `@pr-reviewer help`

### Action inputs

`api-key` (required), `github-token`, `pr-url`, `comment`, `provider`, `model`, `fail-on`.
Example: `fail-on: high` makes the check fail on high-risk PRs.

**Forks:** GitHub gives PRs from forks a read-only token and no secrets, so automatic reviews are skipped for them.
Maintainers can still run the CLI locally.

## Configuration: `.pr-reviewer.yml`

Put it in the repo root (see [`examples/.pr-reviewer.yml`](examples/.pr-reviewer.yml)). It is read from the PR's
**base branch**, so a PR cannot weaken its own review.

```yaml
instructions: "Flag missing error handling."
learnings: ["We use tabs; don't comment on indentation."]
path_instructions:
  - {path: "src/auth/*", instructions: "Be extremely strict about authz."}
ignore: ["gen/*"]
max_inline_comments: 15
min_severity: low        # lowest severity posted inline
context_files: 3
static_checks: true
```

Lockfiles, minified files, images and `dist/` are ignored by default.

## How it works

1. Fetch the PR diff (or only the new commits since the last review) and parse it with new-file line numbers.
2. Drop ignored files, run the static scanner, fetch extra context (changed files, linked issues).
3. Ask the model for a review in a fixed JSON schema; validate it; drop inline lines that are not in the diff.
4. Merge static findings, raise risk to high if a secret was found, then print and/or post.

The PR text and code are treated as **untrusted data** in the prompt (prompt-injection mitigation), and
reviews are posted with the `COMMENT` event, so the tool never approves or blocks a merge by itself.

## Notes and limits

- Diffs are truncated to fit the model (15k-60k characters by provider); skipped files are listed in the review.
- Free models can return malformed JSON; the tool retries once, then tells you to try again or use a larger model.
- Inline line numbers come from the model and are verified against the diff, but weaker models are less accurate.
- The AI-generated percentage is a heuristic guess from style cues, not a detector.
- Static checks are a lightweight pattern scanner, not a replacement for Semgrep/CodeQL.
- Incremental reviews update the summary to describe only the newest changes; use `@pr-reviewer review` for a full one.

## Tests

```bash
pip install pytest && pytest
```

## License

MIT
