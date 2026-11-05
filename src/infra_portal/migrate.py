"""Database migrations.

    python -m infra_portal.migrate          apply every pending migration
    python -m infra_portal.migrate check    fail if the models and migrations differ

The image runs the first form as a one-shot step before the app starts: the
`migrate` service in compose.yaml, and a Job on Kubernetes. Running it twice is
safe; Alembic records the applied revision in the `alembic_version` table.
"""

import sys

from alembic import command
from alembic.config import Config


def _config() -> Config:
    config = Config()
    config.set_main_option("script_location", "infra_portal:migrations")
    return config


def main(argv: list[str]) -> None:
    if argv[:1] == ["check"]:
        # Raises when a model change has no migration yet.
        command.check(_config())
        print("Models and migrations match.")
        return
    command.upgrade(_config(), "head")
    print("Database is at the latest revision.")


if __name__ == "__main__":
    main(sys.argv[1:])
