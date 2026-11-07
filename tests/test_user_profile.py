import unittest

from src.harness import (
    InMemoryUserProfileStore,
    RedisUserProfileStore,
    UserProfile,
)


class FakeRedis:
    def __init__(self):
        self.data = {}
        self.expirations = {}

    def set(self, name, value, *, ex=None):
        self.data[name] = value
        self.expirations[name] = ex
        return True

    def get(self, name):
        return self.data.get(name)


class UserProfileStoreTest(unittest.TestCase):
    def test_in_memory_profiles_are_isolated(self) -> None:
        store = InMemoryUserProfileStore()
        store.upsert(UserProfile(user_id="one", time_zone="America/New_York"))
        store.upsert(UserProfile(user_id="two", output_language="en-US"))

        self.assertEqual(store.get("one").time_zone, "America/New_York")
        self.assertEqual(store.get("two").output_language, "en-US")
        self.assertIsNone(store.get("missing"))

    def test_redis_profile_round_trip_and_ttl(self) -> None:
        redis = FakeRedis()
        store = RedisUserProfileStore(redis, ttl_seconds=300)
        profile = UserProfile(
            user_id="user-1",
            time_zone="America/New_York",
            default_calendar="primary",
        )

        store.upsert(profile)
        loaded = store.get("user-1")

        self.assertEqual(loaded.time_zone, "America/New_York")
        self.assertEqual(loaded.default_calendar, "primary")
        self.assertEqual(redis.expirations["harness:user:user-1"], 300)


if __name__ == "__main__":
    unittest.main()
