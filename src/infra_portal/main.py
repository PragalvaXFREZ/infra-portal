"""HTTP entry point."""

from fastapi import FastAPI, Response, status

from infra_portal.db import database_is_reachable

app = FastAPI(title="infra-portal")


@app.get("/livez")
def livez() -> dict[str, str]:
    """The process is up and can answer HTTP.

    This check must not touch the database. If it did, a database outage would
    make every API container look dead, and the orchestrator would restart them
    all without fixing anything.
    """
    return {"status": "ok"}


@app.get("/readyz")
def readyz(response: Response) -> dict[str, str]:
    """The process can do useful work, which today means reaching Postgres.

    A failing readiness check takes the container out of traffic without
    restarting it.
    """
    if database_is_reachable():
        return {"status": "ok", "database": "reachable"}
    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "unavailable", "database": "unreachable"}
