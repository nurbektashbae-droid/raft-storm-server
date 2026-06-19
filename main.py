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
        "wood": 20, 
        "warmth": 100, 
        "time_left": 120, 
        "game_over": False, 
        "victory": False,
        "upgrades": {
            "canopy": False,  
            "axes": False,    
            "scarfs": False   
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
                "log": "☀️ Рассвет! Вы пережили эту суровую ночь! 🎉"
            })
            continue

        event_timer += 1
        if event_timer >= 25:
            event_timer = 0
            event_type = random.choice(["blessing", "curse", "none"])
            if event_type == "blessing":
                bonus = random.randint(6, 12)
                state["wood"] += bonus
                await manager.broadcast_to_room(room_code, {
                    "type": "STATE_UPDATE", "state": state,
                    "log": f"📦 СОБЫТИЕ: Метель прибила к лагерю обломки ящика! (+{bonus} дров)"
                })
            elif event_type == "curse":
                damage = 10 if state["upgrades"].get("canopy") else 20
                state["warmth"] = max(0, state["warmth"] - damage)
                log_msg = "💨 СОБЫТИЕ: Ледяной шквал бьёт по тенту! Навес уберёг от части стужи. (-10% тепла)" if damage == 10 else "💨 СОБЫТИЕ: Бешеный порыв ветра пробивает лагерь! Костёр притух. (-20% тепла)"
                await manager.broadcast_to_room(room_code, {
                    "type": "STATE_UPDATE", "state": state, "log": log_msg
                })

        # Потребление костра (МАСШТАБИРОВАНИЕ от количества игроков)
        player_count = len(room["players"])
        if state["wood"] > 0:
            # Базовый расход: 1 дрово в сек (в ясно) или 2 (в буран)
            base_burn = 2 if state["weather"] == "Буран" else 1
            # Добавляем +1 расход дров за каждых двух дополнительных игроков
            wood_burn = base_burn + (player_count // 2)
            
            state["wood"] = max(0, state["wood"] - wood_burn)
            state["warmth"] = min(100, state["warmth"] + 1)
        else:
            cold_speed = 6 if state["weather"] == "Буран" else 3
            if state["upgrades"].get("canopy") and state["weather"] == "Буран":
                cold_speed = 3
            state["warmth"] = max(0, state["warmth"] - cold_speed)

        if state["warmth"] <= 0:
            state["game_over"] = True
            await manager.broadcast_to_room(room_code, {
                "type": "GAME_OVER", "state": state,
                "log": "💀 Тьма поглотила вас... Костёр окончательно погас."
            })

        await manager.broadcast_state_with_stamina(room_code)

class ConnectionManager:
    async def connect(self, room_code: str, player_id: str, websocket: WebSocket):
        await websocket.accept()
        if room_code not in rooms:
            rooms[room_code] = {
                "players": {},
                "player_roles": {},
                "players_stamina": {},
                "players_status": {}, # Новый словарь для статусов
                "state": get_initial_state(),
                "task": None
            }
            rooms[room_code]["task"] = asyncio.create_task(room_tick(room_code))
        rooms[room_code]["players"][player_id] = websocket
        rooms[room_code]["players_stamina"][player_id] = 100
        rooms[room_code]["players_status"][player_id] = "⛺ В лагере"

    def disconnect(self, room_code: str, player_id: str):
        if room_code in rooms:
            if player_id in rooms[room_code]["players"]:
                del rooms[room_code]["players"][player_id]
            if player_id in rooms[room_code]["player_roles"]:
                del rooms[room_code]["player_roles"][player_id]
            if player_id in rooms[room_code]["players_stamina"]:
                del rooms[room_code]["players_stamina"][player_id]
            if player_id in rooms[room_code]["players_status"]:
                del rooms[room_code]["players_status"][player_id]
            if not rooms[room_code]["players"]:
                if rooms[room_code]["task"]:
                    rooms[room_code]["task"].cancel()
                del rooms[room_code]

    async def broadcast_to_room(self, room_code: str, message: dict):
        if room_code in rooms:
            for pid, ws in rooms[room_code]["players"].items():
                try: 
                    await ws.send_json(message)
                except: 
                    pass

    async def broadcast_state_with_stamina(self, room_code: str, custom_log: str = None):
        if room_code in rooms:
            room = rooms[room_code]
            
            # Собираем список игроков для отображения на фронтенде
            role_emojis = {"lumberjack": "🪓", "keeper": "🔥", "scout": "🧭"}
            players_list = []
            for pid in room["players"]:
                r = room["player_roles"].get(pid, "survivor")
                status = room["players_status"].get(pid, "⛺ В лагере")
                stamina = room["players_stamina"].get(pid, 100)
                emoji = role_emojis.get(r, "👤")
                players_list.append(f"{emoji} {pid}: Энергия {stamina}% | {status}")

            for pid, ws in room["players"].items():
                msg = {
                    "type": "STATE_UPDATE",
                    "state": room["state"],
                    "player_stamina": room["players_stamina"].get(pid, 100),
                    "players_list": players_list # Шлем список всем
                }
                if custom_log:
                    msg["log"] = custom_log
                try:
                    await ws.send_json(msg)
                except:
                    pass

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
    
    await websocket.send_json({
        "type": "INIT_STATE", 
        "state": rooms[room_code]["state"],
        "player_stamina": 100
    })
    
    role_emojis = {"lumberjack": "🪓 Дровосек", "keeper": "🔥 Хранитель костра", "scout": "🧭 Проводник"}
    role_name = role_emojis.get(role, "Выживший")
    await manager.broadcast_to_room(room_code, {
        "type": "PLAYER_JOINED", "player_id": f"{player_id} ({role_name})"
    })

    try:
        while True:
            data = await websocket.receive_json()
            if room_code not in rooms:
                continue
                
            room = rooms[room_code]
            state = room["state"]
            action = data.get("action")
            
            if action == "restart_game":
                current_upgrades = state["upgrades"]
                room["state"] = get_initial_state()
                room["state"]["upgrades"] = current_upgrades
                for pid in room["players_stamina"]:
                    room["players_stamina"][pid] = 100
                    room["players_status"][pid] = "⛺ В лагере"
                    
                await manager.broadcast_to_room(room_code, {
                    "type": "INIT_STATE", 
                    "state": room["state"],
                    "player_stamina": 100,
                    "log": f"🔄 {player_id} разжёг новый костёр! Улучшения лагеря сохранены."
                })
                continue

            if action in ["buy_canopy", "buy_axes", "buy_scarfs"]:
                costs = {"buy_canopy": 25, "buy_axes": 30, "buy_scarfs": 20}
                item_keys = {"buy_canopy": "canopy", "buy_axes": "axes", "buy_scarfs": "scarfs"}
                cost = costs[action]
                key = item_keys[action]
                
                if state["wood"] >= cost and not state["upgrades"][key]:
                    state["wood"] -= cost
                    state["upgrades"][key] = True
                    item_names = {"canopy": "⛺ Прочный Навес", "axes": "🪓 Острые Топоры", "scarfs": "🧣 Теплые Шарфы"}
                    await manager.broadcast_state_with_stamina(room_code, f"🛠️ {player_id} улучшил лагерь: {item_names[key]}!")
                continue

            if state["game_over"] or state["victory"]:
                continue
                
            p_role = room["player_roles"].get(player_id, "survivor")
            cost_modifier = 5 if state["upgrades"].get("scarfs") else 0
            
            if action == "burn_one_wood":
                cost = max(5, 15 - cost_modifier)
                if room["players_stamina"].get(player_id, 100) >= cost:
                    if state["wood"] >= 1:
                        room["players_stamina"][player_id] -= cost
                        boost = 10 if p_role == "keeper" else 6
                        state["warmth"] = min(100, state["warmth"] + boost)
                        state["wood"] -= 1
                        await manager.broadcast_state_with_stamina(room_code, f"🔥 {player_id} подбросил дрова в огонь (+{boost}% тепла).")

            elif action == "go_to_forest":
                cost = max(15, 35 - cost_modifier)
                if room["players_stamina"].get(player_id, 100) >= cost:
                    room["players_stamina"][player_id] -= cost
                    room["players_status"][player_id] = "🌲 В лесу" # Меняем статус
                    
                    # Чтобы дать фронтенду время на анимацию, сервер рассчитывает исход сразу,
                    # но статус игрока обновится обратно в лагерь через 3 секунды
                    async def delayed_forest_result(pid, role_p):
                        await asyncio.sleep(3)
                        if room_code in rooms and pid in rooms[room_code]["players"]:
                            r_room = rooms[room_code]
                            r_state = r_room["state"]
                            is_blizzard = (r_state["weather"] == "Буран")
                            
                            if role_p == "scout":
                                fail_chance = 0.35 if is_blizzard else 0.15
                            elif role_p == "lumberjack":
                                fail_chance = 0.45 if is_blizzard else 0.20
                            else:
                                fail_chance = 0.70 if is_blizzard else 0.30
                            
                            if random.random() > fail_chance:
                                min_w, max_w = (8, 14) if role_p == "lumberjack" else (4, 8)
                                bonus_axes = 3 if r_state["upgrades"].get("axes") else 0
                                added_wood = random.randint(min_w, max_w) + bonus_axes
                                r_state["wood"] += added_wood
                                r_room["players_status"][pid] = "⛺ В лагере"
                                await manager.broadcast_state_with_stamina(room_code, f"🌲 {pid} успешно вернулся и принёс {added_wood} дров.")
                            else:
                                damage = 18 if is_blizzard else 10
                                r_state["warmth"] = max(0, r_state["warmth"] - damage)
                                r_room["players_status"][pid] = "⛺ В лагере"
                                await manager.broadcast_state_with_stamina(room_code, f"🥶 {pid} заблудился в лесу! Теряет тепло лагеря (-{damage}%).")

                    asyncio.create_task(delayed_forest_result(player_id, p_role))
                    await manager.broadcast_state_with_stamina(room_code)

            elif action == "rest_at_camp":
                room["players_status"][player_id] = "💤 Отдыхает"
                
                async def delayed_rest(pid):
                    await asyncio.sleep(4)
                    if room_code in rooms and pid in rooms[room_code]["players"]:
                        r_room = rooms[room_code]
                        current_stamina = r_room["players_stamina"].get(pid, 100)
                        r_room["players_stamina"][pid] = min(100, current_stamina + 50)
                        r_room["players_status"][pid] = "⛺ В лагере"
                        await manager.broadcast_state_with_stamina(room_code, f"💤 {pid} восстановил силы у огня (+50 Энергии).")
                        
                asyncio.create_task(delayed_rest(player_id))
                await manager.broadcast_state_with_stamina(room_code)

            elif action == "change_weather":
                state["weather"] = "Буран" if state["weather"] == "Ясно" else "Ясно"
                status_text = "⚠️ ВНИМАНИЕ: Начинается лютый Буран!" if state["weather"] == "Буран" else "☀️ Буря утихла. Небо прояснилось."
                await manager.broadcast_state_with_stamina(room_code, status_text)
                
    except WebSocketDisconnect:
        manager.disconnect(room_code, player_id)
