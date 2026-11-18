# PR Reviewer

A small CLI that reviews a GitHub pull request with an LLM and prints:

- **Risk label**: `high` / `medium` / `low`, with a reason
- **Estimated % of AI-generated code** (a heuristic guess, not a detector)
- **Per-file issues** with suggested fixes

The model is forced to answer in a fixed JSON schema, and the result is validated
before printing, so parsing is deterministic.

Works with **any LLM**: give it an API key and it auto-detects the provider from the key.
Several providers have free tiers, so it can cost nothing.

| Provider | Free key | Key looks like |
|---|---|---|
| Google Gemini | https://aistudio.google.com/apikey | `AIza...` |
| Groq | https://console.groq.com/keys | `gsk_...` |
| OpenRouter (`:free` models) | https://openrouter.ai/keys | `sk-or-...` |
| Anthropic Claude (paid) | https://console.anthropic.com | `sk-ant-...` |
| OpenAI (paid) | https://platform.openai.com | `sk-...` |
| Ollama / LM Studio / any OpenAI-compatible API | local, no key | `--provider custom` |

## Setup

```bash
git clone <your-repo-url> && cd pr-reviewer
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Use

```bash
python pr_reviewer.py https://github.com/OWNER/REPO/pull/123 --api-key YOUR_KEY
# or
export LLM_API_KEY=YOUR_KEY
python pr_reviewer.py https://github.com/OWNER/REPO/pull/123
```

Options:

| Flag / env | Meaning |
|---|---|
| `--api-key` / `LLM_API_KEY` | Your LLM key (`GEMINI_API_KEY`, `GROQ_API_KEY`, etc. also work) |
| `--provider` | Override auto-detection: `gemini`, `groq`, `openrouter`, `openai`, `anthropic`, `custom` |
| `--model` | Use a different model than the provider default |
| `--base-url` | Endpoint for `--provider custom` (e.g. `http://localhost:11434/v1` for Ollama) |
| `--json` | Print raw JSON instead of formatted output |
| `GITHUB_TOKEN` | Optional. Needed for private repos, and raises GitHub rate limits |

Local Ollama example:

```bash
python pr_reviewer.py <PR_URL> --provider custom --base-url http://localhost:11434/v1 --model llama3.1
```

Public PRs need no GitHub token.

## Example output

```
PR: https://github.com/OWNER/REPO/pull/123
Risk: MEDIUM - Changes auth middleware without tests
AI-generated code (estimate): ~35% - Uniform docstrings and generic comments

Summary:
  Adds token refresh to the auth middleware.

Issues (1):
  [MEDIUM] auth/middleware.py
    Refresh failure silently falls through as authenticated.
    Fix: Return 401 when refresh raises.
```

## Tests

```bash
pip install pytest && pytest
```

## Notes

- Large diffs are truncated (15k-60k characters depending on provider) to fit free-tier limits.
- Small free models may occasionally return malformed JSON; the tool says so, and a retry or a larger `--model` fixes it.
- The AI-generated percentage is a model's guess from style cues. Treat it as a hint.
- Never commit your `.env` file (it is in `.gitignore`).

## License

MIT
