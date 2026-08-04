from fastapi import HTTPException, status


class NotFound(HTTPException):
    def __init__(self, entity: str, ident: object) -> None:
        super().__init__(status.HTTP_404_NOT_FOUND, f"{entity} {ident!r} not found")


class BadRequest(HTTPException):
    def __init__(self, detail: str) -> None:
        super().__init__(status.HTTP_400_BAD_REQUEST, detail)
