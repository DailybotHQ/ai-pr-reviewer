# Providers — current status and how to add a new one

## Status

| Provider | Status | Default model | Tracking issue |
|---|---|---|---|
| Anthropic | ✅ shipping in v1 | `claude-sonnet-4-6` | n/a |
| OpenAI | 🛠 roadmap (v1.1) | tbd (`gpt-4o`?) | tbd |
| Azure OpenAI | 🛠 roadmap (v1.1) | tbd | tbd |
| Google Gemini | 🛠 roadmap (v1.2) | tbd (`gemini-2.5-pro`?) | tbd |
| AWS Bedrock | 🤔 considering | claude via Bedrock | tbd |
| Self-hosted (vLLM/Ollama) | 🤔 considering | tbd | tbd |

The roadmap is loose and contributor-driven. If you want a provider sooner than the order above suggests, send a PR.

## Why an abstraction at all?

The action is fundamentally a tool-use loop. Every modern instruct-tuned model has a tool-use API, but they disagree on:

- **Message envelope shape** — Anthropic uses `messages` with content blocks of types `text` and `tool_use`; OpenAI uses `messages` with separate `tool_calls` arrays; Gemini uses `contents` with `parts` and `function_call`/`function_response`.
- **System prompt placement** — separate `system` field (Anthropic) vs. role-based (`{"role": "system"}`) message (OpenAI).
- **Caching mechanics** — Anthropic's `cache_control: ephemeral` blocks vs. OpenAI's automatic prompt caching vs. Gemini's explicit caching API.
- **Response shape** — `stop_reason` vs `finish_reason`; tool calls embedded in `content` vs separate `tool_calls`.
- **Streaming and retries** — different status codes, different error envelopes, different rate-limit headers.

Rather than abstract the messaging upward (which would force every code path to handle the lowest common denominator), the `Provider` interface makes each implementation translate **at the boundary**: we keep the in-memory representation in Anthropic's shape (because it's currently the only provider), and a future OpenAI provider would translate Anthropic-shape messages into OpenAI requests on the way out, and OpenAI responses back into Anthropic-shape `content` blocks on the way in.

## What a provider has to satisfy

Implement this interface in `scripts/reviewer.py`:

```python
class Provider:
    def complete(
        self,
        *,
        system_prompt: str,
        messages: list[dict[str, Any]],   # Anthropic shape
        tools: list[dict[str, Any]],      # Anthropic-style input_schema
    ) -> dict[str, Any]:                  # Anthropic-shape response
        ...
```

The return value must look like an Anthropic `Messages.create` response — minimally:

```json
{
  "stop_reason": "tool_use" | "end_turn" | "max_tokens" | ...,
  "content": [
    {"type": "text", "text": "..."},
    {"type": "tool_use", "id": "<unique>", "name": "<tool_name>", "input": {...}}
  ]
}
```

Then register the implementation in `build_provider()` and add a default model in `DEFAULT_MODELS`. That's it.

## Specific gotchas per planned provider

### OpenAI

- OpenAI's `tools` schema accepts `function` items with `parameters` (JSONSchema). Anthropic's `tools` schema accepts top-level items with `input_schema` (also JSONSchema). The translation is mostly trivial; the biggest landmine is **tool_call id matching**: Anthropic's `tool_use_id` is paired with `tool_result.tool_use_id` in the next message; OpenAI's `tool_call_id` is paired with the `tool_call.id` of an `assistant`-role message containing a `tool_calls` array. Different message envelope shapes; same underlying mechanic.
- OpenAI does prompt caching automatically (no header needed) for prompts ≥1024 tokens. Just send the prompt; you get the discount.
- OpenAI response: `choices[0].message` has either `content` (text) or `tool_calls`. Translate to Anthropic-shape `content` blocks before returning.

### Azure OpenAI

- Same protocol as OpenAI but the URL is `https://<resource>.openai.azure.com/openai/deployments/<deployment>/chat/completions?api-version=...`. Add inputs `azure-resource`, `azure-deployment`, `azure-api-version`.

### Google Gemini

- Gemini's tool use uses `functionDeclarations` and the response has `functionCall` parts. The bigger translation: Gemini's `contents` is an array of `{role: "user"|"model", parts: [...]}` rather than message-with-content-blocks. The `model` role replaces `assistant`. Translate at the boundary.
- Gemini's caching is explicit: you create a cached content object via a separate API call and pass its name on subsequent requests. For a 30-turn loop within one review, that's worth it; the implementation should create the cache on first call and reuse the name.

### AWS Bedrock

- Bedrock's Anthropic models use the same Anthropic API shape under `bedrock-runtime` `InvokeModel` / `Converse`. Likely the easiest provider to add; the main work is auth (SigV4) and endpoint routing.

### Self-hosted (vLLM, Ollama, llama.cpp)

- Most expose an OpenAI-compatible chat-completions endpoint. If yours does, the OpenAI provider should work with `api-key: <whatever>` and a custom base URL. Plan to add an `api-base` input alongside the OpenAI provider for this case.

## Testing a new provider

The bar for merging a provider implementation:

1. **Compile-check passes** (`python3 -m py_compile scripts/reviewer.py`).
2. **Manual smoke test on a real PR** — open a PR in a fork or sandbox repo, run the action with `provider: <new>`, paste the resulting tracking comment + review URL in the PR description.
3. **No regressions on existing providers** — run the smoke test on a second PR with `provider: anthropic` to confirm nothing leaked.
4. **`docs/PROVIDERS.md` updated** with the new entry, default model, and any provider-specific inputs.
5. **`CHANGELOG.md` updated** under `[Unreleased]`.

We don't ask for a unit-test framework yet — the testing surface is the integration with the provider's API, which is hard to mock honestly. Smoke tests on real PRs are the bar.

## Cost considerations

The Anthropic provider uses prompt caching aggressively, so a long custom prompt only pays full token cost on the first turn. When adding new providers, replicate this where possible: it cuts the cost of a typical review by ~5x once the cache warms.

---

## Agent Runner Provider Contract (v1.1.0)

Alongside the chat-completions `Provider` above, `scripts/reviewer.py` supports a second provider family: **`AgentRunnerProvider`**. Rather than owning the tool-use loop, this family shells out to a vendor's coding-agent CLI in headless mode and receives structured findings via a file-based contract. This is what powers `provider: claude-code`, `provider: cursor`, and `provider: codex`.

### Why file-based (and not MCP, not fenced-stdout)?

- **Portable across CLIs.** Every vendor CLI can already write files; the schema is our contract, not theirs.
- **Robust to stdout noise.** CLIs emit banners, progress bars, warnings, and streaming JSON that would be brittle to parse.
- **Small blast radius.** A malformed findings file surfaces a clean error to the operator; a broken stdout parser would silently produce empty reviews.
- **Future-proof.** When the ecosystem coalesces on MCP-as-tool-server we'll add it as an additional path; file-based stays as the fallback.

### The file

Every agent-runner provider MUST write its review to:

    <workspace>/.aiprr/findings.json

exactly once, at the end of its run. `parse_findings_file()` in `scripts/reviewer.py` reads and validates it.

### The schema

```json
{
  "summary": "markdown body of the overall review",
  "findings": [
    {
      "path": "src/foo.py",
      "line": 42,
      "body": "markdown body of this inline comment",
      "severity": "critical",
      "start_line": 40,
      "side": "RIGHT"
    }
  ]
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `summary` | string | recommended | Markdown for the top-level review body. Empty string is legal (produces a default fallback summary). |
| `findings` | array | required | May be empty (means "no issues"). |
| `findings[].path` | string | required | Repo-relative file path. Must appear in the PR diff. |
| `findings[].line` | integer | required | Line number (end line for multi-line). Must appear in the diff. |
| `findings[].body` | string | required | Non-empty markdown body of the inline comment. |
| `findings[].severity` | string | optional (default `info`) | Exactly one of `critical`, `warning`, `info` (lowercase). Drives the strictness gate. |
| `findings[].start_line` | integer | optional | Start line for multi-line comments. |
| `findings[].side` | string | optional (default `RIGHT`) | `LEFT` or `RIGHT` (case-normalised). `RIGHT` = new code, `LEFT` = removed. |

### Validation guarantees

`parse_findings_file()` in `scripts/reviewer.py` enforces:

- Root is a JSON object (list/string/number roots rejected).
- `findings` is a list (dict/scalar rejected).
- Every finding is an object with non-empty `path`, integer `line`, non-empty `body`.
- Severity is exactly one of the allowed values (case-insensitive on input, lowercased on output).
- Side is `LEFT`/`RIGHT` (case-insensitive on input, uppercased on output).
- Unknown top-level or per-finding keys are silently ignored (forward-compatibility with vendor extensions).

Missing files raise `FileNotFoundError` with an actionable message. Malformed JSON raises `ValueError` with the offending snippet quoted.

### The prompt directive

CLI providers wrap the review instructions with `write_findings_prompt_directive()`, which appends the schema + "write your findings to this file before ending your turn" instruction to whatever comes from `prompts/default.md`. The directive is standardised so every CLI writes the same schema — one parser, three producers.

### Adding a new agent-runner provider

1. Implement `AgentRunnerProvider` — install check + `run_review()` that invokes the CLI with `write_findings_prompt_directive`-wrapped instructions and returns `parse_findings_file(findings_path)`.
2. Register in `build_provider()`.
3. Add to `DEFAULT_MODELS`.
4. Add a conditional install step in `action.yml` (see the modular-install pattern in Task 07 of the DWP plan).
5. Add a matrix entry in `.github/workflows/self-review.yml` for dogfooding.
