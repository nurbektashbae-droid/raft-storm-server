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

def get_initial_state():
    return {
        "weather": "Ясно", 
        "wood": 20, # Немного подняли стартовый запас для покупок
        "warmth": 100, 
        "time_left": 120, 
        "game_over": False, 
        "victory": False,
        "upgrades": {
            "canopy": false,  # Навес
            "axes": false,    # Топоры
            "scarfs": false   # Шарфы
        }
    }

async def room_tick(room_code: str):
    event_timer = 0
    while room_code in rooms:
        await asyncio.sleep(1)
        if room_code not in rooms:
            break
            
        room = rooms[room_code]
        state = room["state"]
        
        if state["game_over"] or state["victory"]:
            continue

        if state["time_left"] > 0:
            state["time_left"] -= 1
        else:
            state["victory"] = True
            await manager.broadcast_to_room(room_code, {
                "type": "VICTORY", "state": state,
                "log": "☀️ Рассвет! Вы пережили эту ночь! Можете закупиться улучшениями перед следующей каткой. 🎉"
            })
            continue

        event_timer += 1
        if event_timer >= 25:
            event_timer = 0
            event_type = random.choice(["blessing", "curse", "none"])
            if event_type == "blessing":
                bonus = random.randint(5, 10)
                state["wood"] += bonus
                await manager.broadcast_to_room(room_code, {
                    "type": "STATE_UPDATE", "state": state,
                    "log": f"📦 Событие: Ветер принёс сухие ветки! (+{bonus} дров)"
                })
            elif event_type == "curse":
                # Если куплен навес, урон от порыва ветра меньше (10 вместо 15)
                damage = 10 if state["upgrades"].get("canopy") else 15
                state["warmth"] = max(0, state["warmth"] - damage)
                log_msg = "💨 Событие: Навес защитил от ветра!" if damage == 10 else "💨 Событие: Ледяной порыв ветра пронзает до костей! (-15% тепла)"
                await manager.broadcast_to_room(room_code, {
                    "type": "STATE_UPDATE", "state": state, "log": log_msg
                })

        # Сгорание дров
        if state["wood"] > 0:
            wood_burn = 2 if state["weather"] == "Буран" else 1
            state["wood"] = max(0, state["wood"] - wood_burn)
            state["warmth"] = min(100, state["warmth"] + 1)
        else:
            cold_speed = 5 if state["weather"] == "Буран" else 2
            # Навес снижает скорость замерзания при отсутствии дров
            if state["upgrades"].get("canopy") and state["weather"] == "Буран":
                cold_speed = 3
            state["warmth"] = max(0, state["warmth"] - cold_speed)

        if state["warmth"] <= 0:
            state["game_over"] = True
            await manager.broadcast_to_room(room_code, {
                "type": "GAME_OVER", "state": state,
                "log": "💀 Лагерь полностью замерз..."
            })

        await manager.broadcast_to_room(room_code, {"type": "STATE_UPDATE", "state": state})

class ConnectionManager:
    async def connect(self, room_code: str, player_id: str, websocket: WebSocket):
        await websocket.accept()
        if room_code not in rooms:
            rooms[room_code] = {
                "players": {},
                "player_roles": {},
                "state": get_initial_state(),
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
            if room_code not in rooms:
                continue
                
            state = rooms[room_code]["state"]
            action = data.get("action")
            
            if action == "restart_game":
                # При перезапуске сохраняем апгрейды лагеря!
                current_upgrades = state["upgrades"]
                rooms[room_code]["state"] = get_initial_state()
                rooms[room_code]["state"]["upgrades"] = current_upgrades
                await manager.broadcast_to_room(room_code, {
                    "type": "INIT_STATE", 
                    "state": rooms[room_code]["state"],
                    "log": f"🔄 {player_id} начал новую ночь. Купленные улучшения сохранены!"
                })
                continue

            # Покупка улучшений
            if action in ["buy_canopy", "buy_axes", "buy_scarfs"]:
                costs = {"buy_canopy": 25, "buy_axes": 30, "buy_scarfs": 20}
                item_keys = {"buy_canopy": "canopy", "buy_axes": "axes", "buy_scarfs": "scarfs"}
                cost = costs[action]
                key = item_keys[action]
                
                if state["wood"] >= cost and not state["upgrades"][key]:
                    state["wood"] -= cost
                    state["upgrades"][key] = True
                    item_names = {"canopy": "🛠️ Навес", "axes": "🪓 Топоры", "scarfs": " scarves"}
                    await manager.broadcast_to_room(room_code, {
                        "type": "STATE_UPDATE", "state": state,
                        "log": f"🛒 Лагерь приобрёл улучшение: {item_names[key]}!"
                    })
                continue

            if state["game_over"] or state["victory"]:
                continue
                
            p_role = rooms[room_code]["player_roles"].get(player_id, "survivor")
            
            if action == "burn_one_wood":
                if state["wood"] >= 1:
                    boost = 8 if p_role == "keeper" else 5
                    state["warmth"] = min(100, state["warmth"] + boost)
                    state["wood"] -= 1
                    await manager.broadcast_to_room(room_code, {
                        "type": "STATE_UPDATE", "state": state,
                        "log": f"🔥 {player_id} бросил дрово в костёр (+{boost}% тепла)."
                    })

            elif action == "go_to_forest":
                is_blizzard = (state["weather"] == "Буран")
                fail_chance = 0.40 if p_role in ["lumberjack", "scout"] else 0.70 if is_blizzard else 0.25
                
                if random.random() > fail_chance:
                    min_w, max_w = (7, 12) if p_role == "lumberjack" else (4, 7)
                    # Эффект наточенных топоров (+2 дров)
                    bonus_axes = 2 if state["upgrades"].get("axes") else 0
                    added_wood = random.randint(min_w, max_w) + bonus_axes
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
