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
        "UPDATE config SET palavra_chave = ?, regex_placa = ?, evo_url = ?, evo_instance = ?, evo_apikey = ?, msg_erro_placa = ?, llm_api_key = ?, llm_model = ?", 
        (config_data.palavra_chave, config_data.regex_placa, config_data.evo_url, config_data.evo_instance, config_data.evo_apikey, config_data.msg_erro_placa, config_data.llm_api_key, config_data.llm_model)
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

@app.get("/api/webhook/status")
async def get_webhook_status(current_user: dict = Depends(get_current_user)):
    return {"last_hook": LAST_WEBHOOK_TIME}

@app.post("/api/waha/ping")
async def ping_waha(current_user: dict = Depends(get_current_user)):
    if current_user["role"] != "admin": raise HTTPException(status_code=403)
    conn = get_db_connection()
    config = dict(conn.execute("SELECT * FROM config LIMIT 1").fetchone() or {})
    conn.close()
    
    if not config.get('evo_url') or not config.get('evo_instance'):
        raise HTTPException(status_code=400, detail="Configure a WAHA e salve primeiro.")
    
    try:
        url = f"{config['evo_url'].rstrip('/')}/api/sessions/{config['evo_instance']}"
        h = {"accept": "application/json"}
        if config.get('evo_apikey'):
            h["X-Api-Key"] = config['evo_apikey']
        r = requests.get(url, headers=h, timeout=5)
        if r.ok:
            info = r.json()
            return {"status": f"WAHA Conectado! (Status do Celular: {info.get('status', 'OK')})"}
        else:
            raise Exception(f"WAHA retornou erro HTTP {r.status_code}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Erro ao contatar WAHA: {str(e)}")

@app.post("/api/waha/sync")
async def sync_history_waha(current_user: dict = Depends(get_current_user)):
    if current_user["role"] != "admin": raise HTTPException(status_code=403)
    conn = get_db_connection()
    config = dict(conn.execute("SELECT * FROM config LIMIT 1").fetchone() or {})
    conn.close()
    
    if not config.get('evo_url') or not config.get('evo_instance'):
        raise HTTPException(status_code=400, detail="Configure a WAHA primeiro.")
    
    try:
        url = f"{config['evo_url'].rstrip('/')}/api/messages?session={config['evo_instance']}&limit=200"
        h = {"accept": "application/json"}
        if config.get('evo_apikey'): h["X-Api-Key"] = config['evo_apikey']
        r = requests.get(url, headers=h, timeout=20)
        if r.ok:
            msgs = r.json()
            # WAHA pode retornar lista direta ou paginate em "data"
            if isinstance(msgs, dict): msgs = msgs.get("data", [])
            for m in msgs:
                # Simula o payload de webhook WAHA
                processar_mensagem_webhook({"event": "message", "payload": m}, is_sync=True)
            return {"status": f"Histórico Sincronizado! ({len(msgs)} lidas)"}
        else:
            raise Exception(f"HTTP {r.status_code}: {r.text}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# --------- ROTA DO RELATÓRIO / LISTA DE CAMINHÕES ---------
@app.get("/api/disponiveis")
async def listar_disponiveis(dia: str = None, current_user: dict = Depends(get_current_user)):
    if not dia: dia = date.today().strftime("%Y-%m-%d")
    conn = get_db_connection()
    veiculos = conn.execute("SELECT * FROM veiculos WHERE data_operacao = ?", (dia,)).fetchall()
    conn.close()
    return [dict(v) for v in veiculos]

LAST_WEBHOOK_TIME = "Nenhum evento detectado desde o último reinício."

# --------- ROTA DE WEBHOOK (EVOLUTION API / WAHA API) ---------
@app.post("/webhook/evolution")
async def webhook_evolution(request: Request):
    global LAST_WEBHOOK_TIME
    try:
        payload = await request.json()
        evento = payload.get("event", "desconhecido")
        LAST_WEBHOOK_TIME = f"Recebido hoje às {datetime.now(timezone(timedelta(hours=-3))).strftime('%H:%M:%S')} (Tipo: {evento})"
        
        # Aceita Evolution (messages.upsert) e WAHA (message / message.any)
        if evento == "messages.upsert" or str(evento).startswith("message"):
            if "revoke" in evento or evento == "messages.delete":
                processar_mensagem_apagada(payload, is_waha=str(evento).startswith("message"))
            else:
                processar_mensagem_webhook(payload)
                
        return {"status": "ok"}
    except Exception as e:
        return {"status": "erro", "detalhe": str(e)}

def processar_mensagem_apagada(payload, is_waha):
    conn = get_db_connection()
    try:
        if is_waha:
            data = payload.get("payload", {})
            msg_id = data.get("id") or data.get("messageId")
            if msg_id: conn.execute("DELETE FROM veiculos WHERE message_id = ?", (msg_id,))
        else:
            keys = payload.get("data", {}).get("keys", [])
            for key in keys:
                msg_id = key.get("id")
                if msg_id: conn.execute("DELETE FROM veiculos WHERE message_id = ?", (msg_id,))
        conn.commit()
    finally:
        conn.close()

CACHE_GRUPOS = {}
def obter_nome_grupo(jid, config):
    if jid in CACHE_GRUPOS: return CACHE_GRUPOS[jid]
    if not config.get('evo_url') or not config.get('evo_instance'):
        return f"Grupo ({jid.split('@')[0][-4:]})"
    try:
        # A API WAHA usa essa rota para extrair os Info do Grupo
        url = f"{config['evo_url'].rstrip('/')}/api/groups/{jid}?session={config['evo_instance']}"
        headers = {}
        if config.get('evo_apikey'):
            headers["X-Api-Key"] = config['evo_apikey']
            
        resp = requests.get(url, headers=headers, timeout=5)
        if resp.ok:
            data = resp.json()
            nome = data.get('name') or data.get('subject')
            if nome:
                CACHE_GRUPOS[jid] = nome
                return nome
    except:
        pass
    return f"Grupo ({jid.split('@')[0][-4:]})"

def enviar_reposta(jid, texto, config):
    if not config.get('evo_url') or not config.get('evo_instance'): return
    
    url = config['evo_url'].rstrip('/')
    session = config['evo_instance']
    key = config.get('evo_apikey')
    
    # Tenta disparar resposta para WAHA ou fallback pra Evolution
    try:
        req_url = f"{url}/api/sendText"
        headers = {"accept": "application/json", "Content-Type": "application/json"}
        if key: headers["X-Api-Key"] = key
        payload = {"session": session, "chatId": jid, "text": texto}
        r = requests.post(req_url, headers=headers, json=payload, timeout=5)
        # Se 404, provável que seja url da Evolution API original
        if r.status_code == 404:
            req_url = f"{url}/message/sendText/{session}"
            headers = {"apikey": key, "Content-Type": "application/json"}
            payload = {"number": jid, "text": texto}
            requests.post(req_url, headers=headers, json=payload, timeout=5)
    except:
        pass

import json
def analisar_mensagem_com_ia(texto, config):
    api_key = config.get("llm_api_key")
    if not api_key: return None, None
    model = config.get("llm_model") or "google/gemini-2.5-flash-lite-preview"
    
    url = "https://openrouter.ai/api/v1/chat/completions"
    prompt = f"""Você extrai dados de mensagens de motoristas de caminhão.
O motorista irá informar sobre o veículo, placa ou seu status ("disponível" ou "indisponível").
Responda APENAS com um objeto JSON válido (sem markdown de formatação) com as chaves:
"status": "Disponível" OU "Indisponível" (caso a mensagem não seja sobre disponibilidade, coloque null).
"placa": a placa com 7 digitos limpos, ex: "ABC1234" ou "PZH0000". Caso a pessoa mande só 3 letras isoladas parecendo ser a placa (ex: "estou disp PZH"), coloque as 3 letras na placa. Se não houver placa, retorne null.

Mensagem do Motorista: "{texto}"
JSON:"""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"}
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=12)
        if r.ok:
            data = r.json()
            # Limpa qualquer bloco markdown que gemini coloque qnd response_format type=json não é tão obedecido
            txt = data["choices"][0]["message"]["content"].strip()
            if txt.startswith("```json"): txt = txt[7:-3].strip()
            if txt.startswith("```"): txt = txt[3:-3].strip()
            js = json.loads(txt)
            st = js.get("status")
            pl = js.get("placa")
            if isinstance(pl, str): pl = pl.strip().upper().replace(" ", "").replace("-", "")
            return st, pl
    except Exception as e:
        print("Erro na IA:", e)
    return None, None

def processar_mensagem_webhook(payload: dict, is_sync: bool = False):
    conn = get_db_connection()
    config = dict(conn.execute("SELECT * FROM config LIMIT 1").fetchone() or {})
    conn.close()
    
    is_waha = "payload" in payload and str(payload.get("event", "")).startswith("message")
    
    if is_waha:
        data = payload.get("payload", {})
        if data.get("fromMe", False): return
        remote_jid = data.get("from", "")
        texto_original = data.get("body", "")
        timestamp_msg = data.get("timestamp")
        
        telefone_bruto = data.get("author") or data.get("participant") or remote_jid
        _meta = data.get("_data", {})
        motorista = _meta.get("notifyName") or data.get("pushName") or "Desconhecido"
        message_id = data.get("id", "")
    else:
        data = payload.get("data", {})
        remote_jid = data.get("key", {}).get("remoteJid", "")
        if data.get("key", {}).get("fromMe", False): return
        
        message_content = data.get("message", {})
        
        # Checa se Evolution mandou revogação encrustada (ProtocolMessage)
        if "protocolMessage" in message_content and message_content["protocolMessage"].get("type") == "REVOKE":
            msg_id = message_content["protocolMessage"].get("key", {}).get("id")
            if msg_id:
                conn.execute("DELETE FROM veiculos WHERE message_id = ?", (msg_id,))
                conn.commit()
            return
            
        texto_original = message_content.get("conversation", message_content.get("extendedTextMessage", {}).get("text", ""))
        
        telefone_bruto = data.get("participant") or data.get("key", {}).get("participant", "") or remote_jid
        if "sender" in data:
            telefone_bruto = data["sender"]
        timestamp_msg = data.get("messageTimestamp")
        motorista = data.get("pushName", "Desconhecido")
        message_id = data.get("key", {}).get("id", "")
        
    if not texto_original or not remote_jid: return
    
    # Se o admin ativou a IA (llm_api_key presente), a IA toma o controle da extração
    
    status_ia, placa_ia = analisar_mensagem_com_ia(texto_original, config)
    if config.get("llm_api_key"):
        # Usa totalmente a IA se estiver configurada.
        if not status_ia: return # IA disse que não é mensagem de status
        status_veiculo = status_ia
        placa = placa_ia or ""
    else:
        # ---- HEURÍSTICA LEGADA (SEM IA) ----
        texto_lower = texto_original.lower()
        if "indisponivel" in texto_lower or "indisponível" in texto_lower:
            status_veiculo = "Indisponível"
        elif "disponivel" in texto_lower or "disponível" in texto_lower:
            status_veiculo = "Disponível"
        else:
            regex_disp = re.compile(config.get("palavra_chave", "dispon[ií]vel"), re.IGNORECASE)
            if not regex_disp.search(texto_original): return

        # 1. Busca padrão forte: 3 letras e 4 caracteres após, ignorando traços ou espaços e tolerando o virando 0
        padrao_forte = re.compile(r"\b([A-Za-z]{3})[-\s]*([A-Za-z0-9]{4})\b")
        placas = padrao_forte.findall(texto_original)
        
        for p_letra, p_num in placas:
            p_num_corrigido = p_num.replace('o', '0').replace('O', '0')
            if any(char.isdigit() for char in p_num_corrigido):
                placa = (p_letra + p_num_corrigido).upper()
                break
                
        if not placa:
            tres_letras = re.findall(r"\b([a-zA-Z]{3})\b", texto_original)
            blacklist = ["bom", "boa", "por", "com", "que", "pra", "uma", "dia", "não", "nao", "sim", "das", "dos", "nas", "nos", "tem", "foi", "vai", "vou", "fui", "vem", "seu"]
            tres_letras_validas = [p for p in tres_letras if p.lower() not in blacklist]
            if tres_letras_validas:
                placa = tres_letras_validas[0].upper()
    # ----------------------------------------
            
    if not placa:
        # 3. Responde exigindo a placa se o admin escreveu uma mensagem pra isso. Do contrário, usa default
        if not is_sync:
            msg_alerta = config.get("msg_erro_placa", "⚠️ Ops, faltou uma informação!\nPara registrar corretamente seu status na Giannone, mande novamente a mensagem e *informe a PLACA completa* (ou 3 primeiras letras) junto com seu aviso.")
            if msg_alerta:
                enviar_reposta(remote_jid, msg_alerta, config)
        return
    telefone = telefone_bruto.split("@")[0].split(":")[0]  
    
    # ------------------ PEGA O NOME REAL DO GRUPO (Evolution API / WAHA) ------------------
    if "@g.us" in remote_jid:
        grupo = obter_nome_grupo(remote_jid, config)
    else:
        grupo = "Chat Privado"
    
    agora_sp = datetime.now(timezone(timedelta(hours=-3)))
    timestamp_msg = data.get("messageTimestamp", int(agora_sp.timestamp()))
    dt_hora = datetime.fromtimestamp(timestamp_msg, tz=timezone(timedelta(hours=-3)))
    
    data_operacao = dt_hora.strftime("%Y-%m-%d")
    horario_mensagem = dt_hora.strftime("%H:%M:%S")
    
    # Abre nova conexão para salvar e garante fechamento
    conn = get_db_connection()
    try:
        # Verifica se já mandou hoje e atualiza, senão insere
        existente = conn.execute("SELECT id FROM veiculos WHERE data_operacao=? AND telefone=?", (data_operacao, telefone)).fetchone()
        
        if existente:
            conn.execute("UPDATE veiculos SET placa=?, grupo=?, horario_mensagem=?, mensagem_original=?, status=?, message_id=? WHERE id=?", 
                         (placa, grupo, horario_mensagem, texto_original, status_veiculo, message_id, existente["id"]))
        else:
            conn.execute("INSERT INTO veiculos (data_operacao, motorista, telefone, placa, grupo, horario_mensagem, mensagem_original, status, message_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (data_operacao, motorista, telefone, placa, grupo, horario_mensagem, texto_original, status_veiculo, message_id))
        
        conn.commit()
    except Exception as e:
        print("Erro SQL", e)
    finally:
        conn.close()

# --------- ROTA CRUD ADMIN (DELETAR / EDITAR GRUPOS E VEICULOS) ---------
@app.delete("/api/veiculos/{veiculo_id}")
async def deletar_veiculo(veiculo_id: int, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != "admin": raise HTTPException(status_code=403)
    conn = get_db_connection()
    conn.execute("DELETE FROM veiculos WHERE id = ?", (veiculo_id,))
    conn.commit()
    conn.close()
    return {"status": "ok"}

@app.delete("/api/grupos/{nome_grupo}")
async def deletar_grupo(nome_grupo: str, dia: str = None, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != "admin": raise HTTPException(status_code=403)
    if not dia: dia = date.today().strftime("%Y-%m-%d")
    conn = get_db_connection()
    conn.execute("DELETE FROM veiculos WHERE grupo = ? AND data_operacao = ?", (nome_grupo, dia))
    conn.commit()
    conn.close()
    return {"status": "ok"}

@app.put("/api/grupos/{nome_grupo}")
async def renomear_grupo(nome_grupo: str, request: Request, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != "admin": raise HTTPException(status_code=403)
    dados = await request.json()
    novo_nome = dados.get("novo_nome")
    if not novo_nome: raise HTTPException(status_code=400)
    conn = get_db_connection()
    conn.execute("UPDATE veiculos SET grupo = ? WHERE grupo = ?", (novo_nome, nome_grupo))
    conn.commit()
    conn.close()
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
