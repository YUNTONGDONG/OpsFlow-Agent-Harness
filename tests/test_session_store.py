import unittest

from src.harness import (
    InMemorySessionStore,
    RedisSessionStore,
    SessionMessage,
)


class FakeRedisLists:
    def __init__(self):
        self.data = {}
        self.ttls = {}

    def rpush(self, name, value):
        self.data.setdefault(name, []).append(value)
        return len(self.data[name])

    def lrange(self, name, start, end):
        values = self.data.get(name, [])
        normalized_start = len(values) + start if start < 0 else start
        normalized_start = max(normalized_start, 0)
        normalized_end = len(values) + end if end < 0 else end
        return values[normalized_start : normalized_end + 1]

    def ltrim(self, name, start, end):
        values = self.lrange(name, start, end)
        self.data[name] = values
        return True

    def expire(self, name, seconds):
        self.ttls[name] = seconds
        return True


class SessionStoreTest(unittest.TestCase):
    def test_in_memory_sessions_are_isolated_and_bounded(self):
        store = InMemorySessionStore(max_messages=2)
        store.append("one", SessionMessage(role="user", content="old"))
        store.append("one", SessionMessage(role="assistant", content="middle"))
        store.append("one", SessionMessage(role="user", content="new"))
        store.append("two", SessionMessage(role="user", content="other"))

        self.assertEqual(
            [item.content for item in store.recent("one", 10)],
            ["middle", "new"],
        )
        self.assertEqual(store.recent("two", 10)[0].content, "other")

    def test_redis_session_round_trip_trim_and_ttl(self):
        redis = FakeRedisLists()
        store = RedisSessionStore(
            redis,
            max_messages=2,
            ttl_seconds=300,
        )
        store.append("session", SessionMessage(role="user", content="one"))
        store.append("session", SessionMessage(role="assistant", content="two"))
        store.append("session", SessionMessage(role="user", content="three"))

        self.assertEqual(
            [item.content for item in store.recent("session", 10)],
            ["two", "three"],
        )
        self.assertEqual(redis.ttls["harness:session:session"], 300)


if __name__ == "__main__":
    unittest.main()
