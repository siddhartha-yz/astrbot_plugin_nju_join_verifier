# Changelog

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
