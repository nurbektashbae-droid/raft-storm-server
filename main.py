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
    """Фоновый цикл: каждую секунду горит костер и тикает время до рассвета"""
    while room_code in rooms:
        await asyncio.sleep(1)
        if room_code not in rooms:
            break
            
        room = rooms[room_code]
        state = room["state"]
        
        if state["game_over"] or state["victory"]:
            break

        # Тикаем таймер рассвета
        if state["time_left"] > 0:
            state["time_left"] -= 1
        else:
            state["victory"] = True
            await manager.broadcast_to_room(room_code, {
                "type": "VICTORY",
                "state": state,
                "log": "☀️ Рассвет! Метель утихла, вы смогли пережить эту ночь! 🎉"
            })
            break

        # Логика сгорания дров и падения тепла
        if state["wood"] > 0:
            wood_burn = 2 if state["weather"] == "Буран" else 1
            state["wood"] = max(0, state["wood"] - wood_burn)
            state["warmth"] = min(100, state["warmth"] + 1)
        else:
            cold_speed = 5 if state["weather"] == "Буран" else 2
            state["warmth"] = max(0, state["warmth"] - cold_speed)

        # Проверка на проигрыш
        if state["warmth"] <= 0:
            state["game_over"] = True
            await manager.broadcast_to_room(room_code, {
                "type": "GAME_OVER",
                "state": state,
                "log": "💀 Лагерь полностью замерз... Буря оказалась сильнее."
            })
            break

        # Рассылка состояния
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
                    "wood": 15, 
                    "warmth": 100, 
                    "time_left": 120,  # Время до рассвета в секундах
                    "game_over": False,
                    "victory": False
                },
                "task": None
            }
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
            if room_code not in rooms or rooms[room_code]["state"]["game_over"] or rooms[room_code]["state"]["victory"]:
                continue
                
            state = rooms[room_code]["state"]
            action = data.get("action")
            
            if action == "add_wood":
                if state["wood"] > 0 or True:  # Если у игрока в инвентаре (пока списываем из общего запаса)
                    # Подкидываем из имеющихся запасов в костер (сейчас просто тратим 1 из запаса, чтобы перевести в тепло)
                    # Но для простоты: кнопка "Подкинуть" теперь просто тратит 1 дерево из инвентаря лагеря, чтобы дать мощный буст теплу
                    pass

            elif action == "burn_one_wood":
                # Игрок перекидывает дерево из кучи в огонь (если куча не пуста)
                if state["wood"] >= 1:
                    state["warmth"] = min(100, state["warmth"] + 5)
                    await manager.broadcast_to_room(room_code, {
                        "type": "STATE_UPDATE",
                        "state": state,
                        "log": f"🔥 {player_id} раздул пламя дровами."
                    })

            elif action == "go_to_forest":
                # Логика вылазки в лес
                is_blizzard = (state["weather"] == "Буран")
                fail_chance = 0.70 if is_blizzard else 0.25
                
                if random.random() > fail_chance:
                    # Успех
                    added_wood = random.randint(4, 7)
                    state["wood"] += added_wood
                    await manager.broadcast_to_room(room_code, {
                        "type": "STATE_UPDATE",
                        "state": state,
                        "log": f"🌲 {player_id} вернулся из леса и принес `+{added_wood}` дров!"
                    })
                else:
                    # Провал
                    damage = 20 if is_blizzard else 10
                    state["warmth"] = max(0, state["warmth"] - damage)
                    await manager.broadcast_to_room(room_code, {
                        "type": "STATE_UPDATE",
                        "state": state,
                        "log": f"🥶 {player_id} заблудился в метели! Лагерь потерял {-damage}% тепла, пока его искали."
                    })

            elif action == "change_weather":
                state["weather"] = "Буран" if state["weather"] == "Ясно" else "Ясно"
                status_text = "⚠️ Налетает ледяной Буран!" if state["weather"] == "Буран" else "☀️ Буря на мгновение утихла."
                await manager.broadcast_to_room(room_code, {
                    "type": "STATE_UPDATE", 
                    "state": state, 
                    "log": status_text
                })
    except WebSocketDisconnect:
        manager.disconnect(room_code, player_id)
