# astrbot_plugin_nju_join_verifier

AstrBot moderation plugin for conservative QQ group join verification.

The plugin only auto-approves. It never auto-rejects. Every enabled-group join application is parsed by a configured AstrBot LLM provider into exactly two identity fields: the applicant's Chinese name and student ID. Major, department, school and direction are ignored. The extracted pair must still receive an explicit `match` from the external identity-verification service before automatic approval is possible.

Only the applicant's answer text is sent to the LLM. QQ user IDs and group IDs are not included in the prompt. Because the LLM now extracts the student ID as well as the name, the student ID is included in that answer text. LLM output is not trusted by itself: both returned fields must be extractive from the source answer after conservative normalization, and external identity verification remains authoritative.

The verification-service endpoint and credentials are deployment secrets. Configure them privately in AstrBot; this repository intentionally contains no default endpoint or credentials.

Runtime approval state is stored separately in `data/plugin_data/nju_join_verifier/auto_approve.state`, not in the credential-bearing AstrBot config. Owners/admins of configured target groups, or AstrBot admins, can use `/njuverify status`, `/njuverify enable`, and `/njuverify disable`. The legacy `dry_run` config value is used only to bootstrap the initial runtime state.

Malformed or non-extractive LLM output is retried once with a stricter correction prompt; persistent ambiguity, mismatches, unknown IDs, rate limits, provider failures, login failures, and network errors remain pending for human review or transient retry. Definite verifier failures can optionally trigger an idempotent private QQ notification to a configured administrator, containing only the applicant QQ, group ID, and failure type. The plugin never auto-rejects.

Besides real-time OneBot request events, the plugin periodically calls `get_group_system_msg` so requests that arrived while AstrBot was restarting or disconnected can still be processed. Duplicate observations of the same request are serialized, while unrelated applications may be parsed concurrently. Immediately before automatic approval, the plugin rechecks the QQ system request state; if another administrator has already handled the request, it records `already_handled` instead of claiming a bot approval.

The audit database stores request identifiers and result codes only. It does not store the applicant name, student ID, raw answer, or LLM prompt/output.
