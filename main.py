from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "chat.db"

app = FastAPI(title="Speed Chat API")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

# In-memory registry of active WebSocket connections by user id.
active_connections: dict[int, set[WebSocket]] = {}


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_db_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_id INTEGER NOT NULL,
                receiver_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(sender_id) REFERENCES users(id),
                FOREIGN KEY(receiver_id) REFERENCES users(id)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_sender ON messages(sender_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_receiver ON messages(receiver_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_created_at ON messages(created_at)"
        )


@app.on_event("startup")
def on_startup() -> None:
    init_db()


class UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=50)


class MessageCreate(BaseModel):
    sender_id: int
    receiver_id: int
    content: str = Field(min_length=1, max_length=2000)


class UserOut(BaseModel):
    id: int
    username: str
    created_at: str


class MessageOut(BaseModel):
    id: int
    sender_id: int
    receiver_id: int
    content: str
    created_at: str


def row_to_user(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "username": row["username"],
        "created_at": row["created_at"],
    }


def row_to_message(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "sender_id": row["sender_id"],
        "receiver_id": row["receiver_id"],
        "content": row["content"],
        "created_at": row["created_at"],
    }


def ensure_user_exists(user_id: int) -> None:
    with get_db_connection() as conn:
        row = conn.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"User {user_id} not found")


async def push_message_to_receivers(message: dict[str, Any]) -> None:
    payload = json.dumps({"type": "message", "data": message})
    targets = {message["sender_id"], message["receiver_id"]}

    for user_id in targets:
        sockets = active_connections.get(user_id)
        if not sockets:
            continue

        stale_sockets: list[WebSocket] = []
        for socket in list(sockets):
            try:
                await socket.send_text(payload)
            except Exception:
                stale_sockets.append(socket)

        for stale in stale_sockets:
            sockets.discard(stale)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.post("/users/register", response_model=UserOut)
def register_user(payload: UserCreate) -> dict[str, Any]:
    username = payload.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="Username cannot be empty")

    created_at = datetime.now(timezone.utc).isoformat()

    try:
        with get_db_connection() as conn:
            cursor = conn.execute(
                "INSERT INTO users(username, created_at) VALUES(?, ?)",
                (username, created_at),
            )
            user_id = cursor.lastrowid
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            return row_to_user(row)
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="Username already exists")


@app.get("/users", response_model=list[UserOut])
def list_users() -> list[dict[str, Any]]:
    with get_db_connection() as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY username ASC").fetchall()
        return [row_to_user(r) for r in rows]


@app.get("/users/search", response_model=list[UserOut])
def search_users(q: str = Query(min_length=1)) -> list[dict[str, Any]]:
    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM users WHERE username LIKE ? ORDER BY username ASC",
            (f"%{q.strip()}%",),
        ).fetchall()
        return [row_to_user(r) for r in rows]


@app.post("/messages/send", response_model=MessageOut)
async def send_message(payload: MessageCreate) -> dict[str, Any]:
    content = payload.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="Message content cannot be empty")

    if payload.sender_id == payload.receiver_id:
        raise HTTPException(status_code=400, detail="Cannot message yourself")

    ensure_user_exists(payload.sender_id)
    ensure_user_exists(payload.receiver_id)

    created_at = datetime.now(timezone.utc).isoformat()

    with get_db_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO messages(sender_id, receiver_id, content, created_at)
            VALUES(?, ?, ?, ?)
            """,
            (payload.sender_id, payload.receiver_id, content, created_at),
        )
        message_id = cursor.lastrowid
        row = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()

    message = row_to_message(row)
    await push_message_to_receivers(message)
    return message


@app.get("/messages/receive/{user_id}", response_model=list[MessageOut])
def receive_messages(
    user_id: int,
    since_id: Optional[int] = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=500),
) -> list[dict[str, Any]]:
    ensure_user_exists(user_id)

    sql = "SELECT * FROM messages WHERE receiver_id = ?"
    params: list[Any] = [user_id]

    if since_id is not None:
        sql += " AND id > ?"
        params.append(since_id)

    sql += " ORDER BY id ASC LIMIT ?"
    params.append(limit)

    with get_db_connection() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
        return [row_to_message(r) for r in rows]


@app.get("/messages/history", response_model=list[MessageOut])
def chat_history(
    user1_id: int = Query(ge=1),
    user2_id: int = Query(ge=1),
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[dict[str, Any]]:
    ensure_user_exists(user1_id)
    ensure_user_exists(user2_id)

    with get_db_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM messages
            WHERE (sender_id = ? AND receiver_id = ?)
               OR (sender_id = ? AND receiver_id = ?)
            ORDER BY id ASC
            LIMIT ?
            """,
            (user1_id, user2_id, user2_id, user1_id, limit),
        ).fetchall()
        return [row_to_message(r) for r in rows]


@app.get("/messages/search", response_model=list[MessageOut])
def search_messages(
    q: str = Query(min_length=1),
    user_id: Optional[int] = Query(default=None, ge=1),
    other_user_id: Optional[int] = Query(default=None, ge=1),
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM messages WHERE content LIKE ?"
    params: list[Any] = [f"%{q.strip()}%"]

    if user_id is not None and other_user_id is not None:
        ensure_user_exists(user_id)
        ensure_user_exists(other_user_id)
        sql += " AND ((sender_id = ? AND receiver_id = ?) OR (sender_id = ? AND receiver_id = ?))"
        params.extend([user_id, other_user_id, other_user_id, user_id])
    elif user_id is not None:
        ensure_user_exists(user_id)
        sql += " AND (sender_id = ? OR receiver_id = ?)"
        params.extend([user_id, user_id])

    sql += " ORDER BY id ASC LIMIT ?"
    params.append(limit)

    with get_db_connection() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
        return [row_to_message(r) for r in rows]


@app.get("/messages/filter", response_model=list[MessageOut])
def filter_messages(
    user_id: Optional[int] = Query(default=None, ge=1),
    conversation_user1: Optional[int] = Query(default=None, ge=1),
    conversation_user2: Optional[int] = Query(default=None, ge=1),
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[dict[str, Any]]:
    if conversation_user1 is not None and conversation_user2 is not None:
        return chat_history(conversation_user1, conversation_user2, limit)

    if user_id is None:
        raise HTTPException(
            status_code=400,
            detail="Provide user_id or both conversation_user1 and conversation_user2",
        )

    ensure_user_exists(user_id)
    with get_db_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM messages
            WHERE sender_id = ? OR receiver_id = ?
            ORDER BY id ASC
            LIMIT ?
            """,
            (user_id, user_id, limit),
        ).fetchall()
        return [row_to_message(r) for r in rows]


@app.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: int) -> None:
    await websocket.accept()

    if user_id not in active_connections:
        active_connections[user_id] = set()
    active_connections[user_id].add(websocket)

    try:
        while True:
            # Keep connection alive and allow future client-side ping messages.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        user_sockets = active_connections.get(user_id)
        if user_sockets is not None:
            user_sockets.discard(websocket)
            if not user_sockets:
                del active_connections[user_id]


app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def read_index():
    return FileResponse("static/index.html")
    
