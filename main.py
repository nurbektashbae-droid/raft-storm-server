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
    """Фоновый цикл: каждую секунду горит костер, тикает время и происходят ивенты"""
    event_timer = 0
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
                "type": "VICTORY", "state": state,
                "log": "☀️ Рассвет! Метель утихла, вы смогли пережить эту ночь! 🎉"
            })
            break

        # Логика случайных событий (раз в 25 секунд)
        event_timer += 1
        if event_timer >= 25:
            event_timer = 0
            event_type = random.choice(["blessing", "curse", "none"])
            if event_type == "blessing":
                bonus = random.randint(5, 10)
                state["wood"] += bonus
                await manager.broadcast_to_room(room_code, {
                    "type": "STATE_UPDATE", "state": state,
                    "log": f"📦 Событие: Ветер принёс сухие ветки к лагерю! (+{bonus} дров)"
                })
            elif event_type == "curse":
                state["warmth"] = max(0, state["warmth"] - 15)
                await manager.broadcast_to_room(room_code, {
                    "type": "STATE_UPDATE", "state": state,
                    "log": "💨 Событие: Ледяной порыв ветра пронзает до костей! (-15% тепла)"
                })

        # Сгорание дров
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
                "type": "GAME_OVER", "state": state,
                "log": "💀 Лагерь полностью замерз... Буря оказалась сильнее."
            })
            break

        await manager.broadcast_to_room(room_code, {"type": "STATE_UPDATE", "state": state})

class ConnectionManager:
    async def connect(self, room_code: str, player_id: str, websocket: WebSocket):
        await websocket.accept()
        if room_code not in rooms:
            rooms[room_code] = {
                "players": {},
                "player_roles": {},
                "state": {
                    "weather": "Ясно", "wood": 15, "warmth": 100, 
                    "time_left": 120, "game_over": False, "victory": False
                },
                "task": None
            }
            rooms[room_code]["task"] = asyncio.create_task(room_tick(room_code))
            
        rooms[room_code]["players"][player_id] = websocket

    def disconnect(self, room_code: str, player_id: str):
        if room_code in rooms:
            if player_id in rooms[room_code]["players"]:
                del rooms[room_code]["players"][player_id]
            if player_id in rooms[room_code]["player_roles"]:
                del rooms[room_code]["player_roles"][player_id]
            if not rooms[room_code]["players"]:
                if rooms[room_code]["task"]:
                    rooms[room_code]["task"].cancel()
                del rooms[room_code]

    async def broadcast_to_room(self, room_code: str, message: dict):
        if room_code in rooms:
            for pid, ws in rooms[room_code]["players"].items():
                try: await ws.send_json(message)
                except: pass

manager = ConnectionManager()

@app.get("/create_room")
def create_room():
    code = "".join(random.choices(string.ascii_uppercase, k=4))
    while code in rooms:
        code = "".join(random.choices(string.ascii_uppercase, k=4))
    return {"room_code": code}

@app.websocket("/ws/{room_code}/{player_id}/{role}")
async def websocket_endpoint(websocket: WebSocket, room_code: str, player_id: str, role: str):
    room_code = room_code.upper()
    await manager.connect(room_code, player_id, websocket)
    rooms[room_code]["player_roles"][player_id] = role
    
    await websocket.send_json({"type": "INIT_STATE", "state": rooms[room_code]["state"]})
    
    role_emojis = {"lumberjack": "🪓 Дровосек", "keeper": "🔥 Хранитель", "scout": "🧭 Проводник"}
    role_name = role_emojis.get(role, "Выживший")
    await manager.broadcast_to_room(room_code, {
        "type": "PLAYER_JOINED", "player_id": f"{player_id} ({role_name})"
    })

    try:
        while True:
            data = await websocket.receive_json()
            if room_code not in rooms or rooms[room_code]["state"]["game_over"] or rooms[room_code]["state"]["victory"]:
                continue
                
            state = rooms[room_code]["state"]
            action = data.get("action")
            p_role = rooms[room_code]["player_roles"].get(player_id, "survivor")
            
            if action == "burn_one_wood":
                if state["wood"] >= 1:
                    # Хранитель костра даёт +8% тепла вместо +5%
                    boost = 8 if p_role == "keeper" else 5
                    state["warmth"] = min(100, state["warmth"] + boost)
                    await manager.broadcast_to_room(room_code, {
                        "type": "STATE_UPDATE", "state": state,
                        "log": f"🔥 {player_id} бросил дрово в костёр (+{boost}% тепла)."
                    })

            elif action == "go_to_forest":
                is_blizzard = (state["weather"] == "Буран")
                # У Проводника и Дровосека меньше шанс заблудиться
                fail_chance = 0.25
                if is_blizzard:
                    fail_chance = 0.40 if p_role in ["lumberjack", "scout"] else 0.70
                else:
                    fail_chance = 0.10 if p_role in ["lumberjack", "scout"] else 0.25
                
                if random.random() > fail_chance:
                    # Дровосек рубит больше дров
                    min_w, max_w = (7, 12) if p_role == "lumberjack" else (4, 7)
                    added_wood = random.randint(min_w, max_w)
                    state["wood"] += added_wood
                    await manager.broadcast_to_room(room_code, {
                        "type": "STATE_UPDATE", "state": state,
                        "log": f"🌲 {player_id} принёс {added_wood} дров из леса."
                    })
                else:
                    damage = 20 if is_blizzard else 10
                    state["warmth"] = max(0, state["warmth"] - damage)
                    await manager.broadcast_to_room(room_code, {
                        "type": "STATE_UPDATE", "state": state,
                        "log": f"🥶 {player_id} заблудился! Лагерь потерял {damage}% тепла."
                    })

            elif action == "change_weather":
                state["weather"] = "Буран" if state["weather"] == "Ясно" else "Ясно"
                status_text = "⚠️ Налетает ледяной Буран!" if state["weather"] == "Буран" else "☀️ Буря утихла."
                await manager.broadcast_to_room(room_code, {
                    "type": "STATE_UPDATE", "state": state, "log": status_text
                })
    except WebSocketDisconnect:
        manager.disconnect(room_code, player_id)
