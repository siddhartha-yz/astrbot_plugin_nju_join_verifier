# Changelog

## 0.4.0

- Remove major/department from the verification decision; only name and student ID matter.
- Parse every join application with the configured LLM provider instead of using deterministic parsing first.
- Require strict extractive JSON output containing `name` and `student_id`, then require the external verifier to return `match` before approval.
- Send only the applicant answer text to the LLM; QQ/group IDs remain excluded, while the student ID is now part of the LLM input because the model extracts both identity fields.
- Serialize duplicate observations per request rather than globally, allowing unrelated applications to be parsed concurrently.
- Keep compatibility with existing `llm_fallback_*` deployment config while exposing the new `llm_parser_*` schema names.

## 0.3.2

- Require the verification-service endpoint to be configured privately instead of shipping a public default.

## 0.3.1

- Scope QQ admin runtime controls to configured target groups.
- Leave unrelated group join requests available to other plugins.
- Serialize real-time and reconciliation processing to prevent duplicate approval races.
- Retry compact-answer LLM fallback failures when the provider is temporarily unavailable.
- Keep documentation and verifier user-agent aligned with the reviewed release.

## 0.3.0

- Add privacy-minimized LLM fallback for ambiguous name boundaries.
- Keep student IDs and QQ numbers out of LLM prompts.
- Require the LLM result to be an exact source substring and pass external identity verification.

## 0.2.0

- Add independent runtime enable/disable state and `/njuverify` management commands.
