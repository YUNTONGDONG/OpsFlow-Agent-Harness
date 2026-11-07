import unittest

from src.harness import (
    IdempotencyRecord,
    IdempotencyStatus,
    RedisIdempotencyStore,
)


class FakeRedis:
    def __init__(self) -> None:
        self.data = {}
        self.expirations = {}

    def set(self, name, value, *, ex=None, nx=False):
        self.data[name] = value
        self.expirations[name] = ex
        return True

    def get(self, name):
        return self.data.get(name)


class RedisIdempotencyStoreTest(unittest.TestCase):
    def test_record_round_trip_and_ttl(self) -> None:
        redis = FakeRedis()
        store = RedisIdempotencyStore(redis, ttl_seconds=600)
        record = IdempotencyRecord(
            key="write-key",
            status=IdempotencyStatus.VERIFIED,
            result={"data": {"result": "created"}},
            external_resource_id="task-123",
        )

        store.save(record)
        loaded = store.get("write-key")

        self.assertEqual(loaded, record)
        self.assertEqual(
            redis.expirations["harness:idempotency:write-key"],
            600,
        )

    def test_missing_record_returns_none(self) -> None:
        self.assertIsNone(RedisIdempotencyStore(FakeRedis()).get("missing"))


if __name__ == "__main__":
    unittest.main()
