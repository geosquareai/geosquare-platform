from __future__ import annotations

from fastapi import FastAPI, File, HTTPException, UploadFile

from .importer import ImportRejected, analyse

app = FastAPI(title="GeoSquare Platform", version="0.1.0")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/import/analyse")
async def import_analyse(file: UploadFile = File(...), confirm_digit_gid: bool = False) -> dict:
    """Read a CSV and report its GID column and level. Nothing is stored yet."""
    try:
        return analyse(await file.read(), confirm_digit_gid=confirm_digit_gid).as_dict()
    except ImportRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
