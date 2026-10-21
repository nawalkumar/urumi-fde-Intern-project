"""Authenticated store administration routes.

A session may read and change only that user's stores. Responses use the
public store view. Kubernetes objects and credentials are not returned.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from apps.gateway.app.admin_api import _user
from apps.gateway.app.keys import StoreUnavailable
from apps.gateway.app.stores.model import ERRORS, public_store
from apps.gateway.app.stores.repository import list_stores
from apps.gateway.app.stores.service import StoreService

logger = logging.getLogger(__name__)

_STATUS = {
    "created": 202,
    "replay": 200,
    "conflict": 409,
    "limit": 409,
    "invalid": 400,
    "disabled": 503,
    "unavailable": 503,
    "not_found": 404,
    "retry": 409,
    "deleting": 202,
    "deleted": 200,
    "provisioning": 202,
    "ok": 200,
}


def register_store_routes(app: FastAPI, service: StoreService) -> None:
    @app.get("/admin/stores")
    async def list_my_stores(request: Request) -> JSONResponse:
        user = await _user(service.store, request)
        if isinstance(user, JSONResponse):
            return user
        try:
            limit = int(request.query_params.get("limit", "50"))
        except ValueError:
            limit = 50
        try:
            rows = await list_stores(service.store, user.user_id, limit)
        except StoreUnavailable:
            return _error(503, "unavailable")
        return JSONResponse({"stores": [public_store(row) for row in rows]})

    @app.post("/admin/stores")
    async def create_store(request: Request) -> JSONResponse:
        user = await _user(service.store, request)
        if isinstance(user, JSONResponse):
            return user
        body = await _body(request)
        if body is None:
            return _error(400, "invalid")
        name = body.get("name")
        key = body.get("idempotency_key")
        header = request.headers.get("idempotency-key")
        if isinstance(header, str) and header.strip():
            if key is None:
                key = header.strip()
            elif key != header.strip():
                return _error(400, "invalid")
        record, code = await service.create(user.user_id, name, key)
        if record is None or code in {"conflict", "limit", "invalid", "disabled", "unavailable"}:
            return _error(_STATUS.get(code, 400), code)
        return JSONResponse(public_store(record), status_code=_STATUS.get(code, 200))

    @app.get("/admin/stores/{store_id}")
    async def get_store_status(store_id: str, request: Request) -> JSONResponse:
        return await _one(service, store_id, request)

    @app.get("/admin/stores/{store_id}/deletion")
    async def get_deletion(store_id: str, request: Request) -> JSONResponse:
        return await _one(service, store_id, request)

    @app.post("/admin/stores/{store_id}/delete")
    async def delete_store(store_id: str, request: Request) -> JSONResponse:
        user = await _user(service.store, request)
        if isinstance(user, JSONResponse):
            return user
        record, code = await service.request_delete(user.user_id, store_id)
        if record is None:
            return _error(_STATUS.get(code, 404), code)
        return JSONResponse(public_store(record), status_code=_STATUS.get(code, 202))

    @app.post("/admin/stores/{store_id}/retry")
    async def retry_store(store_id: str, request: Request) -> JSONResponse:
        user = await _user(service.store, request)
        if isinstance(user, JSONResponse):
            return user
        record, code = await service.request_retry(user.user_id, store_id)
        if code == "retry":
            return _error(409, "retry")
        if record is None:
            return _error(_STATUS.get(code, 404), code)
        return JSONResponse(public_store(record), status_code=_STATUS.get(code, 202))


async def _one(service: StoreService, store_id: str, request: Request) -> JSONResponse:
    user = await _user(service.store, request)
    if isinstance(user, JSONResponse):
        return user
    record, code = await service.get_owned(user.user_id, store_id)
    if record is None:
        return _error(_STATUS.get(code, 404), code)
    return JSONResponse(public_store(record))


def _error(status: int, code: str) -> JSONResponse:
    description = ERRORS.get(code, ERRORS["invalid"])
    error = "invalid_request"
    if status == 404:
        error = "not_found"
    elif status == 409:
        error = "conflict"
    elif status == 503:
        error = "unavailable"
    return JSONResponse({"error": error, "error_description": description}, status_code=status)


async def _body(request: Request) -> dict | None:
    try:
        body = await request.json()
    except Exception:
        return None
    if not isinstance(body, dict):
        return None
    return body
