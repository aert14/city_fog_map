#!/usr/bin/env python3
import sys
import os

# Simulate being in services/monolith/main.py
# __file__ would be services/monolith/main.py
# os.path.dirname(__file__) would be services/monolith
# os.path.dirname(os.path.dirname(__file__)) would be services
fake_monolith_dir = os.path.join(os.path.dirname(__file__), "services", "monolith")
sys.path.append(os.path.dirname(fake_monolith_dir))

try:
    from services.common import database as db
    print("✅ Import successful: common.database imported correctly")
except ImportError as e:
    print(f"❌ Import failed: {e}")
    sys.exit(1)
