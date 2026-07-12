<!--
Security-focused extension prompt.

Consume via:

    with:
      prompt-extension-file: examples/prompts/security-focused.md

This APPENDS to the base prompt — do NOT use it with `prompt-file:`
(which replaces the base). Pair with `strictness: block-on-warning`
or `block-on-any` for a zero-tolerance security posture. See
docs/PROMPTS.md for the full "base vs extension vs replacement"
decision guide.
-->

## Project-specific severity overrides (Security focus)

Anchor every finding to the OWASP Top-10 category it belongs to. The
base severity rubric applies; the overrides below make the reviewer
significantly more strict on security-adjacent code.

- ALWAYS `critical`:
  - **A01 Broken Access Control** — any handler that reads/writes user
    data without an explicit authorization check (role/tenant/owner).
  - **A02 Cryptographic Failures** — hardcoded secrets, weak KDFs
    (`md5`, `sha1` for password hashing), missing HTTPS enforcement,
    unauthenticated encryption (raw AES without GCM/HMAC).
  - **A03 Injection** — SQL/NoSQL/LDAP/OS-command built via string
    concatenation with user input. Parameterized queries only.
  - **A05 Security Misconfiguration** — `debug=True` in a deploy
    config; permissive CORS (`*`) on an authenticated endpoint;
    `Content-Security-Policy: *`.
  - **A08 Software & Data Integrity** — unpinned CI dependencies on a
    production build path; `pip install` from a URL; unverified
    checksums on downloaded binaries.
  - **A09 Logging & Monitoring** — logging PII or secrets to stdout,
    files, or Sentry.
- ALWAYS `warning`:
  - **A04 Insecure Design** — missing rate limits on
    authentication/reset/registration endpoints.
  - **A07 Identification & Authentication** — session cookies without
    `HttpOnly`, `Secure`, `SameSite`.
  - **A10 SSRF** — outbound HTTP client that accepts user-controlled
    hostnames without an allowlist.

## Reviewer style additions

- Every finding cites its OWASP category (`[A03]`, `[A05]`, …) in the
  first line of the comment body.
- When suggesting a fix, link to the OWASP Cheat Sheet Series entry
  when one applies.
