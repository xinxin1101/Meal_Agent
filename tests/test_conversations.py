from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from mealpilot.conversation.store import ConversationConflict, ConversationStore
from mealpilot.domain.models import ChatResponse
from mealpilot.main import app, conversation_store
from mealpilot.main import meal_plan_history_store
from tests.test_meal_plan_history import history_plan
from tests.auth_helpers import authenticated_client


def test_conversation_store_persists_exchanges_and_enforces_occ(tmp_path: Path) -> None:
    path = tmp_path / "conversations.sqlite3"
    store = ConversationStore(path)
    created = store.create("user-a", None, "create-key")
    assert created.summary.title == "新对话"
    assert created.summary.conversation_version == 0

    answer = ChatResponse(reply="已记录。", response_source="deterministic_fallback")
    response = store.append_exchange(
        "user-a",
        created.summary.conversation_id,
        0,
        "请结合计划回答",
        answer,
        {"message": "请结合计划回答"},
        "message-key",
    )
    assert response.conversation_version == 1

    reopened = ConversationStore(path).get("user-a", created.summary.conversation_id)
    assert [message.role for message in reopened.messages] == ["user", "assistant"]
    assert reopened.messages[1].response_source == "deterministic_fallback"
    assert reopened.summary.title == "请结合计划回答"

    replay = store.append_exchange(
        "user-a", created.summary.conversation_id, 0, "请结合计划回答", answer,
        {"message": "请结合计划回答"}, "message-key",
    )
    assert replay == response
    try:
        store.append_exchange(
            "user-a", created.summary.conversation_id, 0, "另一条消息", answer,
            {"message": "另一条消息"}, "other-key",
        )
    except ConversationConflict:
        pass
    else:
        raise AssertionError("stale conversation version must be rejected")


def test_conversation_store_delete_is_versioned_idempotent_and_removes_messages(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path / "delete-conversation.sqlite3")
    created = store.create("user-delete", "to delete", "create-delete")
    store.delete("user-delete", created.summary.conversation_id, 0, "delete-key")
    store.delete("user-delete", created.summary.conversation_id, 0, "delete-key")
    assert store.list("user-delete") == []
    try:
        store.get("user-delete", created.summary.conversation_id)
    except KeyError:
        pass
    else:
        raise AssertionError("deleted conversation must not be readable")


def test_conversation_api_restores_messages_and_selected_evidence(monkeypatch: object) -> None:
    monkeypatch.setattr("mealpilot.memory.chat.load_siliconflow_settings", lambda: type("Settings", (), {"configured": False, "multi_agent_enabled": False})())
    client, user_id = authenticated_client("conversation")
    unique = uuid4().hex

    created = client.post(
        f"/v1/users/{user_id}/conversations",
        json={},
        headers={"Idempotency-Key": f"create-{unique}"},
    )
    assert created.status_code == 201
    conversation_id = created.json()["summary"]["conversation_id"]

    response = client.post(
        "/v1/chat",
        json={
            "user_id": user_id,
            "message": "记住这条会话上下文",
            "conversation_id": conversation_id,
            "expected_conversation_version": 0,
            "use_current_plan": False,
            "use_history": True,
            "history_plan_ids": [],
            "use_preferences": False,
        },
        headers={"Idempotency-Key": f"message-{unique}"},
    )
    assert response.status_code == 200
    assert response.json()["conversation_version"] == 1
    assert response.json()["history_plans_used"] == []
    replay = client.post(
        "/v1/chat",
        json={
            "user_id": user_id,
            "message": "记住这条会话上下文",
            "conversation_id": conversation_id,
            "expected_conversation_version": 0,
            "use_current_plan": False,
            "use_history": True,
            "history_plan_ids": [],
            "use_preferences": False,
        },
        headers={"Idempotency-Key": f"message-{unique}"},
    )
    assert replay.status_code == 200
    assert replay.json()["assistant_message_id"] == response.json()["assistant_message_id"]

    restored = client.get(f"/v1/users/{user_id}/conversations/{conversation_id}")
    assert restored.status_code == 200
    assert [message["role"] for message in restored.json()["messages"]] == ["user", "assistant"]
    assert restored.json()["messages"][1]["memories_used"] == []
    assert client.get(f"/v1/users/{user_id}/conversations").json()[0]["conversation_id"] == conversation_id
    assert client.get(f"/v1/users/not-{user_id}/conversations/{conversation_id}").status_code == 403


def test_conversation_api_requires_owner_confirmation_version_and_deletes() -> None:
    client, user_id = authenticated_client("conversation-delete")
    created = client.post(f"/v1/users/{user_id}/conversations", json={"title": "delete me"}, headers={"Idempotency-Key": f"create-{uuid4().hex}"})
    conversation_id = created.json()["summary"]["conversation_id"]
    missing_key = client.delete(f"/v1/users/{user_id}/conversations/{conversation_id}?expected_version=0")
    assert missing_key.status_code == 422
    deleted = client.delete(f"/v1/users/{user_id}/conversations/{conversation_id}?expected_version=0", headers={"Idempotency-Key": f"delete-{uuid4().hex}"})
    assert deleted.status_code == 204
    assert client.get(f"/v1/users/{user_id}/conversations/{conversation_id}").status_code == 404


def test_persistent_chat_rejects_missing_idempotency_and_stale_version() -> None:
    client, user_id = authenticated_client("conversation-conflict")
    unique = uuid4().hex
    created = conversation_store.create(user_id, None, f"direct-{unique}")
    conversation_id = created.summary.conversation_id
    request = {
        "user_id": user_id,
        "message": "测试",
        "conversation_id": conversation_id,
        "expected_conversation_version": 0,
        "use_history": False,
        "use_preferences": False,
    }
    assert client.post("/v1/chat", json=request).status_code == 422
    first = client.post("/v1/chat", json=request, headers={"Idempotency-Key": f"first-{unique}"})
    assert first.status_code == 200
    stale = client.post("/v1/chat", json=request, headers={"Idempotency-Key": f"stale-{unique}"})
    assert stale.status_code == 409


def test_chat_uses_only_the_exact_selected_history_plan(monkeypatch: object) -> None:
    monkeypatch.setattr("mealpilot.memory.chat.load_siliconflow_settings", lambda: type("Settings", (), {"configured": False, "multi_agent_enabled": False})())
    client, user_id = authenticated_client("conversation-selection")
    unique = uuid4().hex
    first = history_plan(user_id=user_id, suffix=f"first-{unique}")
    second = history_plan(user_id=user_id, suffix=f"second-{unique}")
    meal_plan_history_store.adopt(first, f"adopt-first-{unique}")
    meal_plan_history_store.adopt(second, f"adopt-second-{unique}")

    response = client.post("/v1/chat", json={
        "user_id": user_id,
        "message": "只参考我明确选择的这一份历史",
        "use_history": True,
        "history_plan_ids": [first.history_id],
        "use_preferences": False,
    })
    assert response.status_code == 200
    assert [item["history_id"] for item in response.json()["history_plans_used"]] == [first.history_id]
