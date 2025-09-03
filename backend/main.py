from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict, Set, List
import asyncio, json, datetime

from sqlalchemy import create_engine, String, Integer, DateTime, ForeignKey, select, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

# ---------------------- Database setup ----------------------

engine = create_engine("sqlite:///chat.db", echo=False, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    last_seen: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    messages: Mapped[List["Message"]] = relationship(back_populates="user")

class Room(Base):
    __tablename__ = "rooms"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), unique=True, index=True)

    messages: Mapped[List["Message"]] = relationship(back_populates="room")

class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    content: Mapped[str] = mapped_column(String(1000))
    timestamp: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.datetime.now(datetime.timezone.utc))

    room: Mapped[Room] = relationship(back_populates="messages")
    user: Mapped[User] = relationship(back_populates="messages")

def init_db():
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        # Ensure default rooms
        default_rooms = ["General", "Developers", "HR"]
        existing = {r[0] for r in db.execute(select(Room.name)).all()}
        for name in default_rooms:
            if name not in existing:
                db.add(Room(name=name))
        db.commit()

# ---------------------- App setup ----------------------

app = FastAPI(title="Team Chat")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def on_startup():
    init_db()

# ---------------------- Schemas ----------------------

class LoginRequest(BaseModel):
    username: str

class MessageOut(BaseModel):
    username: str
    content: str
    timestamp: str
    room: str

# ---------------------- Connection Manager ----------------------

class ConnectionManager:
    def __init__(self):
        self.room_connections: Dict[str, Set[WebSocket]] = {}
        self.room_online_users: Dict[str, Set[str]] = {}
        self.lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, room: str, username: str):
        await websocket.accept()
        async with self.lock:
            self.room_connections.setdefault(room, set()).add(websocket)
            self.room_online_users.setdefault(room, set()).add(username)
        # Broadcast join + update online list
        await self.broadcast(room, {
            "type": "join",
            "room": room,
            "username": username
        })
        await self.send_online_list(room)

    async def disconnect(self, websocket: WebSocket, room: str, username: str):
        async with self.lock:
            if room in self.room_connections and websocket in self.room_connections[room]:
                self.room_connections[room].remove(websocket)
            if room in self.room_online_users and username in self.room_online_users[room]:
                self.room_online_users[room].remove(username)
        await self.broadcast(room, {
            "type": "leave",
            "room": room,
            "username": username
        })
        await self.send_online_list(room)

    async def broadcast(self, room: str, message: dict):
        # Copy to avoid "set changed size during iteration"
        connections = list(self.room_connections.get(room, []))
        to_remove = []
        for ws in connections:
            try:
                await ws.send_text(json.dumps(message))
            except Exception:
                to_remove.append(ws)
        if to_remove:
            async with self.lock:
                for ws in to_remove:
                    self.room_connections.get(room, set()).discard(ws)

    async def send_online_list(self, room: str):
        users = list(self.room_online_users.get(room, set()))
        await self.broadcast(room, {
            "type": "online_list",
            "room": room,
            "users": users
        })

manager = ConnectionManager()

# ---------------------- REST Endpoints ----------------------

@app.post("/login")
def login(payload: LoginRequest):
    username = payload.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="Username required")

    with SessionLocal() as db:
        user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
        if not user:
            user = User(username=username, last_seen=datetime.datetime.now(datetime.timezone.utc))
            db.add(user)
            db.commit()
            db.refresh(user)
    return {"ok": True, "username": username}

@app.get("/rooms")
def get_rooms():
    with SessionLocal() as db:
        rooms = [r[0] for r in db.execute(select(Room.name).order_by(Room.name)).all()]
    return {"rooms": rooms}

@app.get("/messages")
def get_messages(room: str, limit: int = 50):
    with SessionLocal() as db:
        room_obj = db.execute(select(Room).where(Room.name == room)).scalar_one_or_none()
        if not room_obj:
            raise HTTPException(status_code=404, detail="Room not found")
        rows = db.execute(
            select(Message, User.username)
            .join(User, User.id == Message.user_id)
            .where(Message.room_id == room_obj.id)
            .order_by(Message.timestamp.desc())
            .limit(limit)
        ).all()
        # Return newest-first -> reverse to oldest-first for UI
        msgs = []
        for m, username in reversed(rows):
            msgs.append({
                "username": username,
                "content": m.content,
                "timestamp": m.timestamp.isoformat(),
                "room": room
            })
        return {"messages": msgs}

@app.get("/online")
def get_online(room: str):
    return {"room": room, "users": list(manager.room_online_users.get(room, set()))}

# ---------------------- WebSocket Endpoint ----------------------

@app.websocket("/ws/{room}")
async def websocket_endpoint(websocket: WebSocket, room: str):
    # Expect ?username=XYZ
    username = websocket.query_params.get("username", "").strip()
    if not username:
        await websocket.close(code=1008)
        return

    # Ensure the room exists
    with SessionLocal() as db:
        room_obj = db.execute(select(Room).where(Room.name == room)).scalar_one_or_none()
        if not room_obj:
            # create room on the fly
            room_obj = Room(name=room)
            db.add(room_obj)
            db.commit()
            db.refresh(room_obj)

        # ensure user exists
        user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
        if not user:
            user = User(username=username, last_seen=datetime.datetime.now(datetime.timezone.utc))
            db.add(user)
            db.commit()
            db.refresh(user)

    await manager.connect(websocket, room, username)

    try:
        while True:
            text = await websocket.receive_text()
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                continue

            msg_type = payload.get("type")
            if msg_type == "chat":
                content = (payload.get("content") or "").strip()
                if not content:
                    continue
                # Save to DB
                with SessionLocal() as db:
                    user = db.execute(select(User).where(User.username == username)).scalar_one()
                    room_obj = db.execute(select(Room).where(Room.name == room)).scalar_one()
                    message = Message(room_id=room_obj.id, user_id=user.id, content=content)
                    db.add(message)
                    db.commit()
                    db.refresh(message)

                    timestamp = message.timestamp

                # Broadcast
                await manager.broadcast(room, {
                    "type": "chat",
                    "room": room,
                    "username": username,
                    "content": content,
                    "timestamp": timestamp.isoformat()
                })

            elif msg_type == "typing":
                is_typing = bool(payload.get("isTyping", True))
                await manager.broadcast(room, {
                    "type": "typing",
                    "room": room,
                    "username": username,
                    "isTyping": is_typing
                })

            else:
                # ignore unknown types
                pass

    except WebSocketDisconnect:
        with SessionLocal() as db:
            # update last seen
            user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
            if user:
                user.last_seen = datetime.datetime.now(datetime.timezone.utc)
                db.commit()
        await manager.disconnect(websocket, room, username)
