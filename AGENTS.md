# Project Instructions

## Commit Discipline

- Break implementation work into small, independently verifiable steps.
- After completing and verifying each step, commit it immediately before starting the next step.
- Keep each commit limited to that step. Do not include unrelated pre-existing worktree changes.
- Use a concise commit message that describes the completed outcome.
- If a step cannot be verified or committed, report the blocker instead of silently continuing to later steps.

## Python Environment

- Run Python commands, tests, and dependency checks through the project environment: prefer `uv run ...`; when invoking an interpreter directly, use `.venv/bin/python`.
- Do not use a bare `python` from `PATH` to decide whether project dependencies are available; it may resolve to an unrelated global or Conda environment.
