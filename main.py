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

@app.get("/")
def read_root():
    return FileResponse("index.html")

async def room_tick(room_code: str):
    """Фоновый цикл комнаты: каждую секунду тратит дрова и уменьшает тепло"""
    while room_code in rooms:
        await asyncio.sleep(1)
        if room_code not in rooms:
            break
            
        room = rooms[room_code]
        state = room["state"]
        
        if state["game_over"]:
            break

        # Логика сгорания дров и падения тепла
        if state["wood"] > 0:
            # Если буран, дрова горят быстрее
            wood_burn = 2 if state["weather"] == "Буран" else 1
            state["wood"] = max(0, state["wood"] - wood_burn)
            # Если костер горит, тепло немного восстанавливается (до 100)
            state["warmth"] = min(100, state["warmth"] + 1)
        else:
            # Если дров нет, лагерь замерзает
            cold_speed = 5 if state["weather"] == "Буран" else 2
            state["warmth"] = max(0, state["warmth"] - cold_speed)

        # Проверка на проигрыш
        if state["warmth"] <= 0:
            state["game_over"] = True
            await manager.broadcast_to_room(room_code, {
                "type": "GAME_OVER",
                "state": state,
                "log": "💀 Лагерь полностью замерз... Буря победила."
            })
            break

        # Каждую секунду рассылаем обновленное состояние всем игрокам
        await manager.broadcast_to_room(room_code, {
            "type": "STATE_UPDATE",
            "state": state
        })

class ConnectionManager:
    async def connect(self, room_code: str, player_id: str, websocket: WebSocket):
        await websocket.accept()
        if room_code not in rooms:
            rooms[room_code] = {
                "players": {},
                "state": {
                    "weather": "Ясно", 
                    "wood": 10, 
                    "warmth": 100, 
                    "game_over": False
                },
                "task": None
            }
            # Запускаем фоновый таймер для новой комнаты
            rooms[room_code]["task"] = asyncio.create_task(room_tick(room_code))
            
        rooms[room_code]["players"][player_id] = websocket

    def disconnect(self, room_code: str, player_id: str):
        if room_code in rooms and player_id in rooms[room_code]["players"]:
            del rooms[room_code]["players"][player_id]
            if not rooms[room_code]["players"]:
                if rooms[room_code]["task"]:
                    rooms[room_code]["task"].cancel()
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
            if room_code not in rooms or rooms[room_code]["state"]["game_over"]:
                continue
                
            state = rooms[room_code]["state"]
            
            if data.get("action") == "add_wood":
                state["wood"] += 3
                await manager.broadcast_to_room(room_code, {
                    "type": "STATE_UPDATE", 
                    "state": state, 
                    "log": f"🔥 {player_id} подкинул дров (+3)."
                })
            elif data.get("action") == "change_weather":
                state["weather"] = "Буран" if state["weather"] == "Ясно" else "Ясно"
                status_text = "⚠️ Начался буран!" if state["weather"] == "Буран" else "☀️ Буря утихла."
                await manager.broadcast_to_room(room_code, {
                    "type": "STATE_UPDATE", 
                    "state": state, 
                    "log": status_text
                })
    except WebSocketDisconnect:
        manager.disconnect(room_code, player_id)
