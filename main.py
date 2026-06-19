import asyncio
import random
import string
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
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

# Тот самый HTML-интерфейс игры, теперь он зашит прямо в сервер!
html_content = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Перевал: Сквозь Метель</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { background: #121213; color: #e2e8f0; font-family: 'Courier New', Courier, monospace; display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 100vh; padding: 20px; }
        .card { background: #1e1e24; border: 2px solid #3f3f46; border-radius: 12px; padding: 24px; width: 100%; max-width: 400px; text-align: center; box-shadow: 0 10px 15px -3px rgba(0,0,0,0.5); }
        h1 { font-size: 20px; margin-bottom: 20px; color: #f1f5f9; text-transform: uppercase; letter-spacing: 2px; }
        input { width: 100%; padding: 12px; background: #27272a; border: 1px solid #4b5563; border-radius: 6px; color: #fff; text-align: center; font-size: 18px; margin-bottom: 16px; text-transform: uppercase; }
        button { width: 100%; padding: 14px; background: #ea580c; border: none; border-radius: 6px; color: white; font-weight: bold; font-size: 16px; cursor: pointer; text-transform: uppercase; transition: 0.2s; }
        button:hover { background: #c2410c; }
        .hidden { display: none; }
        .status-box { background: #27272a; border: 1px dashed #4b5563; padding: 12px; margin: 12px 0; border-radius: 6px; }
        .stat { font-size: 24px; font-weight: bold; color: #f97316; margin: 8px 0; }
        #weather { font-size: 22px; color: #38bdf8; font-weight: bold; }
        #log { background: #09090b; padding: 10px; height: 120px; overflow-y: auto; text-align: left; font-size: 12px; border-radius: 4px; border: 1px solid #27272a; color: #a1a1aa; }
        #error-text { color: #ef4444; font-size: 12px; margin-top: 10px; text-align: left; word-break: break-all; }
    </style>
</head>
<body>
    <div id="lobby-screen" class="card">
        <h1>Сквозь Метель</h1>
        <p style="font-size: 14px; color: #a1a1aa; margin-bottom: 20px;">Создайте новый перевал или введите код</p>
        <button onclick="createRoom()" style="background: #2563eb; margin-bottom: 12px;">Создать перевал</button>
        <div style="margin: 10px 0; color: #71717a;">— ИЛИ —</div>
        <input type="text" id="room-input" placeholder="КОД ИГРЫ" maxlength="4">
        <button onclick="joinRoom()">Войти в бурю</button>
        <div id="error-text"></div>
    </div>

    <div id="game-screen" class="card hidden">
        <h1 id="game-title">Перевал: ----</h1>
        <div class="status-box">
            <div>Погода:</div>
            <div id="weather">Ясно</div>
        </div>
        <div class="status-box">
            <div>Дрова в лагере:</div>
            <div id="wood-count" class="stat">0</div>
        </div>
        <button onclick="sendAction('add_wood')" style="margin-bottom: 20px; font-size: 18px; padding: 20px;">🔥 ПОД КИНУТЬ ДРОВ</button>
        <button onclick="sendAction('change_weather')" style="background: #ef4444; font-size: 12px; padding: 8px; margin-bottom: 15px;">Вызвать буран</button>
        <div id="log"></div>
    </div>

    <script>
        // Теперь скрипт сам определяет, на каком хосте он запущен!
        const HOST = window.location.host;
        const PROTOCOL = window.location.protocol === 'https:' ? 'https:' : 'http:';
        const WS_PROTOCOL = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        
        let ws;
        let myId = "Игрок_" + Math.floor(Math.random() * 1000);

        function showErr(msg) { document.getElementById('error-text').innerText = msg; }

        async function createRoom() {
            try {
                let response = await fetch(`${PROTOCOL}//${HOST}/create_room`);
                let data = await response.json();
                document.getElementById('room-input').value = data.room_code;
                joinRoom();
            } catch (e) { showErr("Ошибка: " + e.message); }
        }

        function joinRoom() {
            let code = document.getElementById('room-input').value.trim().toUpperCase();
            if (code.length !== 4) return alert("4 буквы!");
            try {
                ws = new WebSocket(`${WS_PROTOCOL}//${HOST}/ws/${code}/${myId}`);
                ws.onopen = () => {
                    document.getElementById('lobby-screen').classList.add('hidden');
                    document.getElementById('game-screen').classList.remove('hidden');
                    document.getElementById('game-title').innerText = `Перевал: ${code}`;
                };
                ws.onmessage = (event) => {
                    let data = JSON.parse(event.data);
                    if (data.type === "INIT_STATE" || data.type === "STATE_UPDATE") {
                        document.getElementById('wood-count').innerText = data.state.wood;
                        document.getElementById('weather').innerText = data.state.weather;
                        document.getElementById('weather').style.color = data.state.weather === "Буран" ? "#ef4444" : "#38bdf8";
                        if (data.log) addLog(data.log);
                    } else if (data.type === "PLAYER_JOINED") { addLog(`⛺ ${data.player_id} у костра.`); }
                };
                ws.onerror = () => { showErr("Ошибка WebSocket"); };
            } catch(e) { showErr(e.message); }
        }

        function sendAction(act) {
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ action: act, weather: "Буран" }));
            }
        }

        function addLog(txt) {
            let logDiv = document.getElementById('log');
            logDiv.innerHTML += `<div>` + txt + `</div>`;
            logDiv.scrollTop = logDiv.scrollHeight;
        }
    </script>
</body>
</html>
"""

# При заходе на главную страницу сервера — отдаем этот HTML
@app.get("/", response_class=HTMLResponse)
def read_root():
    return html_content

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
