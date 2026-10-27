#!/usr/bin/env python3
"""Public7.2 entry point; exact GitHub-only APK and Windows inputs."""
from unified_release import main, profile_for

if __name__ == "__main__":
    main(profile_for("7.2.0"))
