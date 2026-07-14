# Self-review skip test (temporary)

Throwaway PR to verify that the `self-review` dogfood reports as **Skipped**
(grey), not a green **Success**, when the `ready` label is absent.

Expected on this PR (no `ready` label):
- `Decide self-review scope` job → runs, prints the "add the 'ready' label" notice.
- `Self-review — *` legs → **Skipped** (matrix is empty; no green no-op).

Delete this file / close the PR after verifying.
