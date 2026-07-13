from fastapi import FastAPI

from backend.app.api.routes import router
from backend.app.main_version import API_VERSION

app = FastAPI(title="Mnemosyne", version=API_VERSION)
app.include_router(router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
