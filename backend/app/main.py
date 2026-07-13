from fastapi import FastAPI

from backend.app.api.routes import router

app = FastAPI(title="Mnemosyne", version="0.1.0")
app.include_router(router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
