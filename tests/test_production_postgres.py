import os
import time
from uuid import uuid4

import pytest

from mealpilot.domain.models import PreferenceMemory, PreferenceMemoryItem
from mealpilot.production.documents import PostgresConversationStore
from mealpilot.production.postgres import PostgresPreferenceMemoryStore, PostgresRunStore
from mealpilot.production.queue import OutboxPublisher, PostgresRunQueue


DATABASE_URL = os.getenv("DATABASE_URL")


@pytest.mark.skipif(not DATABASE_URL, reason="PostgreSQL integration test requires DATABASE_URL")
def test_postgres_occ_outbox_queue_recovery_and_user_isolation() -> None:
    suffix = uuid4().hex
    run_id = f"production-test-{suffix}"
    run_store = PostgresRunStore(DATABASE_URL)
    created, is_new = run_store.create(run_id, {"run_id": run_id}, f"create-{suffix}")
    assert is_new and created.run_version == 0
    assert run_store.transition(run_id, 99, "RUNNING", event_type="started") is None
    running = run_store.transition(run_id, 0, "RUNNING", event_type="started")
    assert running is not None and running.run_version == 1
    assert [event["event_type"] for event in run_store.events_after(run_id, 0)] == ["queued", "started"]

    queue = PostgresRunQueue(DATABASE_URL)
    queue.enqueue(run_id)
    first = queue.claim("worker-a", lease_seconds=1)
    assert first and first["run_id"] == run_id
    time.sleep(1.05)
    recovered = queue.claim("worker-b", lease_seconds=10)
    assert recovered and recovered["job_id"] == first["job_id"] and recovered["attempts"] == 2
    queue.complete(recovered["job_id"], "worker-b")

    memory = PostgresPreferenceMemoryStore(DATABASE_URL)
    owner, other = f"owner-{suffix}", f"other-{suffix}"
    memory.replace(PreferenceMemory(user_id=owner, items=[PreferenceMemoryItem(category="food_preference", value="清淡")]))
    assert [item.value for item in memory.get(owner).items] == ["清淡"]
    assert memory.get(other).items == []

    conversations = PostgresConversationStore(DATABASE_URL)
    conversation = conversations.create(owner, "隔离测试", f"conversation-{suffix}")
    with pytest.raises(KeyError):
        conversations.get(other, conversation.summary.conversation_id)

    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        assert OutboxPublisher(DATABASE_URL, redis_url).publish_batch() >= 2
