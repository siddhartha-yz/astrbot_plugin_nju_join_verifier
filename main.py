from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

from .nju_join_verifier.llm_parser import parse_llm_name, prepare_llm_fallback
from .nju_join_verifier.parser import AnswerParseError, JoinAnswer, parse_join_answer
from .nju_join_verifier.store import ReviewStore
from .nju_join_verifier.verifier import (
    IdentityVerifier,
    VerifierAuthenticationError,
    VerifierError,
)


def _id_set(value: Any) -> frozenset[str]:
    if isinstance(value, str):
        values: Sequence[Any] = value.split(",")
    elif isinstance(value, Sequence):
        values = value
    else:
        values = ()
    return frozenset(text for item in values if (text := str(item).strip()))


def _mask_id(value: Any) -> str:
    text = str(value or "")
    if len(text) <= 4:
        return "****"
    return "*" * min(6, len(text) - 4) + text[-4:]


class GroupJoinRequestFilter(filter.CustomFilter):
    def filter(self, event: AstrMessageEvent, cfg: AstrBotConfig) -> bool:
        raw = getattr(event.message_obj, "raw_message", None)
        return bool(
            isinstance(raw, Mapping)
            and raw.get("post_type") == "request"
            and raw.get("request_type") == "group"
            and raw.get("sub_type") == "add"
        )


class Main(Star):
    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self._context = context
        self.enabled_groups = _id_set(config.get("enabled_groups", []))
        self.platform_id = str(config.get("platform_id", "napcat")).strip() or "napcat"
        bootstrap_dry_run = bool(config.get("dry_run", True))
        self.scan_interval = max(30, int(config.get("scan_interval_seconds", 60)))
        self.retry_backoff = max(30, int(config.get("retry_backoff_seconds", 300)))
        self.llm_fallback_enabled = bool(config.get("llm_fallback_enabled", True))
        self.llm_provider_id = (
            str(config.get("llm_fallback_provider_id", "deepseek/deepseek-v4-flash")).strip()
            or "deepseek/deepseek-v4-flash"
        )
        self.llm_timeout = max(
            3,
            min(60, int(config.get("llm_fallback_timeout_seconds", 20))),
        )
        self.llm_max_tokens = max(
            64,
            min(1024, int(config.get("llm_fallback_max_tokens", 256))),
        )

        base_url = str(config.get("verifier_base_url", "")).strip()
        username = str(config.get("verifier_username", "")).strip()
        password = str(config.get("verifier_password", ""))

        data_dir = Path(get_astrbot_plugin_data_path()) / "nju_join_verifier"
        data_dir.mkdir(parents=True, exist_ok=True)
        self.store = ReviewStore(data_dir / "reviews.sqlite3")
        self._runtime_state_path = data_dir / "auto_approve.state"
        self.auto_approve = self._read_runtime_mode(default=not bootstrap_dry_run)

        self.verifier: IdentityVerifier | None = None
        if base_url and username and password:
            self.verifier = IdentityVerifier(
                base_url=base_url,
                username=username,
                password=password,
                min_interval_seconds=float(config.get("min_verify_interval_seconds", 1.5)),
            )

        self._closed = False
        # Real-time request events and periodic reconciliation can observe the
        # same pending request. Serialize processing so one request cannot be
        # verified/approved twice before its audit state is persisted.
        self._request_lock = asyncio.Lock()
        self._scan_task = asyncio.get_running_loop().create_task(self._scan_loop())
        logger.info(
            "NJU Join Verifier loaded: groups=%d auto_approve=%s credentials=%s scan=%ds llm_fallback=%s",
            len(self.enabled_groups),
            self.auto_approve,
            "configured" if self.verifier else "missing",
            self.scan_interval,
            self.llm_fallback_enabled,
        )

    @filter.command_group("njuverify")
    def njuverify(self) -> None:
        pass

    @njuverify.command("status")
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def status(self, event: AstrMessageEvent) -> None:
        if not await self._can_manage(event):
            event.set_result(
                event.plain_result("权限不足：仅群管理员/群主或 AstrBot 管理员可操作。")
            )
            event.stop_event()
            return
        mode = "已启用" if self.auto_approve else "已停用（仅核验，不自动同意）"
        credentials = "已配置" if self.verifier is not None else "未配置"
        llm = f"已启用（{self.llm_provider_id}）" if self.llm_fallback_enabled else "已停用"
        event.set_result(
            event.plain_result(
                f"NJU Join Verifier：自动审批{mode}；核验凭据{credentials}；"
                f"LLM 兜底{llm}；目标群 {len(self.enabled_groups)} 个。"
            )
        )
        event.stop_event()

    @njuverify.command("enable")
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def enable(self, event: AstrMessageEvent) -> None:
        if not await self._can_manage(event):
            event.set_result(
                event.plain_result("权限不足：仅群管理员/群主或 AstrBot 管理员可操作。")
            )
            event.stop_event()
            return
        self._write_runtime_mode(True)
        self.auto_approve = True
        logger.info("Automatic join approval enabled by an administrator")
        event.set_result(event.plain_result("NJU Join Verifier：自动审批已启用。"))
        event.stop_event()

    @njuverify.command("disable")
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def disable(self, event: AstrMessageEvent) -> None:
        if not await self._can_manage(event):
            event.set_result(
                event.plain_result("权限不足：仅群管理员/群主或 AstrBot 管理员可操作。")
            )
            event.stop_event()
            return
        self._write_runtime_mode(False)
        self.auto_approve = False
        logger.info("Automatic join approval disabled by an administrator")
        event.set_result(event.plain_result("NJU Join Verifier：自动审批已停用；核验仍会继续。"))
        event.stop_event()

    def _read_runtime_mode(self, *, default: bool) -> bool:
        try:
            value = self._runtime_state_path.read_text(encoding="utf-8").strip().lower()
        except FileNotFoundError:
            self._write_runtime_mode(default)
            return default
        except OSError as exc:
            logger.warning("Cannot read runtime mode: %s", type(exc).__name__)
            return default
        if value in {"enabled", "true", "1"}:
            return True
        if value in {"disabled", "false", "0"}:
            return False
        logger.warning("Invalid runtime mode; using bootstrap default")
        return default

    def _write_runtime_mode(self, enabled: bool) -> None:
        tmp = self._runtime_state_path.with_suffix(".state.tmp")
        tmp.write_text("enabled\n" if enabled else "disabled\n", encoding="utf-8")
        tmp.replace(self._runtime_state_path)

    async def _can_manage(self, event: AstrMessageEvent) -> bool:
        if event.is_admin():
            return True
        raw = getattr(event.message_obj, "raw_message", None)
        bot = getattr(event, "bot", None)
        if not isinstance(raw, Mapping) or bot is None:
            return False
        group_id = str(raw.get("group_id") or "").strip()
        user_id = str(raw.get("user_id") or raw.get("sender", {}).get("user_id") or "").strip()
        if not group_id or not user_id or group_id not in self.enabled_groups:
            return False
        try:
            info = await bot.call_action(
                "get_group_member_info",
                group_id=int(group_id) if group_id.isdigit() else group_id,
                user_id=int(user_id) if user_id.isdigit() else user_id,
                no_cache=True,
            )
        except Exception as exc:  # noqa: BLE001 - third-party OneBot boundary
            logger.warning("Cannot verify command administrator role: %s", type(exc).__name__)
            return False
        return isinstance(info, Mapping) and str(info.get("role") or "") in {"owner", "admin"}

    @filter.custom_filter(GroupJoinRequestFilter, priority=20)
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def on_group_join_request(self, event: AstrMessageEvent) -> None:
        raw = getattr(event.message_obj, "raw_message", None)
        bot = getattr(event, "bot", None)
        if not isinstance(raw, Mapping) or bot is None:
            return
        group_id = str(raw.get("group_id") or "").strip()
        if group_id not in self.enabled_groups:
            # Do not consume join requests from unrelated groups; another plugin
            # may legitimately want to handle them.
            return
        await self._process_request(dict(raw), bot, source="event")
        event.stop_event()

    async def _process_request(self, raw: dict[str, Any], bot: Any, *, source: str) -> None:
        async with self._request_lock:
            await self._process_request_locked(raw, bot, source=source)

    async def _process_request_locked(
        self,
        raw: dict[str, Any],
        bot: Any,
        *,
        source: str,
    ) -> None:
        group_id = str(raw.get("group_id") or "").strip()
        user_id = str(raw.get("user_id") or "").strip()
        flag = str(raw.get("flag") or "").strip()
        comment = str(raw.get("comment") or "")
        if not group_id or not user_id or not flag or group_id not in self.enabled_groups:
            return

        previous = self.store.get(flag)
        now = int(time.time())
        if previous is not None:
            if previous.outcome in {"approved", "manual"}:
                return
            if previous.outcome == "transient" and now - previous.updated_at < self.retry_backoff:
                return
            if (
                previous.outcome == "dry_run_match"
                and not self.auto_approve
                and now - previous.updated_at < self.retry_backoff
            ):
                return

        if self.verifier is None:
            self.store.record(
                flag=flag,
                group_id=group_id,
                user_id=user_id,
                outcome="transient",
                detail="credentials_missing",
            )
            logger.warning(
                "Join verification deferred: group=%s user=%s verifier credentials missing",
                group_id,
                _mask_id(user_id),
            )
            return

        try:
            answer = parse_join_answer(comment)
        except AnswerParseError as exc:
            answer, llm_detail, llm_transient = await self._answer_from_llm(comment)
            if answer is None:
                outcome = "transient" if llm_transient else "manual"
                self.store.record(
                    flag=flag,
                    group_id=group_id,
                    user_id=user_id,
                    outcome=outcome,
                    detail=f"format:{exc.code};llm:{llm_detail}",
                )
                if llm_transient:
                    logger.info(
                        "Join verification deferred for LLM retry: group=%s user=%s reason=format:%s llm=%s source=%s",
                        group_id,
                        _mask_id(user_id),
                        exc.code,
                        llm_detail,
                        source,
                    )
                else:
                    logger.info(
                        "Join request left for manual review: group=%s user=%s reason=format:%s llm=%s source=%s",
                        group_id,
                        _mask_id(user_id),
                        exc.code,
                        llm_detail,
                        source,
                    )
                return

        result = "mismatch"
        result_format = answer.format
        tested_names: set[str] = set()
        try:
            for candidate_name in answer.name_candidates:
                tested_names.add(candidate_name)
                result = await self.verifier.verify(
                    student_id=answer.student_id,
                    name=candidate_name,
                )
                if result == "match":
                    break
                # A mismatch can be caused by choosing the wrong name boundary in
                # a compact answer, so try the next candidate. Other results are
                # independent of that boundary and should stop immediately.
                if result != "mismatch":
                    break

            if (
                result == "mismatch"
                and self.llm_fallback_enabled
                and answer.format == "compact_candidates"
            ):
                llm_answer, llm_detail, llm_transient = await self._answer_from_llm(comment)
                if llm_transient:
                    self.store.record(
                        flag=flag,
                        group_id=group_id,
                        user_id=user_id,
                        outcome="transient",
                        detail=f"llm:{llm_detail}",
                    )
                    logger.info(
                        "Join verification deferred for LLM retry: group=%s user=%s reason=%s",
                        group_id,
                        _mask_id(user_id),
                        llm_detail,
                    )
                    return
                if (
                    llm_answer is not None
                    and llm_answer.student_id == answer.student_id
                    and llm_answer.name not in tested_names
                ):
                    result = await self.verifier.verify(
                        student_id=answer.student_id,
                        name=llm_answer.name,
                    )
                    if result == "match":
                        result_format = "llm_fallback"
                        logger.info(
                            "LLM fallback resolved a compact join answer: group=%s user=%s",
                            group_id,
                            _mask_id(user_id),
                        )
                elif llm_answer is None:
                    logger.debug("LLM compact fallback not used: %s", llm_detail)
        except VerifierAuthenticationError as exc:
            self.store.record(
                flag=flag,
                group_id=group_id,
                user_id=user_id,
                outcome="transient",
                detail="auth_error",
            )
            logger.warning(
                "Join verification deferred: group=%s user=%s verifier authentication failed (%s)",
                group_id,
                _mask_id(user_id),
                type(exc).__name__,
            )
            return
        except VerifierError as exc:
            self.store.record(
                flag=flag,
                group_id=group_id,
                user_id=user_id,
                outcome="transient",
                detail="service_error",
            )
            logger.warning(
                "Join verification deferred: group=%s user=%s verifier unavailable (%s)",
                group_id,
                _mask_id(user_id),
                type(exc).__name__,
            )
            return

        if result == "match":
            if not self.auto_approve:
                self.store.record(
                    flag=flag,
                    group_id=group_id,
                    user_id=user_id,
                    outcome="dry_run_match",
                    detail=f"format:{result_format}",
                )
                logger.info(
                    "Join verification matched (dry-run): group=%s user=%s source=%s",
                    group_id,
                    _mask_id(user_id),
                    source,
                )
                return

            params: dict[str, Any] = {"flag": flag, "approve": True}
            self_id = str(raw.get("self_id") or "").strip()
            if self_id.isdigit():
                params["self_id"] = int(self_id)
            try:
                await bot.call_action("set_group_add_request", **params)
            except Exception as exc:  # noqa: BLE001 - third-party OneBot boundary
                self.store.record(
                    flag=flag,
                    group_id=group_id,
                    user_id=user_id,
                    outcome="transient",
                    detail="approve_action_failed",
                )
                logger.warning(
                    "Auto-approve failed: group=%s user=%s error=%s",
                    group_id,
                    _mask_id(user_id),
                    type(exc).__name__,
                )
                return

            self.store.record(
                flag=flag,
                group_id=group_id,
                user_id=user_id,
                outcome="approved",
                detail=f"format:{result_format}",
            )
            logger.info(
                "Join request auto-approved: group=%s user=%s source=%s",
                group_id,
                _mask_id(user_id),
                source,
            )
            return

        if result == "rate_limited":
            self.store.record(
                flag=flag,
                group_id=group_id,
                user_id=user_id,
                outcome="transient",
                detail="rate_limited",
            )
            logger.info(
                "Join verification rate-limited; will retry: group=%s user=%s",
                group_id,
                _mask_id(user_id),
            )
            return

        # Definite non-match or invalid input: never auto-reject. Leave it pending.
        self.store.record(
            flag=flag,
            group_id=group_id,
            user_id=user_id,
            outcome="manual",
            detail=f"verify:{result}",
        )
        logger.info(
            "Join request left for manual review: group=%s user=%s verify_result=%s",
            group_id,
            _mask_id(user_id),
            result,
        )

    async def _answer_from_llm(
        self,
        comment: str,
    ) -> tuple[JoinAnswer | None, str, bool]:
        """Identify only the applicant-name boundary with an AstrBot provider.

        The student ID is extracted locally and never included in the LLM prompt.
        The model output is accepted only when it is an exact substring of the
        remaining non-ID source text.
        """

        if not self.llm_fallback_enabled:
            return None, "disabled", False
        try:
            item = prepare_llm_fallback(comment)
        except AnswerParseError as exc:
            return None, exc.code, False

        system_prompt = (
            "你是一个极严格的中文文本切分器。输入来自大学 QQ 群入群申请，"
            "学号已经在本地删除；剩余文本包含专业、院系或方向和申请人的真实姓名。"
            "只输出姓名在输入中的原样子串，不得改字、补字、解释、输出 JSON 或标点。"
            "无法可靠确定时只输出 UNKNOWN。"
        )
        try:
            response = await asyncio.wait_for(
                self._context.llm_generate(
                    chat_provider_id=self.llm_provider_id,
                    prompt=item.context,
                    system_prompt=system_prompt,
                    temperature=0,
                    max_tokens=self.llm_max_tokens,
                ),
                timeout=self.llm_timeout,
            )
        except Exception as exc:  # noqa: BLE001 - optional provider boundary
            logger.warning("LLM join-answer fallback unavailable: %s", type(exc).__name__)
            return None, "provider_error", True

        try:
            name = parse_llm_name(response.completion_text or "", item.context)
        except AnswerParseError as exc:
            return None, exc.code, False
        return (
            JoinAnswer(
                major=None,
                name_candidates=(name,),
                student_id=item.student_id,
                format="llm_fallback",
            ),
            "ok",
            False,
        )

    async def _scan_loop(self) -> None:
        await asyncio.sleep(15)
        while not self._closed:
            try:
                await self._scan_pending_requests()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - background loop must fail closed
                logger.warning("Pending join-request scan failed: %s", type(exc).__name__)
            await asyncio.sleep(self.scan_interval)

    async def _scan_pending_requests(self) -> None:
        platform = self._context.get_platform_inst(self.platform_id)
        bot = getattr(platform, "bot", None) if platform is not None else None
        if bot is None:
            return
        try:
            data = await bot.call_action("get_group_system_msg")
        except Exception as exc:  # noqa: BLE001 - third-party OneBot boundary
            logger.debug("Cannot scan group system messages yet: %s", type(exc).__name__)
            return
        if not isinstance(data, Mapping):
            return

        requests = data.get("join_requests")
        if not isinstance(requests, list):
            return
        for item in requests:
            if not isinstance(item, Mapping) or bool(item.get("checked")):
                continue
            group_id = str(item.get("group_id") or "").strip()
            if group_id not in self.enabled_groups:
                continue
            raw = {
                "post_type": "request",
                "request_type": "group",
                "sub_type": "add",
                "group_id": group_id,
                "user_id": str(item.get("actor") or ""),
                "comment": str(item.get("message") or ""),
                "flag": str(item.get("request_id") or ""),
            }
            await self._process_request(raw, bot, source="scan")

    async def terminate(self) -> None:
        self._closed = True
        self._scan_task.cancel()
        await asyncio.gather(self._scan_task, return_exceptions=True)
        if self.verifier is not None:
            await self.verifier.close()
        self.store.close()
