from os import environ

from fastapi import Request

AUTH_HEADER = environ.get("AUTH_HEADER", "Remote-User")
DEFAULT_USER = environ.get("DEFAULT_USER", "anonymous")


def get_current_user(request: Request) -> str:
    return request.headers.get(AUTH_HEADER, DEFAULT_USER)
