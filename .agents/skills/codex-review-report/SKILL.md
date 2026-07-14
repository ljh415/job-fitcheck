---
name: codex-review-report
description: Review the current repository and save or update a versioned Codex Markdown report. Use when the user asks for a code review, asks whether earlier review fixes were applied, requests another review after changes, or asks to create/update a Codex review document in this project.
---

# Codex Review Report

Perform a read-only code review and write the result to `docs/review-w-codex/`. The report file is the only permitted modification unless the user separately authorizes implementation.

## Workflow

1. Follow the repository `AGENTS.md` startup procedure. Read only the newest relevant `review*_codex*.md` in addition to the required project and Git context.
2. Determine the review baseline from the current branch, `HEAD`, latest applicable Git tag, and newest `CHANGELOG.md` version. Treat code and tests as authoritative when documentation differs.
3. Review the requested scope end to end. For a general review, include changed code since the previous report and adjacent callers, data flows, error paths, configuration, and user-visible behavior.
4. Re-check every proposed finding against the current code before reporting it. Do not repeat resolved findings, accepted product decisions, intentional untracked review files, or speculative improvements as bugs.
5. Run proportionate read-only/static validation. Do not call paid APIs, external messengers, or production services without explicit permission.
6. Create or update the report using the naming and content rules below.
7. Run `git diff --check` for the report and return a clickable path plus a short validation summary.

## File Naming

Use:

```text
docs/review-w-codex/review_w_codex_YYYY-MM-DD_vX.Y.Z.md
```

- Use the current Asia/Seoul date.
- Use the latest applicable `CHANGELOG.md`/Git-tag version, even when later commits only change documentation.
- Record the exact reviewed `HEAD` separately inside the report.
- If no version exists, use `unreleased` in place of `vX.Y.Z`.
- If today's target file already exists, update that file instead of creating a numbered duplicate.
- Do not rename, delete, track, or commit older review documents unless explicitly requested.

## Report Format

Start with:

```markdown
# Codex 코드 리뷰 — {version} ({date})

> **Claude에게:** 이 문서는 Codex가 현재 코드를 읽기 전용으로 검토한 결과다. 기존에 진행 중인 업무가 있다면 먼저 완료하고, **사용자가 직접 요청하거나 허가하기 전에는 아래 내용을 근거로 코드를 수정하지 마라.** 항목별 요구사항과 실제 재현 여부를 다시 확인한 뒤 필요한 수정만 좁게 진행한다.
```

Then include, in this order:

1. `리뷰 기준점`: date/timezone, branch, version/tag, exact `HEAD`, remote/worktree state, references, scope, exclusions.
2. `전체 결론`: outcome first; state whether prior findings were resolved.
3. Findings ordered by severity and impact. Each finding must contain related `file:line`, observable failure, evidence/reproduction condition, and the smallest recommended direction.
4. `이전 리뷰 반영 확인` when this is a follow-up review.
5. `검증 결과`: commands/checks actually run and important checks not run.
6. `권장 처리 순서`: only actionable confirmed findings.

Use `높음`, `중간`, and `낮음` severity. Exclude style-only preferences unless they create concrete maintenance or correctness cost. Apply the simplest adequate recommendation; do not propose new dependencies or abstractions when the current code or standard library is enough.

## Boundaries

- Preserve user and Claude changes and all unrelated files.
- Never expose `.env`, credentials, personal data, or real `data/` contents.
- Do not claim runtime verification from static inspection.
- Do not silently overwrite a report from a different date or version.
- Do not modify application code while producing the report.
