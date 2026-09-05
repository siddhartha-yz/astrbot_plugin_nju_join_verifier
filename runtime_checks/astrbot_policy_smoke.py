from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path
from typing import Any

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def load_main_module():
    package = types.ModuleType("review_plugin")
    package.__path__ = [str(PLUGIN_ROOT)]
    sys.modules["review_plugin"] = package
    spec = importlib.util.spec_from_file_location("review_plugin.main", PLUGIN_ROOT / "main.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load plugin main module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class Message:
    def __init__(self, raw: dict[str, Any]) -> None:
        self.raw_message = raw


class Bot:
    def __init__(self, role: str = "admin") -> None:
        self.role = role
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_action(self, action: str, **params: Any) -> dict[str, Any]:
        self.calls.append((action, params))
        return {"role": self.role}


class Event:
    def __init__(
        self,
        raw: dict[str, Any],
        *,
        astr_admin: bool = False,
        role: str = "admin",
    ) -> None:
        self.message_obj = Message(raw)
        self.bot = Bot(role)
        self._astr_admin = astr_admin
        self.stopped = False

    def is_admin(self) -> bool:
        return self._astr_admin

    def stop_event(self) -> None:
        self.stopped = True


class FakeStore:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def get(self, flag: str):
        return None

    def record(self, **kwargs: Any) -> None:
        self.records.append(kwargs)


class MatchingVerifier:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def verify(self, *, student_id: str, name: str) -> str:
        self.calls.append((student_id, name))
        return "match"


async def check_policy(module) -> None:
    Main = module.Main
    plugin = object.__new__(Main)
    plugin.enabled_groups = frozenset({"target"})

    unrelated = Event(
        {
            "group_id": "other",
            "user_id": "u",
            "post_type": "request",
            "request_type": "group",
            "sub_type": "add",
        }
    )
    await plugin.on_group_join_request(unrelated)
    assert not unrelated.stopped
    assert not unrelated.bot.calls

    unrelated_admin = Event({"group_id": "other", "user_id": "u"}, role="owner")
    assert not await plugin._can_manage(unrelated_admin)
    assert not unrelated_admin.bot.calls

    target_admin = Event({"group_id": "target", "user_id": "u"}, role="admin")
    assert await plugin._can_manage(target_admin)
    assert target_admin.bot.calls[0][0] == "get_group_member_info"

    astr_admin = Event({}, astr_admin=True)
    assert await plugin._can_manage(astr_admin)


async def check_per_request_serialization(module) -> None:
    Main = module.Main
    plugin = object.__new__(Main)
    plugin._request_locks = {}
    plugin._request_lock_users = {}
    plugin._request_locks_guard = asyncio.Lock()
    active_by_flag: dict[str, int] = {}
    max_by_flag: dict[str, int] = {}
    total_active = 0
    max_total_active = 0

    async def fake_locked(self, raw, bot, *, source):
        nonlocal total_active, max_total_active
        flag = str(raw["flag"])
        active_by_flag[flag] = active_by_flag.get(flag, 0) + 1
        max_by_flag[flag] = max(max_by_flag.get(flag, 0), active_by_flag[flag])
        total_active += 1
        max_total_active = max(max_total_active, total_active)
        await asyncio.sleep(0.03)
        active_by_flag[flag] -= 1
        total_active -= 1

    plugin._process_request_locked = types.MethodType(fake_locked, plugin)
    await asyncio.gather(
        plugin._process_request({"flag": "same"}, None, source="event"),
        plugin._process_request({"flag": "same"}, None, source="scan"),
        plugin._process_request({"flag": "other"}, None, source="event"),
    )
    assert max_by_flag["same"] == 1
    assert max_total_active >= 2
    assert plugin._request_locks == {}
    assert plugin._request_lock_users == {}


async def check_missing_verifier_skips_llm(module) -> None:
    Main = module.Main
    plugin = object.__new__(Main)
    plugin.enabled_groups = frozenset({"target"})
    plugin.retry_backoff = 300
    plugin.store = FakeStore()
    plugin.verifier = None
    plugin.llm_parser_enabled = True
    plugin.auto_approve = True
    llm_calls = 0

    async def fake_llm(self, comment: str):
        nonlocal llm_calls
        llm_calls += 1
        return None, "unexpected", False

    plugin._identity_from_llm = types.MethodType(fake_llm, plugin)
    bot = Bot()
    await plugin._process_request_locked(
        {
            "group_id": "target",
            "user_id": "u",
            "flag": "no-verifier",
            "comment": "张三 12345678",
        },
        bot,
        source="event",
    )
    assert llm_calls == 0
    assert plugin.store.records[-1]["outcome"] == "transient"
    assert plugin.store.records[-1]["detail"] == "credentials_missing"
    assert not bot.calls


async def check_every_request_uses_llm(module) -> None:
    Main = module.Main
    plugin = object.__new__(Main)
    plugin.enabled_groups = frozenset({"target"})
    plugin.retry_backoff = 300
    plugin.store = FakeStore()
    verifier = MatchingVerifier()
    plugin.verifier = verifier
    plugin.llm_parser_enabled = True
    plugin.auto_approve = False
    llm_inputs: list[str] = []

    async def fake_llm(self, comment: str):
        llm_inputs.append(comment)
        return module.ParsedIdentity(name="张三", student_id="12345678"), "ok", False

    plugin._identity_from_llm = types.MethodType(fake_llm, plugin)
    bot = Bot()
    await plugin._process_request_locked(
        {
            "group_id": "target",
            "user_id": "u",
            "flag": "simple",
            "comment": "张三 12345678",
        },
        bot,
        source="event",
    )
    assert llm_inputs == ["张三 12345678"]
    assert verifier.calls == [("12345678", "张三")]
    assert plugin.store.records[-1]["outcome"] == "dry_run_match"
    assert plugin.store.records[-1]["detail"] == "format:llm_identity"


async def check_transient_llm_retry(module) -> None:
    Main = module.Main
    plugin = object.__new__(Main)
    plugin.enabled_groups = frozenset({"target"})
    plugin.retry_backoff = 300
    plugin.store = FakeStore()
    plugin.verifier = MatchingVerifier()
    plugin.llm_parser_enabled = True
    plugin.auto_approve = True

    async def fake_llm(self, comment: str):
        return None, "provider_error", True

    plugin._identity_from_llm = types.MethodType(fake_llm, plugin)
    bot = Bot()
    await plugin._process_request_locked(
        {
            "group_id": "target",
            "user_id": "u",
            "flag": "f",
            "comment": "张三 12345678",
        },
        bot,
        source="event",
    )
    record = plugin.store.records[-1]
    assert record["outcome"] == "transient"
    assert record["detail"] == "llm:provider_error"
    assert not bot.calls


async def check_already_handled_is_not_counted_as_bot_approval(module) -> None:
    Main = module.Main
    plugin = object.__new__(Main)
    plugin.enabled_groups = frozenset({"target"})
    plugin.retry_backoff = 300
    plugin.store = FakeStore()
    plugin.verifier = MatchingVerifier()
    plugin.llm_parser_enabled = True
    plugin.auto_approve = True

    async def fake_llm(self, comment: str):
        return module.ParsedIdentity(name="张三", student_id="12345678"), "ok", False

    plugin._identity_from_llm = types.MethodType(fake_llm, plugin)

    class AlreadyHandledBot(Bot):
        async def call_action(self, action: str, **params: Any):
            self.calls.append((action, params))
            if action == "get_group_system_msg":
                return {
                    "join_requests": [
                        {"request_id": "handled", "group_id": "target", "checked": True}
                    ]
                }
            return None

    bot = AlreadyHandledBot()
    await plugin._process_request_locked(
        {
            "group_id": "target",
            "user_id": "u",
            "flag": "handled",
            "comment": "张三 12345678",
        },
        bot,
        source="event",
    )
    assert plugin.store.records[-1]["outcome"] == "already_handled"
    assert plugin.store.records[-1]["detail"] == "matched_but_already_checked"
    assert all(action != "set_group_add_request" for action, _ in bot.calls)


async def check_llm_format_error_retries_once(module) -> None:
    Main = module.Main
    plugin = object.__new__(Main)
    plugin.llm_parser_enabled = True
    plugin.llm_provider_id = "fake"
    plugin.llm_timeout = 5
    plugin.llm_max_tokens = 256

    class Response:
        def __init__(self, text: str) -> None:
            self.completion_text = text

    class Context:
        def __init__(self) -> None:
            self.calls = 0

        async def llm_generate(self, **kwargs: Any):
            self.calls += 1
            if self.calls == 1:
                return Response("姓名是张三，学号是12345678")
            return Response('{"name":"张三","student_id":"12345678"}')

    context = Context()
    plugin._context = context
    identity, detail, transient = await plugin._identity_from_llm("姓名：张三 学号：12345678")
    assert identity == module.ParsedIdentity(name="张三", student_id="12345678")
    assert detail == "ok"
    assert transient is False
    assert context.calls == 2


async def check_scan_isolates_request_failures(module) -> None:
    Main = module.Main
    plugin = object.__new__(Main)
    plugin.enabled_groups = frozenset({"target"})
    plugin.platform_id = "napcat"
    processed: list[str] = []

    class ScanBot:
        async def call_action(self, action: str, **params: Any):
            assert action == "get_group_system_msg"
            return {
                "join_requests": [
                    {
                        "request_id": "first",
                        "group_id": "target",
                        "actor": "",
                        "invitor_uin": "10001",
                        "checked": False,
                        "message": "张三 12345678",
                    },
                    {
                        "request_id": "second",
                        "group_id": "target",
                        "actor": "",
                        "invitor_uin": "10002",
                        "checked": False,
                        "message": "李四 87654321",
                    },
                ]
            }

    class Platform:
        bot = ScanBot()

    class Context:
        def get_platform_inst(self, platform_id: str):
            assert platform_id == "napcat"
            return Platform()

    plugin._context = Context()

    async def fake_process(self, raw, bot, *, source):
        assert source == "scan"
        assert raw["user_id"] in {"10001", "10002"}
        processed.append(raw["flag"])
        if raw["flag"] == "first":
            raise RuntimeError("simulated request failure")

    plugin._process_request = types.MethodType(fake_process, plugin)
    await plugin._scan_pending_requests()
    assert processed == ["first", "second"]


async def main() -> None:
    module = load_main_module()
    await check_policy(module)
    await check_per_request_serialization(module)
    await check_missing_verifier_skips_llm(module)
    await check_every_request_uses_llm(module)
    await check_transient_llm_retry(module)
    await check_already_handled_is_not_counted_as_bot_approval(module)
    await check_llm_format_error_retries_once(module)
    await check_scan_isolates_request_failures(module)
    print("astrbot_policy_smoke=ok")


if __name__ == "__main__":
    asyncio.run(main())
