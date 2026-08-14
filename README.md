# astrbot_plugin_nju_join_verifier

AstrBot moderation plugin for conservative QQ group join verification.

The plugin only auto-approves. It never auto-rejects. A request is approved only when it is an enabled QQ group join request, the answer can be parsed conservatively, the verification service returns `match`, and runtime auto-approval is enabled.

The verification-service endpoint and credentials are deployment secrets. Configure them privately in AstrBot; this repository intentionally contains no default endpoint or credentials.

Runtime approval state is stored separately in `data/plugin_data/nju_join_verifier/auto_approve.state`, not in the credential-bearing AstrBot config. Owners/admins of configured target groups, or AstrBot admins, can use `/njuverify status`, `/njuverify enable`, and `/njuverify disable`. The legacy `dry_run` config value is used only to bootstrap the initial runtime state.

When deterministic parsing cannot safely identify the name boundary, the plugin can use an AstrBot LLM provider as a fallback. The student ID and QQ number are removed locally first; only the remaining major/department/name fragment is sent to the model. The model may only return an exact name substring from that fragment. The returned name still has to match the locally extracted student ID through the verification service before approval is possible. General AstrBot chat can remain disabled while this direct plugin provider call is used.

Malformed answers, mismatches, unknown IDs, rate limits, LLM failures, login failures, and network errors remain pending for human review or transient retry. The plugin never auto-rejects.

Besides real-time OneBot request events, the plugin periodically calls `get_group_system_msg` so requests that arrived while AstrBot was restarting or disconnected can still be processed.

The audit database stores request identifiers and result codes only. It does not store the applicant name, major, student ID, raw answer, or LLM prompt/output.
