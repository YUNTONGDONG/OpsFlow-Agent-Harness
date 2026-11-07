import os
import unittest

from redis import Redis
from redis.exceptions import RedisError


class RedisConnectionIntegrationTest(unittest.TestCase):
    def test_configured_redis_accepts_ping(self) -> None:
        client = Redis.from_url(
            os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            socket_connect_timeout=1,
            socket_timeout=1,
        )
        try:
            available = client.ping()
        except RedisError as exc:
            self.skipTest(f"Redis is not running: {type(exc).__name__}")

        self.assertTrue(available)


if __name__ == "__main__":
    unittest.main()
