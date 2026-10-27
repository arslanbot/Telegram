from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import psycopg2
import json
import hashlib
import datetime
import os

app = FastAPI()

# Подключаем статические файлы (HTML)
app.mount("/static", StaticFiles(directory="static"), name="static")

# Получаем URL базы данных из переменных окружения (Render сам её подставит)
DATABASE_URL = os.environ.get("DATABASE_URL")

def get_db_connection():
    url = DATABASE_URL.replace("postgres://", "postgresql://")
    return psycopg2.connect(url)

def init_db():
    if not DATABASE_URL:
        print("ВНИМАНИЕ: DATABASE_URL не задан!")
        return
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        username TEXT UNIQUE,
        password TEXT,
        avatar TEXT DEFAULT '👤'
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS messages (
        id SERIAL PRIMARY KEY,
        sender_id INTEGER,
        receiver_id INTEGER,
        text TEXT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.commit()
    conn.close()

init_db()

class UserRegister(BaseModel):
    username: str
    password: str

class UserLogin(BaseModel):
    username: str
    password: str

active_connections = {} 

@app.post("/api/register")
async def register(user: UserRegister):
    conn = get_db_connection()
    c = conn.cursor()
    hashed = hashlib.sha256(user.password.encode()).hexdigest()
    try:
        c.execute("INSERT INTO users (username, password) VALUES (%s, %s)", (user.username, hashed))
        conn.commit()
        return {"status": "ok", "message": "Пользователь создан"}
    except psycopg2.IntegrityError:
        raise HTTPException(status_code=400, detail="Такой пользователь уже есть")
    finally:
        conn.close()

@app.post("/api/login")
async def login(user: UserLogin):
    conn = get_db_connection()
    c = conn.cursor()
    hashed = hashlib.sha256(user.password.encode()).hexdigest()
    c.execute("SELECT id, username, avatar FROM users WHERE username=%s AND password=%s", (user.username, hashed))
    row = c.fetchone()
    conn.close()
    if row:
        return {"status": "ok", "user_id": row[0], "username": row[1], "avatar": row[2]}
    raise HTTPException(status_code=401, detail="Неверный логин или пароль")

@app.get("/api/users/{user_id}")
async def get_users(user_id: int):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT id, username, avatar FROM users WHERE id != %s", (user_id,))
    users = [{"id": r[0], "username": r[1], "avatar": r[2]} for r in c.fetchall()]
    conn.close()
    return users

@app.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: int):
    await websocket.accept()
    active_connections[user_id] = websocket
    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            sender_id = msg['sender_id']
            receiver_id = msg['receiver_id']
            text = msg['text']
            
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("INSERT INTO messages (sender_id, receiver_id, text) VALUES (%s, %s, %s) RETURNING timestamp",
                      (sender_id, receiver_id, text))
            timestamp = c.fetchone()[0].isoformat()
            conn.commit()
            conn.close()

            if receiver_id in active_connections:
                await active_connections[receiver_id].send_text(json.dumps({
                    "sender_id": sender_id,
                    "text": text,
                    "timestamp": timestamp
                }))
    except WebSocketDisconnect:
        del active_connections[user_id]
    except Exception as e:
        print(f"Ошибка: {e}")
        if user_id in active_connections:
            del active_connections[user_id]

@app.get("/")
async def read_index():
    return FileResponse('static/index.html')

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
