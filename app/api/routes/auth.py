from fastapi import APIRouter

router = APIRouter(tags=["auth"])

# 🚧 Este es solo un placeholder, luego podemos armar JWT/usuarios reales
@router.post("/login")
def login(username: str, password: str):
    if username == "admin" and password == "123":
        return {"status": "ok", "token": "fake-jwt-token"}
    return {"status": "error", "msg": "invalid credentials"}
