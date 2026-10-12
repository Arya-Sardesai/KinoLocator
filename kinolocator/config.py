"""Configuration read from the environment.

Secrets (API keys) are only ever read from environment variables. Locally, put
them in a .env file (git-ignored) and export it; in GitHub Actions, add them as
repository secrets and map them to env vars in the workflow.
"""

from __future__ import annotations

import os


class MissingConfigError(RuntimeError):
    pass


def require_env(name: str, purpose: str) -> str:
    """Return the env var `name`, or fail with an error saying what needs it."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise MissingConfigError(
            f"Environment variable {name} is not set; it is required for {purpose}. "
            f"Set it in your shell or .env file locally, or as a repository secret "
            f"mapped to {name} in .github/workflows/scrape.yml."
        )
    return value
