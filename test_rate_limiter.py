#!/usr/bin/env python3
"""
Simple test script for rate limiter functionality
"""
import sys
import os
import asyncio
import tempfile

# Add services to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'services'))

from monolith.cache import check_rate_limit, increment_rate_limit, init_redis_pool, close_redis_pool

async def test_rate_limiter():
    """Test rate limiter functions"""
    print("Testing rate limiter...")

    # Initialize Redis (if available)
    await init_redis_pool()

    user_id = 12345

    try:
        # Test 1: First request should be allowed
        allowed = await check_rate_limit(user_id, limit=3)  # Small limit for testing
        print(f"First request allowed: {allowed}")
        assert allowed == True

        # Increment counter
        await increment_rate_limit(user_id, window_seconds=60)

        # Test 2: Second request should be allowed
        allowed = await check_rate_limit(user_id, limit=3)
        print(f"Second request allowed: {allowed}")
        assert allowed == True

        # Increment counter
        await increment_rate_limit(user_id, window_seconds=60)

        # Test 3: Third request should be allowed
        allowed = await check_rate_limit(user_id, limit=3)
        print(f"Third request allowed: {allowed}")
        assert allowed == True

        # Increment counter
        await increment_rate_limit(user_id, window_seconds=60)

        # Test 4: Fourth request should be denied
        allowed = await check_rate_limit(user_id, limit=3)
        print(f"Fourth request allowed: {allowed}")
        assert allowed == False

        print("✅ All rate limiter tests passed!")

    except Exception as e:
        print(f"❌ Test failed: {e}")
        return False
    finally:
        await close_redis_pool()

    return True

if __name__ == "__main__":
    success = asyncio.run(test_rate_limiter())
    sys.exit(0 if success else 1)
