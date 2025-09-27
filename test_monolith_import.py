#!/usr/bin/env python3
import sys
import os

# Set environment variables
os.environ["DATABASE_URL"] = "fake"
os.environ["NO_AUTH_MODE"] = "1"

# Simulate the path setup from monolith/main.py
sys.path.append(os.path.dirname(os.path.dirname(__file__)))  # services
sys.path.append(os.path.join(os.path.dirname(__file__), "services", "monolith"))  # services/monolith

try:
    from services.monolith.main import app
    print("✅ Monolith app imported successfully")
except Exception as e:
    print(f"❌ Import failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
