import sqlite3
import re
from datetime import datetime, date, timezone, timedelta
from fastapi import FastAPI, Request, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import jwt
from models import ConfigUpdate
from database import init_db

# Configurações de Segurança
SECRET_KEY = "GIANNONE_SUPER_SECRET"
ALGORITHM = "HS256"
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

app = FastAPI(title="Agente Operacional Giannone Transportes")

# Inicializa Banco de Dados
init_db()

# Montando Arquivos Estáticos e Templates (Frontend)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Funções Auxiliares de Banco e Auth
def get_db_connection():
    conn = sqlite3.connect("data/giannone.db")
    conn.row_factory = sqlite3.Row
    return conn

def verify_password(plain_password, hashed_password):
    import hashlib
    return hashlib.sha256(plain_password.encode()).hexdigest() == hashed_password

def create_access_token(data: dict):
    return jwt.encode(data, SECRET_KEY, algorithm=ALGORITHM)

async def get_current_user(token: str = Depends(oauth2_scheme)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None: raise HTTPException(status_code=401)
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Token inválido")
    
    conn = get_db_connection()
    user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    conn.close()
    if user is None:
        raise HTTPException(status_code=401, detail="Usuário não encontrado")
    return dict(user)

# --------- ROTAS DE FRONTEND ---------
@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

# --------- ROTAS DE AUTENTICAÇÃO ---------
@app.post("/token")
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    conn = get_db_connection()
    user = conn.execute("SELECT * FROM users WHERE username = ?", (form_data.username,)).fetchone()
    conn.close()
    
    if not user or not verify_password(form_data.password, user["password_hash"]):
        raise HTTPException(status_code=400, detail="Usuário ou senha incorretos")
    
    access_token = create_access_token(data={"sub": user["username"], "role": user["role"]})
    return {"access_token": access_token, "token_type": "bearer", "role": user["role"]}

# --------- ROTAS DO PAINEL ADMIN ---------
@app.get("/api/config")
async def get_config(current_user: dict = Depends(get_current_user)):
    conn = get_db_connection()
    config = dict(conn.execute("SELECT * FROM config LIMIT 1").fetchone() or {})
    conn.close()
    return config

@app.post("/api/config")
async def update_config(config_data: ConfigUpdate, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Apenas admins podem alterar regras.")
    
    conn = get_db_connection()
    conn.execute(
        "UPDATE config SET palavra_chave = ?, regex_placa = ?, evo_url = ?, evo_instance = ?, evo_apikey = ?", 
        (config_data.palavra_chave, config_data.regex_placa, config_data.evo_url, config_data.evo_instance, config_data.evo_apikey)
    )
    conn.commit()
    conn.close()
    return {"status": "Configurações atualizadas com sucesso!"}

from pydantic import BaseModel
class SyncData(BaseModel):
    meu_link: str

import requests
@app.post("/api/evolution/sync")
async def sync_evolution(data: SyncData, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != "admin": raise HTTPException(status_code=403)
    conn = get_db_connection()
    config = dict(conn.execute("SELECT * FROM config LIMIT 1").fetchone())
    conn.close()
    if not config.get('evo_url') or not config.get('evo_instance') or not config.get('evo_apikey'):
        raise HTTPException(status_code=400, detail="Configure a API primeiro.")
    
    url = f"{config['evo_url'].rstrip('/')}/webhook/set/{config['evo_instance']}"
    headers = {"apikey": config['evo_apikey'], "Content-Type": "application/json"}
    payload = {
        "webhook": {
            "enabled": True,
            "url": f"{data.meu_link.rstrip('/')}/webhook/evolution",
            "webhookByEvents": False,
            "webhookBase64": False,
            "events": ["MESSAGES_UPSERT"]
        }
    }
    try:
        r = requests.post(url, json=payload, headers=headers)
        r.raise_for_status()
        return {"status": "Webhook sincronizado na Evolution API!"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/users")
async def list_users(current_user: dict = Depends(get_current_user)):
    if current_user["role"] != "admin":
        return []
    conn = get_db_connection()
    users = conn.execute("SELECT id, username, role FROM users").fetchall()
    conn.close()
    return [dict(u) for u in users]

@app.post("/api/users")
async def create_user(username: str, role: str, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Acesso negado.")
    import hashlib
    password_hash = hashlib.sha256("giannone123".encode()).hexdigest() # Senha padrão
    
    conn = get_db_connection()
    try:
        conn.execute("INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)", (username, password_hash, role))
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Usuário já existe.")
    finally:
        conn.close()
    return {"status": "Usuário criado. Senha padrão: giannone123"}

@app.delete("/api/users/{user_id}")
async def delete_user(user_id: int, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != "admin": raise HTTPException(status_code=403)
    conn = get_db_connection()
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    return {"status": "ok"}

# --------- ROTA DO RELATÓRIO / LISTA DE CAMINHÕES ---------
@app.get("/api/disponiveis")
async def listar_disponiveis(dia: str = None, current_user: dict = Depends(get_current_user)):
    if not dia: dia = date.today().strftime("%Y-%m-%d")
    conn = get_db_connection()
    veiculos = conn.execute("SELECT * FROM veiculos WHERE data_operacao = ?", (dia,)).fetchall()
    conn.close()
    return [dict(v) for v in veiculos]

# --------- ROTA DE WEBHOOK (EVOLUTION API) ---------
@app.post("/webhook/evolution")
async def webhook_evolution(request: Request):
    try:
        payload = await request.json()
        if payload.get("event") == "messages.upsert":
           processar_mensagem_webhook(payload)
        return {"status": "ok"}
    except Exception as e:
        return {"status": "erro", "detalhe": str(e)}

def obter_nome_grupo(jid, config):
    if not config.get('evo_url') or not config.get('evo_apikey') or not config.get('evo_instance'):
        return f"Grupo ({jid.split('@')[0][-4:]})"
    try:
        # A API Evolution V1 e V2 usa essa rota para extrair os Info do Grupo (aonde tem o título)
        url = f"{config['evo_url'].rstrip('/')}/group/findGroupInfos/{config['evo_instance']}?groupJid={jid}"
        headers = {"apikey": config['evo_apikey']}
        resp = requests.get(url, headers=headers, timeout=5)
        if resp.ok:
            data = resp.json()
            # Pode retornar em formatação antiga ou nova
            return data.get('subject') or data.get('name') or f"Grupo ({jid.split('@')[0][-4:]})"
    except:
        pass
    return f"Grupo ({jid.split('@')[0][-4:]})"

def processar_mensagem_webhook(payload: dict):
    conn = get_db_connection()
    config = dict(conn.execute("SELECT * FROM config LIMIT 1").fetchone())
    
    regex_disp = re.compile(config["palavra_chave"], re.IGNORECASE)
    regex_placa = re.compile(config["regex_placa"])
    
    data = payload.get("data", {})
    remote_jid = data.get("key", {}).get("remoteJid", "")
    if data.get("key", {}).get("fromMe", False): return
    
    message_content = data.get("message", {})
    texto_original = message_content.get("conversation", message_content.get("extendedTextMessage", {}).get("text", ""))
    if not texto_original: return
    
    # Valida regras
    if not regex_disp.search(texto_original): return
    placa_match = regex_placa.search(texto_original)
    if not placa_match: return
    
    placa = placa_match.group(0).upper()
    telefone = data.get("participant") or data.get("key", {}).get("participant", "") or remote_jid
    # Remove as marcações de JID ocultos, de dispositivos multiplos e etc
    telefone = telefone.split("@")[0].split(":")[0]  
    
    # Tentativa de pegar o número real caso seja um grupo de comunidade (que mascara a ID)
    if "sender" in data:
        telefone = data["sender"].split("@")[0].split(":")[0]
    
    motorista = data.get("pushName", "Desconhecido")
    
    # ------------------ PEGA O NOME REAL DO GRUPO (Evolution API) ------------------
    if "@g.us" in remote_jid:
        grupo = obter_nome_grupo(remote_jid, config)
    else:
        grupo = "Chat Privado"
    
    agora_sp = datetime.now(timezone(timedelta(hours=-3)))
    timestamp_msg = data.get("messageTimestamp", int(agora_sp.timestamp()))
    dt_hora = datetime.fromtimestamp(timestamp_msg, tz=timezone(timedelta(hours=-3)))
    
    data_operacao = dt_hora.strftime("%Y-%m-%d")
    horario_mensagem = dt_hora.strftime("%H:%M:%S")
    
    # Verifica se já mandou hoje e atualiza, senão insere
    existente = conn.execute("SELECT id FROM veiculos WHERE data_operacao=? AND telefone=?", (data_operacao, telefone)).fetchone()
    
    if existente:
        conn.execute("UPDATE veiculos SET placa=?, grupo=?, horario_mensagem=?, mensagem_original=? WHERE id=?", 
                     (placa, grupo, horario_mensagem, texto_original, existente["id"]))
    else:
        conn.execute("INSERT INTO veiculos (data_operacao, motorista, telefone, placa, grupo, horario_mensagem, mensagem_original) VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (data_operacao, motorista, telefone, placa, grupo, horario_mensagem, texto_original))
    
    conn.commit()
    conn.close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
