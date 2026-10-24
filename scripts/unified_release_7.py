#!/usr/bin/env python3
"""Public 7.0.0 entry point; shares the audited unified release pipeline."""
from unified_release import main, profile_for

if __name__ == "__main__":
    main(profile_for("7.0.0"))
