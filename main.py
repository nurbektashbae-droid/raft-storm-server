import asyncio
import random
import string
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

rooms = {}

# Эта функция теперь ПРАВИЛЬНО привязана к главной странице
@app.get("/")
def read_root():
    return FileResponse("index.html")

class ConnectionManager:
    async def connect(self, room_code: str, player_id: str, websocket: WebSocket):
        await websocket.accept()
        if room_code not in rooms:
            rooms[room_code] = {
                "players": {},
                "state": {"weather": "Ясно", "temperature": 20, "wood": 5}
            }
        rooms[room_code]["players"][player_id] = websocket

    def disconnect(self, room_code: str, player_id: str):
        if room_code in rooms and player_id in rooms[room_code]["players"]:
            del rooms[room_code]["players"][player_id]
            if not rooms[room_code]["players"]:
                del rooms[room_code]

    async def broadcast_to_room(self, room_code: str, message: dict):
        if room_code in rooms:
            for player_id, ws in rooms[room_code]["players"].items():
                try:
                    await ws.send_json(message)
                except Exception:
                    pass

manager = ConnectionManager()

@app.get("/create_room")
def create_room():
    code = "".join(random.choices(string.ascii_uppercase, k=4))
    while code in rooms:
        code = "".join(random.choices(string.ascii_uppercase, k=4))
    return {"room_code": code}

@app.websocket("/ws/{room_code}/{player_id}")
async def websocket_endpoint(websocket: WebSocket, room_code: str, player_id: str):
    room_code = room_code.upper()
    await manager.connect(room_code, player_id, websocket)
    await websocket.send_json({"type": "INIT_STATE", "state": rooms[room_code]["state"]})
    await manager.broadcast_to_room(room_code, {"type": "PLAYER_JOINED", "player_id": player_id})

    try:
        while True:
            data = await websocket.receive_json()
            if data.get("action") == "add_wood":
                rooms[room_code]["state"]["wood"] += 1
                await manager.broadcast_to_room(room_code, {
                    "type": "STATE_UPDATE", "state": rooms[room_code]["state"], "log": f"{player_id} подкинул дров."
                })
            elif data.get("action") == "change_weather":
                rooms[room_code]["state"]["weather"] = "Буран"
                await manager.broadcast_to_room(room_code, {
                    "type": "STATE_UPDATE", "state": rooms[room_code]["state"], "log": "⚠️ Начался буран!"
                })
    except WebSocketDisconnect:
        manager.disconnect(room_code, player_id)
