import os
import unittest
from unittest.mock import patch, MagicMock


def check_production_debug_flags():
    """Extract the logic we added to test it independently"""
    app_env = os.getenv("APP_ENV", "").lower()
    debug_auth_mode = os.getenv("DEBUG_AUTH_MODE", "0") == "1"
    no_auth_mode = os.getenv("NO_AUTH_MODE", "0") == "1"

    if app_env == "production" and (debug_auth_mode or no_auth_mode):
        raise RuntimeError("Cannot start in production environment with DEBUG_AUTH_MODE or NO_AUTH_MODE enabled")


class StartupProtectionTestCase(unittest.TestCase):
    """Test that production environment prevents startup with debug flags"""

    def test_production_with_debug_auth_mode_raises_error(self):
        """Test production env with DEBUG_AUTH_MODE raises RuntimeError"""
        with patch.dict(os.environ, {
            "APP_ENV": "production",
            "DEBUG_AUTH_MODE": "1",
            "NO_AUTH_MODE": "0"
        }):
            with self.assertRaises(RuntimeError) as cm:
                check_production_debug_flags()
            self.assertIn("Cannot start in production", str(cm.exception))

    def test_production_with_no_auth_mode_raises_error(self):
        """Test production env with NO_AUTH_MODE raises RuntimeError"""
        with patch.dict(os.environ, {
            "APP_ENV": "production",
            "DEBUG_AUTH_MODE": "0",
            "NO_AUTH_MODE": "1"
        }):
            with self.assertRaises(RuntimeError) as cm:
                check_production_debug_flags()
            self.assertIn("Cannot start in production", str(cm.exception))

    def test_production_with_both_debug_flags_raises_error(self):
        """Test production env with both debug flags raises RuntimeError"""
        with patch.dict(os.environ, {
            "APP_ENV": "production",
            "DEBUG_AUTH_MODE": "1",
            "NO_AUTH_MODE": "1"
        }):
            with self.assertRaises(RuntimeError) as cm:
                check_production_debug_flags()
            self.assertIn("Cannot start in production", str(cm.exception))

    def test_production_without_debug_flags_works(self):
        """Test that APP_ENV=production without debug flags works normally"""
        with patch.dict(os.environ, {
            "APP_ENV": "production",
            "DEBUG_AUTH_MODE": "0",
            "NO_AUTH_MODE": "0"
        }):
            # This should not raise an exception
            check_production_debug_flags()

    def test_non_production_with_debug_flags_works(self):
        """Test non-production env with debug flags works normally"""
        for env in ["development", "staging", "", None]:
            with self.subTest(env=env):
                env_vars = {
                    "DEBUG_AUTH_MODE": "1",
                    "NO_AUTH_MODE": "1"
                }
                if env is not None:
                    env_vars["APP_ENV"] = env

                with patch.dict(os.environ, env_vars, clear=False):
                    # This should not raise an exception
                    check_production_debug_flags()


if __name__ == "__main__":
    unittest.main()
