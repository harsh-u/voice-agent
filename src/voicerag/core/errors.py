"""Shared HTTP error helpers."""
from fastapi import HTTPException


def not_found(resource: str = "Resource") -> HTTPException:
    return HTTPException(status_code=404, detail=f"{resource} not found")


def forbidden(detail: str = "Access denied") -> HTTPException:
    return HTTPException(status_code=403, detail=detail)


def bad_request(detail: str) -> HTTPException:
    return HTTPException(status_code=400, detail=detail)


def conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=409, detail=detail)


def too_large(detail: str = "Payload too large") -> HTTPException:
    return HTTPException(status_code=413, detail=detail)


def rate_limited() -> HTTPException:
    return HTTPException(status_code=429, detail="Rate limit exceeded")
