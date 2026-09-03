import sqlite3
import re
import csv
import io
from datetime import datetime, date, timezone, timedelta
from fastapi import FastAPI, Request, Depends, HTTPException, status
from fastapi.responses import Response
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import os
import jwt
from models import ConfigUpdate
from database import init_db, get_db_connection, USE_POSTGRES

# Configurações de Segurança - via env (EasyPanel > Environment)
SECRET_KEY = os.getenv("SECRET_KEY", os.getenv("JWT_SECRET", "GIANNONE_SUPER_SECRET"))
ALGORITHM = "HS256"
if SECRET_KEY == "GIANNONE_SUPER_SECRET":
    print("AVISO: SECRET_KEY usando valor padrão! Configure SECRET_KEY no EasyPanel > Environment para produção.")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

app = FastAPI(title="Agente Operacional Giannone Transportes")

# Inicializa Banco de Dados
init_db()

# Montando Arquivos Estáticos e Templates (Frontend)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Funções Auxiliares de Auth (DB vem de database.py)
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
        "UPDATE config SET palavra_chave = ?, regex_placa = ?, evo_url = ?, evo_instance = ?, evo_apikey = ?, msg_erro_placa = ?, llm_api_key = ?, llm_model = ?, llm_base_url = ?", 
        (config_data.palavra_chave, config_data.regex_placa, config_data.evo_url, config_data.evo_instance, config_data.evo_apikey, config_data.msg_erro_placa, config_data.llm_api_key, config_data.llm_model, config_data.llm_base_url)
    )
    conn.commit()
    conn.close()
    return {"status": "Configurações atualizadas com sucesso!"}

from pydantic import BaseModel
class SyncData(BaseModel):
    meu_link: str

class ChatRequest(BaseModel):
    pergunta: str

import requests
@app.post("/api/evolution/sync")
async def sync_evolution(data: SyncData, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != "admin": raise HTTPException(status_code=403)
    conn = get_db_connection()
    config = dict(conn.execute("SELECT * FROM config LIMIT 1").fetchone())
    conn.close()
    if not config.get('evo_url') or not config.get('evo_instance'):
        raise HTTPException(status_code=400, detail="Configure a URL e Instância/Sessão WAHA primeiro.")
    
    base_url = config['evo_url'].rstrip('/')
    session = config['evo_instance']
    api_key = config.get('evo_apikey', '')
    webhook_url = f"{data.meu_link.rstrip('/')}/webhook/evolution"
    
    # Tenta WAHA primeiro (PUT /api/sessions/{name})
    try:
        waha_url = f"{base_url}/api/sessions/{session}"
        headers = {"accept": "application/json", "Content-Type": "application/json"}
        if api_key: headers["X-Api-Key"] = api_key
        payload = {
            "config": {
                "webhooks": [{
                    "url": webhook_url,
                    "events": ["message", "message.any"]
                }]
            }
        }
        r = requests.put(waha_url, json=payload, headers=headers, timeout=10)
        if r.ok:
            return {"status": f"Webhook WAHA configurado! Apontando para: {webhook_url}"}
    except:
        pass
    
    # Fallback: tenta Evolution API (POST /webhook/set/{instance})
    try:
        evo_url = f"{base_url}/webhook/set/{session}"
        headers = {"Content-Type": "application/json"}
        if api_key: headers["apikey"] = api_key
        payload = {
            "webhook": {
                "enabled": True,
                "url": webhook_url,
                "webhookByEvents": False,
                "webhookBase64": False,
                "events": ["MESSAGES_UPSERT"]
            }
        }
        r = requests.post(evo_url, json=payload, headers=headers, timeout=10)
        r.raise_for_status()
        return {"status": f"Webhook Evolution configurado! Apontando para: {webhook_url}"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Falha ao configurar webhook: {str(e)}")

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
    except Exception as e:
        # Captura tanto sqlite3.IntegrityError quanto psycopg2.errors.UniqueViolation
        if "UNIQUE" in str(e).upper() or "unique" in str(e).lower() or "already exists" in str(e).lower():
            raise HTTPException(status_code=400, detail="Usuário já existe.")
        raise
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
    
    base = config['evo_url'].rstrip('/')
    session = config['evo_instance']
    h = {"accept": "application/json"}
    if config.get('evo_apikey'): h["X-Api-Key"] = config['evo_apikey']
    
    total_processadas = 0
    erros = []
    
    try:
        # 1. Busca lista de chats (grupos @g.us) - tenta múltiplos endpoints (CORE/PLUS/PRO)
        import urllib.parse
        chats = None
        last_err = ""
        chat_urls = [
            f"{base}/api/{session}/chats?limit=50&sortBy=conversationTimestamp&sortOrder=desc",
            f"{base}/api/{session}/chats/overview?limit=50",
            f"{base}/api/{session}/chats?limit=100",
            f"{base}/api/sessions/{session}/chats?limit=50",
            f"{base}/api/chats?session={session}&limit=50",
        ]
        for chats_url in chat_urls:
            try:
                r = requests.get(chats_url, headers=h, timeout=15)
                if r.ok:
                    j = r.json()
                    # WAHA PLUS pode retornar {data: [...]} ou lista direta ou {chats: [...]}
                    if isinstance(j, list):
                        chats = j
                    elif isinstance(j, dict):
                        chats = j.get("data") or j.get("chats") or j.get("items") or []
                        if not chats and len(j) > 0 and "id" in j:
                            chats = [j]  # resposta single chat
                    if chats is not None and len(chats) >= 0:
                        break
                last_err = f"{chats_url} -> HTTP {r.status_code}: {r.text[:120]}"
            except Exception as ex:
                last_err = f"{chats_url} -> {ex}"
                continue
        if chats is None:
            raise Exception(f"Nenhum endpoint de chats respondeu. Último erro: {last_err} | Base: {base} Session: {session} (verifique se a sessão existe e está STARTED na WAHA)")
        if isinstance(chats, dict): chats = chats.get("data", [])
        
        # Helper para extrair ID string (WAHA PLUS retorna id como dict)
        def _extract_id(c):
            v = c.get("id", "")
            if isinstance(v, dict):
                return v.get("_serialized") or v.get("id") or v.get("value") or str(v)
            return str(v)
        def _extract_name(c):
            for k in ("name","subject","topic","formattedTitle","pushName"):
                v = c.get(k)
                if isinstance(v, str) and v.strip(): return v.strip()
            return ""
        # Filtra apenas grupos
        grupo_chats = [c for c in chats if "@g.us" in _extract_id(c)]
        
        # Timestamp de 24h atrás
        agora = datetime.now(timezone(timedelta(hours=-3)))
        limite_24h = int((agora - timedelta(hours=24)).timestamp())
        
        # 2. Para cada grupo, busca mensagens recentes
        for chat in grupo_chats:
            chat_id = _extract_id(chat)
            if not chat_id: continue

            # Preenche nome do grupo se disponível no objeto do chat
            chat_name = _extract_name(chat)
            if chat_name:
                nome_limpo = chat_name.strip()
                CACHE_GRUPOS[chat_id] = nome_limpo
                sufixo_4 = chat_id.split('@')[0][-4:]
                try:
                    conn_sync = get_db_connection()
                    conn_sync.execute(
                        "UPDATE veiculos SET grupo = ? WHERE grupo = ? OR grupo LIKE ?", 
                        (nome_limpo, f"Grupo ({sufixo_4})", f"%({sufixo_4})%")
                    )
                    conn_sync.commit()
                    conn_sync.close()
                except Exception: pass
            
            try:
                enc_id = urllib.parse.quote(chat_id, safe='')
                # Tenta 2 variantes de endpoint de mensagens
                msgs = None
                for msgs_url in [
                    f"{base}/api/{session}/chats/{enc_id}/messages?limit=100&sortOrder=desc",
                    f"{base}/api/{session}/chats/{enc_id}/messages?limit=100",
                    f"{base}/api/{session}/chats/{chat_id}/messages?limit=100&sortOrder=desc",
                ]:
                    try:
                        r2 = requests.get(msgs_url, headers=h, timeout=15)
                        if r2.ok:
                            j2 = r2.json()
                            if isinstance(j2, dict): msgs = j2.get("data") or j2.get("messages") or []
                            else: msgs = j2
                            break
                        else:
                            last_msg_err = f"HTTP {r2.status_code}"
                    except Exception as ex2:
                        last_msg_err = str(ex2)[:80]
                        continue
                if msgs is None or not isinstance(msgs, list):
                    if isinstance(msgs, dict): msgs = msgs.get("data", []) or []
                    if msgs is None:
                        erros.append(f"{chat_id}: {last_msg_err if 'last_msg_err' in locals() else 'sem resposta'}")
                        continue
                
                for m in msgs:
                    # Filtra apenas últimas 24h
                    ts = m.get("timestamp", 0)
                    if ts and ts < limite_24h: continue
                    
                    # Simula payload de webhook WAHA
                    processar_mensagem_webhook({"event": "message", "payload": m}, is_sync=True)
                    total_processadas += 1
                    
            except Exception as ex:
                erros.append(f"{chat_id}: {str(ex)[:80]}")
                continue
        
        resultado = f"Histórico sincronizado! {total_processadas} mensagens processadas de {len(grupo_chats)} grupos."
        if erros:
            resultado += f" ({len(erros)} erros em chats específicos)"
        return {"status": resultado}
        
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
# Log de debug para webhooks (mantém os últimos 30 eventos)
WEBHOOK_LOG = []
# Log de processamento de mensagens (mantém os últimos 30)
PROCESS_LOG = []

# --------- ROTA DE WEBHOOK (EVOLUTION API / WAHA API) ---------
@app.post("/webhook/evolution")
async def webhook_evolution(request: Request):
    global LAST_WEBHOOK_TIME
    try:
        payload = await request.json()
        evento = payload.get("event", "desconhecido")
        agora_str = datetime.now(timezone(timedelta(hours=-3))).strftime('%H:%M:%S')
        LAST_WEBHOOK_TIME = f"Recebido hoje às {agora_str} (Tipo: {evento})"
        
        # Salva no log de debug (máximo 30 entradas)
        log_entry = {
            "hora": agora_str,
            "evento": evento,
            "tem_payload": "payload" in payload,
            "tem_data": "data" in payload,
            "keys": list(payload.keys()),
        }
        # Tenta extrair info básica da mensagem
        if "payload" in payload:
            p = payload["payload"]
            log_entry["from"] = p.get("from", "")
            log_entry["body_preview"] = (p.get("body", "") or "")[:80]
            log_entry["fromMe"] = p.get("fromMe", None)
        elif "data" in payload:
            d = payload["data"]
            log_entry["remoteJid"] = d.get("key", {}).get("remoteJid", "")
            msg_content = d.get("message", {})
            log_entry["body_preview"] = (msg_content.get("conversation", "") or msg_content.get("extendedTextMessage", {}).get("text", ""))[:80]
        
        WEBHOOK_LOG.append(log_entry)
        if len(WEBHOOK_LOG) > 30: WEBHOOK_LOG.pop(0)
        
        # Aceita Evolution (messages.upsert) e WAHA (message / message.any)
        if evento == "messages.upsert" or str(evento).startswith("message"):
            if "revoke" in evento or evento == "messages.delete":
                processar_mensagem_apagada(payload, is_waha=str(evento).startswith("message"))
            else:
                processar_mensagem_webhook(payload)
                
        return {"status": "ok"}
    except Exception as e:
        return {"status": "erro", "detalhe": str(e)}

# --------- ROTA DE DEBUG: LOG DE WEBHOOKS ---------
@app.get("/api/webhook/log")
async def get_webhook_log(current_user: dict = Depends(get_current_user)):
    return {"last_hook": LAST_WEBHOOK_TIME, "log": list(reversed(WEBHOOK_LOG)), "process_log": list(reversed(PROCESS_LOG))}

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
def obter_nome_grupo(jid: str, config: dict, payload: dict = None) -> str:
    if not jid or "@g.us" not in jid:
        return "Chat Privado"
        
    if jid in CACHE_GRUPOS:
        return CACHE_GRUPOS[jid]

    sufixo_4 = jid.split('@')[0][-4:]
    nome_padrao = f"Grupo ({sufixo_4})"

    # 1. Tenta extrair o nome do grupo DIRETAMENTE do payload do webhook
    if payload and isinstance(payload, dict):
        p_data = payload.get("payload") if isinstance(payload.get("payload"), dict) else payload.get("data", {})
        if not isinstance(p_data, dict): p_data = {}
        
        nome_payload = (
            payload.get("_data", {}).get("chat", {}).get("name") or
            p_data.get("_data", {}).get("chat", {}).get("name") or
            payload.get("chat", {}).get("name") or
            p_data.get("chat", {}).get("name") or
            payload.get("_data", {}).get("name") or
            p_data.get("_data", {}).get("name") or
            payload.get("groupInfo", {}).get("subject") or
            p_data.get("groupInfo", {}).get("subject") or
            payload.get("groupMetadata", {}).get("subject") or
            p_data.get("groupMetadata", {}).get("subject") or
            payload.get("group", {}).get("name") or
            p_data.get("group", {}).get("name") or
            payload.get("name") or
            p_data.get("name")
        )
        
        if nome_payload and isinstance(nome_payload, str) and nome_payload.strip() and not nome_payload.endswith("@g.us"):
            nome_limpo = nome_payload.strip()
            CACHE_GRUPOS[jid] = nome_limpo
            try:
                conn = get_db_connection()
                conn.execute("UPDATE veiculos SET grupo = ? WHERE grupo = ?", (nome_limpo, nome_padrao))
                conn.commit()
                conn.close()
            except Exception: pass
            return nome_limpo

    # 2. Busca via API HTTP da WAHA (/api/{session}/chats/{jid})
    if not config.get('evo_url') or not config.get('evo_instance'):
        return nome_padrao
        
    base_url = config['evo_url'].rstrip('/')
    session = config['evo_instance']
    api_key = (config.get('evo_apikey') or "").strip()
    
    headers_waha = {"accept": "application/json"}
    if api_key:
        headers_waha["X-Api-Key"] = api_key
        headers_waha["apikey"] = api_key

    # Endpoints oficiais da documentação WAHA:
    urls = [
        f"{base_url}/api/{session}/chats/{jid}",
        f"{base_url}/api/chats/{jid}?session={session}",
        f"{base_url}/api/groups/{jid}?session={session}",
        f"{base_url}/group/findGroupInfos/{session}?groupJid={jid}"
    ]
    
    for u in urls:
        try:
            r = requests.get(u, headers=headers_waha, timeout=4)
            if r.ok:
                data = r.json()
                if isinstance(data, dict):
                    nome = data.get('name') or data.get('subject') or data.get('topic')
                    if nome and isinstance(nome, str) and nome.strip() and not nome.endswith("@g.us"):
                        nome_limpo = nome.strip()
                        CACHE_GRUPOS[jid] = nome_limpo
                        try:
                            conn = get_db_connection()
                            conn.execute("UPDATE veiculos SET grupo = ? WHERE grupo = ?", (nome_limpo, nome_padrao))
                            conn.commit()
                            conn.close()
                        except Exception: pass
                        return nome_limpo
        except Exception:
            pass

    return nome_padrao

@app.post("/api/waha/sync_grupos")
async def sync_grupos_waha(current_user: dict = Depends(get_current_user)):
    if current_user["role"] != "admin": raise HTTPException(status_code=403)
    conn = get_db_connection()
    config = dict(conn.execute("SELECT * FROM config LIMIT 1").fetchone() or {})
    conn.close()
    
    if not config.get('evo_url') or not config.get('evo_instance'):
        raise HTTPException(status_code=400, detail="WAHA não configurada.")
        
    base_url = config['evo_url'].rstrip('/')
    session = config['evo_instance']
    api_key = (config.get('evo_apikey') or "").strip()
    
    headers = {"accept": "application/json"}
    if api_key:
        headers["X-Api-Key"] = api_key
        headers["apikey"] = api_key
        
    urls = [
        f"{base_url}/api/{session}/chats?limit=100",
        f"{base_url}/api/chats?session={session}&limit=100"
    ]
    
    atualizados = 0
    conn = get_db_connection()
    try:
        for u in urls:
            try:
                r = requests.get(u, headers=headers, timeout=8)
                if r.ok:
                    chats = r.json()
                    if isinstance(chats, dict): chats = chats.get("data", [])
                    if isinstance(chats, list):
                        for c in chats:
                            jid = c.get("id", "")
                            nome = c.get("name") or c.get("subject")
                            if jid and "@g.us" in jid and nome and not nome.endswith("@g.us"):
                                nome_limpo = nome.strip()
                                CACHE_GRUPOS[jid] = nome_limpo
                                sufixo = jid.split('@')[0][-4:]
                                cursor = conn.execute(
                                    "UPDATE veiculos SET grupo = ? WHERE grupo = ? OR grupo LIKE ?", 
                                    (nome_limpo, f"Grupo ({sufixo})", f"%({sufixo})%")
                                )
                                atualizados += cursor.rowcount
                        conn.commit()
                        break
            except Exception as ex:
                print("Erro ao atualizar grupos WAHA:", ex)
    finally:
        conn.close()
        
    return {"status": "ok", "grupos_atualizados": atualizados}

def enviar_reposta(jid, texto, config):
    if not config.get('evo_url') or not config.get('evo_instance'):
        print("Auto-resposta não enviada: WAHA/Evolution não configurados.")
        return False
    
    url = config['evo_url'].rstrip('/')
    session = config['evo_instance']
    key = (config.get('evo_apikey') or "").strip()
    
    chat_id = jid
    if "@" not in chat_id:
        chat_id = f"{chat_id}@c.us"
    
    # 1. Tenta disparar resposta para WAHA API (POST /api/sendText)
    try:
        req_url = f"{url}/api/sendText"
        headers = {"accept": "application/json", "Content-Type": "application/json"}
        if key:
            headers["X-Api-Key"] = key
            headers["apikey"] = key
        payload = {"session": session, "chatId": chat_id, "text": texto}
        r = requests.post(req_url, headers=headers, json=payload, timeout=6)
        if r.ok:
            print(f"Auto-resposta WAHA enviada com sucesso para {chat_id}")
            return True
        else:
            print(f"WAHA sendText HTTP {r.status_code}: {r.text[:150]}")
    except Exception as e:
        print("Erro WAHA sendText:", e)

    # 2. Fallback para Evolution API (POST /message/sendText/{session})
    try:
        req_url = f"{url}/message/sendText/{session}"
        headers = {"Content-Type": "application/json"}
        if key:
            headers["apikey"] = key
        payload = {"number": jid, "text": texto}
        r = requests.post(req_url, headers=headers, json=payload, timeout=6)
        if r.ok:
            print(f"Auto-resposta Evolution enviada com sucesso para {jid}")
            return True
        else:
            print(f"Evolution sendText HTTP {r.status_code}: {r.text[:150]}")
    except Exception as e:
        print("Erro Evolution sendText:", e)

    return False

import json

def parse_llm_response(text: str) -> str:
    text = text.strip()
    if not text:
        raise ValueError("Resposta vazia da IA")
    
    # Se o servidor enviou stream SSE (data: ...)
    if "data:" in text:
        chunks = []
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                payload_str = line[5:].strip()
                if payload_str and payload_str != "[DONE]":
                    try:
                        obj = json.loads(payload_str)
                        choice = obj.get("choices", [{}])[0]
                        # tenta delta, message, text
                        chunk = choice.get("delta", {}).get("content")
                        if chunk is None: chunk = choice.get("message", {}).get("content")
                        if chunk is None: chunk = choice.get("text", "")
                        if chunk:
                            chunks.append(str(chunk))
                    except Exception:
                        pass
        if chunks:
            joined = "".join(chunks).strip()
            if joined: return joined

    # Tenta parsing JSON padrão ou raw_decode (para objetos JSON concatenados / extra data)
    try:
        data = json.loads(text)
    except Exception:
        try:
            data, _ = json.JSONDecoder().raw_decode(text)
        except Exception as e:
            # Se não é JSON, retorna texto puro (pode ser resposta direta)
            if len(text) > 5 and "choices" not in text.lower():
                return text
            raise ValueError(f"Resposta inválida da IA: {text[:300]}")
            
    def _content_to_str(v):
        # content pode ser string, lista de blocos [{"type":"text","text":"..."}] ou None
        if v is None:
            return ""
        if isinstance(v, str):
            return v
        if isinstance(v, list):
            parts = []
            for b in v:
                if isinstance(b, str):
                    parts.append(b)
                elif isinstance(b, dict):
                    # OpenRouter/Anthropic style: {"type":"text","text":"..."}
                    for k in ("text", "content", "value"):
                        if isinstance(b.get(k), str) and b[k].strip():
                            parts.append(b[k])
                            break
            return "".join(parts)
        return str(v)

    if isinstance(data, dict):
        # Alguns provedores retornam {"response": "..."} ou {"content": "..."}
        if not data.get("choices") and data.get("response"):
            return str(data["response"])
        if not data.get("choices") and data.get("content"):
            return str(data["content"])
        # Ollama style: {"message": {"content": "..."}}
        if not data.get("choices") and isinstance(data.get("message"), dict):
            t = _content_to_str(data["message"].get("content")).strip()
            if t: return t
        choices = data.get("choices", [])
        if choices:
            c = choices[0]
            if isinstance(c, dict):
                for key in ("message", "delta"):
                    m = c.get(key, {})
                    if isinstance(m, dict):
                        t = _content_to_str(m.get("content")).strip()
                        if t: return t
                        # alguns modelos (ex: deepseek, muse-spark via router)
                        # colocam a resposta em reasoning_content/reasoning
                        t = _content_to_str(m.get("reasoning_content")).strip()
                        if t: return t
                        t = _content_to_str(m.get("reasoning")).strip()
                        if t: return t
                if c.get("text"):
                    t = _content_to_str(c.get("text")).strip()
                    if t: return t
                # fallback para qualquer campo string no choice
                for k in ("content", "text", "response", "output_text", "output"):
                    if c.get(k):
                        t = _content_to_str(c.get(k)).strip()
                        if t: return t
                # choice existe mas veio com content vazio/nulo -> erro explicativo
                # (antes caía no "Formato inesperado" genérico)
                raise ValueError(
                    f"IA retornou choice sem conteúdo (model={data.get('model')} "
                    f"finish_reason={c.get('finish_reason')} keys={list(c.keys())}): {text[:500]}"
                )
    raise ValueError(f"Formato de resposta inesperado da IA: {text[:500]}")

def get_llm_url(config):
    base = (config.get("llm_base_url") or "").strip().rstrip("/")
    if not base: 
        base = "https://integrate.api.nvidia.com/v1"
    
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"

def analisar_mensagem_com_ia(texto, config):
    api_key = (config.get("llm_api_key") or "").strip()
    if not api_key: return []
    model = (config.get("llm_model") or "").strip() or "meta/llama-3.3-70b-instruct"
    
    url = get_llm_url(config)
    prompt = (
        "Você extrai dados de mensagens de motoristas de caminhão.\n"
        "O motorista pode informar sobre um ou mais veículos, placas e status (\"disponível\" ou \"indisponível\").\n"
        "Responda APENAS com um array JSON válido (sem markdown de formatação) de objetos com as chaves:\n"
        "\"status\": \"Disponível\" OU \"Indisponível\" (caso a mensagem não seja sobre disponibilidade, coloque null).\n"
        "\"placa\": a placa com 7 digitos limpos, ex: \"ABC1234\" ou \"PZH0000\". Caso a pessoa mande só 3 letras isoladas parecendo ser a placa (ex: \"estou disp PZH\"), coloque as 3 letras na placa. Se não houver placa, retorne null.\n\n"
        "Exemplo de resposta para 2 veículos:\n"
        "[{\"status\": \"Disponível\", \"placa\": \"ABC1234\"}, {\"status\": \"Indisponível\", \"placa\": \"XYZ9876\"}]\n\n"
        f"Mensagem do Motorista: \"{texto}\"\n"
        "JSON:"
    )
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "stream": False
    }
    resultados = []
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=12)
        if r.ok:
            txt = parse_llm_response(r.text).strip()
            if txt.startswith("```json"): txt = txt[7:-3].strip()
            if txt.startswith("```"): txt = txt[3:-3].strip()
            js = json.loads(txt)
            if isinstance(js, dict):
                js = [js]
            if isinstance(js, list):
                for item in js:
                    if isinstance(item, dict):
                        st = item.get("status")
                        pl = item.get("placa")
                        if isinstance(pl, str): pl = pl.strip().upper().replace(" ", "").replace("-", "")
                        if st or pl:
                            resultados.append({"status": st, "placa": pl})
            return resultados
        else:
            print(f"Erro na IA HTTP {r.status_code} ({url}): {r.text[:200]}")
    except Exception as e:
        print(f"Erro de Conexão na IA ({url}):", e)
    return []

def processar_mensagem_webhook(payload: dict, is_sync: bool = False):
    conn = get_db_connection()
    config = dict(conn.execute("SELECT * FROM config LIMIT 1").fetchone() or {})
    conn.close()
    
    is_waha = "payload" in payload and str(payload.get("event", "")).startswith("message")
    
    if is_waha:
        data = payload.get("payload", {})
        if data.get("fromMe", False): return
        remote_jid = data.get("from", "")
        texto_original = (
            data.get("body") or 
            data.get("caption") or 
            data.get("_data", {}).get("caption") or 
            data.get("_data", {}).get("body") or 
            data.get("text") or 
            ""
        )
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
            
        texto_original = (
            message_content.get("conversation") or 
            message_content.get("extendedTextMessage", {}).get("text") or 
            message_content.get("imageMessage", {}).get("caption") or 
            message_content.get("documentMessage", {}).get("caption") or 
            message_content.get("videoMessage", {}).get("caption") or 
            ""
        )
        
        telefone_bruto = data.get("participant") or data.get("key", {}).get("participant", "") or remote_jid
        if "sender" in data:
            telefone_bruto = data["sender"]
        timestamp_msg = data.get("messageTimestamp")
        motorista = data.get("pushName", "Desconhecido")
        message_id = data.get("key", {}).get("id", "")
        
    if not texto_original or not remote_jid: return
    
    # Log de processamento para debug
    log_entry = {
        "hora": datetime.now(timezone(timedelta(hours=-3))).strftime('%H:%M:%S'),
        "jid": remote_jid[:30],
        "texto": (texto_original or "")[:80],
        "motorista": motorista,
        "etapa": "inicio",
        "resultado": ""
    }
    
    # ---- EXTRAÇÃO DE STATUS E PLACAS ----
    itens_extraidos = []
    
    # Tenta IA primeiro (se configurada)
    if config.get("llm_api_key"):
        itens_ia = analisar_mensagem_com_ia(texto_original, config)
        if itens_ia:
            for item in itens_ia:
                st = item.get("status")
                pl = (item.get("placa") or "").strip().upper()
                if st and pl:  # Apenas aceita item da IA se tiver STATUS E PLACA VÁLIDA
                    itens_extraidos.append({"status": st, "placa": pl})
            if itens_extraidos:
                log_entry["resultado"] = f"IA extraiu {len(itens_extraidos)} veículo(s)"

    # Fallback para heurística (se IA não configurada OU se IA não retornou placa válida)
    if not itens_extraidos:
        status_heuristico = ""
        texto_lower = texto_original.lower()
        if "indisponivel" in texto_lower or "indisponível" in texto_lower:
            status_heuristico = "Indisponível"
        elif "disponivel" in texto_lower or "disponível" in texto_lower:
            status_heuristico = "Disponível"
        else:
            regex_disp = re.compile(config.get("palavra_chave", "dispon[ií]vel"), re.IGNORECASE)
            if not regex_disp.search(texto_original):
                log_entry["etapa"] = "descartada_heuristica"
                log_entry["resultado"] = "Palavra-chave não encontrada na mensagem"
                PROCESS_LOG.append(log_entry)
                if len(PROCESS_LOG) > 30: PROCESS_LOG.pop(0)
                return
        
        if not status_heuristico:
            status_heuristico = "Disponível"
            
        # Extração de placas via regex (busca todas na mensagem)
        placas_encontradas = []
        padrao_forte = re.compile(r"\b([A-Za-z]{3})[-\s]*([A-Za-z0-9]{4})\b")
        placas = padrao_forte.findall(texto_original)
        
        for p_letra, p_num in placas:
            p_num_corrigido = p_num.replace('o', '0').replace('O', '0').replace('i', '1').replace('I', '1').replace('l', '1').replace('L', '1')
            if any(char.isdigit() for char in p_num_corrigido):
                placas_encontradas.append((p_letra + p_num_corrigido).upper())
                
        if not placas_encontradas:
            tres_letras = re.findall(r"\b([a-zA-Z]{3})\b", texto_original)
            blacklist = ["bom", "boa", "por", "com", "que", "pra", "uma", "dia", "não", "nao", "sim", "das", "dos", "nas", "nos", "tem", "foi", "vai", "vou", "fui", "vem", "seu"]
            tres_letras_validas = [p for p in tres_letras if p.lower() not in blacklist]
            if tres_letras_validas:
                placas_encontradas.append(tres_letras_validas[0].upper())

        if placas_encontradas:
            for pl in placas_encontradas:
                itens_extraidos.append({"status": status_heuristico, "placa": pl})
        else:
            itens_extraidos.append({"status": status_heuristico, "placa": ""})

        log_entry["resultado"] = f"Heurística extraiu {len(itens_extraidos)} item(ns)"

    # Se não houve placa em nenhum item, responde pedindo a placa
    tem_alguma_placa = any(bool(it.get("placa")) for it in itens_extraidos)
    if not tem_alguma_placa:
        if not is_sync:
            msg_alerta = (config.get("msg_erro_placa") or "").strip()
            if not msg_alerta:
                msg_alerta = "⚠️ Ops, faltou uma informação!\nPara registrar corretamente seu status na Giannone, mande novamente a mensagem e *informe a PLACA completa* (ou 3 primeiras letras) junto com seu aviso."
            
            enviado = enviar_reposta(remote_jid, msg_alerta, config)
            log_entry["etapa"] = "alerta_placa"
            log_entry["resultado"] = f"Auto-resposta disparada ({'sucesso' if enviado else 'falha envio WAHA'})"
            PROCESS_LOG.append(log_entry)
            if len(PROCESS_LOG) > 30: PROCESS_LOG.pop(0)
        return

    telefone = telefone_bruto.split("@")[0].split(":")[0]  
    
    if "@g.us" in remote_jid:
        grupo = obter_nome_grupo(remote_jid, config, payload)
    else:
        grupo = "Chat Privado"
    
    agora_sp = datetime.now(timezone(timedelta(hours=-3)))
    ts_val = data.get("messageTimestamp") or data.get("timestamp")
    try:
        timestamp_msg = int(ts_val) if ts_val is not None else int(agora_sp.timestamp())
    except (ValueError, TypeError):
        timestamp_msg = int(agora_sp.timestamp())

    dt_hora = datetime.fromtimestamp(timestamp_msg, tz=timezone(timedelta(hours=-3)))
    
    data_operacao = dt_hora.strftime("%Y-%m-%d")
    horario_mensagem = dt_hora.strftime("%H:%M:%S")
    
    conn = get_db_connection()
    salvos = 0
    try:
        for item in itens_extraidos:
            placa = item.get("placa")
            status_veiculo = item.get("status", "Disponível")
            if not placa: continue

            # Chaveia por (data_operacao, telefone, placa) para permitir múltiplos veículos por motorista no mesmo dia
            existente = conn.execute(
                "SELECT id FROM veiculos WHERE data_operacao=? AND telefone=? AND placa=?", 
                (data_operacao, telefone, placa)
            ).fetchone()
            
            if existente:
                conn.execute(
                    "UPDATE veiculos SET grupo=?, horario_mensagem=?, mensagem_original=?, status=?, message_id=? WHERE id=?", 
                    (grupo, horario_mensagem, texto_original, status_veiculo, message_id, existente["id"])
                )
            else:
                conn.execute(
                    "INSERT INTO veiculos (data_operacao, motorista, telefone, placa, grupo, horario_mensagem, mensagem_original, status, message_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (data_operacao, motorista, telefone, placa, grupo, horario_mensagem, texto_original, status_veiculo, message_id)
                )
            salvos += 1
            
        conn.commit()
        log_entry["etapa"] = "salvo"
        log_entry["resultado"] = f"{salvos} veículo(s) salvo(s) para {motorista}"
        PROCESS_LOG.append(log_entry)
        if len(PROCESS_LOG) > 30: PROCESS_LOG.pop(0)
    except Exception as e:
        print("Erro SQL", e)
        log_entry["etapa"] = "erro_sql"
        log_entry["resultado"] = str(e)[:80]
        PROCESS_LOG.append(log_entry)
        if len(PROCESS_LOG) > 30: PROCESS_LOG.pop(0)
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

# --------- ROTA DE TESTE LLM ---------
@app.post("/api/llm/test")
async def test_llm(current_user: dict = Depends(get_current_user)):
    if current_user["role"] != "admin": raise HTTPException(status_code=403)
    conn = get_db_connection()
    config = dict(conn.execute("SELECT * FROM config LIMIT 1").fetchone() or {})
    conn.close()
    
    api_key = (config.get("llm_api_key") or "").strip()
    if not api_key:
        return {"status": "not_configured"}
    
    model = (config.get("llm_model") or "").strip() or "meta/llama-3.3-70b-instruct"
    url = get_llm_url(config)
    
    try:
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        base_msgs = [{"role": "user", "content": "Responda apenas com a frase: IA funcionando corretamente."}]
        payload = {
            "model": model,
            "messages": base_msgs,
            "max_tokens": 100,
            "max_completion_tokens": 100,
            "temperature": 0.7,
            "stream": False
        }
        r = requests.post(url, headers=headers, json=payload, timeout=20)
        print(f"LLM TEST url={url} model={model} status={r.status_code} raw={r.text[:800]}")
        if r.ok:
            try:
                txt = parse_llm_response(r.text).strip()
            except Exception as pe:
                # retry sem temperature e sem max_tokens (alguns modelos/router rejeitam)
                print(f"LLM TEST retry após parse falhar: {pe}")
                payload2 = {"model": model, "messages": base_msgs, "stream": False}
                r2 = requests.post(url, headers=headers, json=payload2, timeout=20)
                print(f"LLM TEST retry status={r2.status_code} raw={r2.text[:800]}")
                if not r2.ok:
                    return {"status": "error", "detail": f"HTTP {r2.status_code}: {r2.text[:300]}", "url": url}
                txt = parse_llm_response(r2.text).strip()
            return {"status": "ok", "model": model, "test_response": txt, "url": url}
        else:
            err_msg = f"HTTP {r.status_code}"
            if r.status_code == 401:
                err_msg += " (Não Autorizado: API Key inválida para este endpoint)"
            elif r.status_code == 404:
                err_msg += " (Não Encontrado: Verifique a URL Base e se o Modelo existe no provedor)"
            elif r.status_code in (400, 422):
                err_msg += f" (Requisição Rejeitada: {r.text[:180]})"
            else:
                err_msg += f": {r.text[:180]}"
            return {"status": "error", "detail": err_msg, "url": url}
    except Exception as e:
        return {"status": "error", "detail": f"Erro de Conexão ({type(e).__name__}): {str(e)}", "url": url}

# --------- ROTA DE CHAT IA (ASSISTENTE) ---------
@app.post("/api/chat")
async def chat_ia(req: ChatRequest, current_user: dict = Depends(get_current_user)):
    conn = get_db_connection()
    config = dict(conn.execute("SELECT * FROM config LIMIT 1").fetchone() or {})
    
    api_key = (config.get("llm_api_key") or "").strip()
    if not api_key:
        conn.close()
        raise HTTPException(status_code=400, detail="IA não configurada. Adicione a API Key nas Configurações.")
    
    model = (config.get("llm_model") or "").strip() or "meta/llama-3.3-70b-instruct"
    
    # ===== CONTEXTO COMPLETO DO BANCO DE DADOS =====
    hoje = date.today().strftime("%Y-%m-%d")
    
    # Dados de HOJE (detalhados)
    veiculos_hoje = conn.execute("SELECT * FROM veiculos WHERE data_operacao = ? ORDER BY grupo, horario_mensagem", (hoje,)).fetchall()
    
    # Dados dos últimos 7 dias (resumo por dia)
    semana = (date.today() - timedelta(days=7)).strftime("%Y-%m-%d")
    historico = conn.execute(
        "SELECT data_operacao, COUNT(*) as total, SUM(CASE WHEN status = 'Disponível' THEN 1 ELSE 0 END) as disp, SUM(CASE WHEN status = 'Indisponível' THEN 1 ELSE 0 END) as indisp FROM veiculos WHERE data_operacao >= ? GROUP BY data_operacao ORDER BY data_operacao DESC",
        (semana,)
    ).fetchall()
    
    # Total geral no banco (compat SQLite/Postgres)
    def _scalar(sql, params=()):
        row = conn.execute(sql, params).fetchone()
        if row is None: return 0
        if isinstance(row, dict): return list(row.values())[0]
        return row[0]
    total_geral = _scalar("SELECT COUNT(*) as cnt FROM veiculos")
    total_grupos = _scalar("SELECT COUNT(DISTINCT grupo) as cnt FROM veiculos WHERE data_operacao = ?", (hoje,))
    
    # Top 10 veículos mais disponíveis no mês atual (compat: LIKE em vez de strftime, sem GROUP_CONCAT)
    mes_atual = date.today().strftime("%Y-%m")
    # Usa LIKE 'YYYY-MM%' que funciona em SQLite e Postgres (data_operacao é TEXT)
    raw_top = conn.execute("""
        SELECT placa, COUNT(*) as disp_cnt FROM veiculos 
        WHERE data_operacao LIKE ? AND status = 'Disponível' 
        GROUP BY placa ORDER BY disp_cnt DESC LIMIT 10
    """, (f"{mes_atual}%",)).fetchall()
    # Normaliza para dict mutável e busca motoristas separadamente (evita GROUP_CONCAT/STRING_AGG incompatível)
    top_mes_rows = []
    for r in raw_top:
        d = dict(r)
        try:
            mot_rows = conn.execute("SELECT DISTINCT motorista FROM veiculos WHERE placa = ? AND data_operacao LIKE ?", (d["placa"], f"{mes_atual}%")).fetchall()
            d["motoristas"] = ", ".join([dict(x)["motorista"] for x in mot_rows])
        except Exception as e:
            print(f"Erro motoristas placa {d.get('placa')}: {e}")
            d["motoristas"] = ""
        top_mes_rows.append(d)
    
    top_mes_txt = ""
    for tm in top_mes_rows:
        top_mes_txt += f"  - Placa {tm['placa']}: {tm['disp_cnt']} dias disponível este mês ({tm['motoristas']})\n"

    conn.close()
    
    # Montar detalhamento de hoje
    disponiveis = [dict(v) for v in veiculos_hoje if v["status"] == "Disponível"]
    indisponiveis = [dict(v) for v in veiculos_hoje if v["status"] == "Indisponível"]
    
    # Detalhamento por grupo
    grupos = {}
    for v in veiculos_hoje:
        g = v["grupo"]
        if g not in grupos: grupos[g] = []
        grupos[g].append(dict(v))
    
    detalhe_grupos = ""
    for g, vs in grupos.items():
        detalhe_grupos += f"\n### Grupo: {g} ({len(vs)} veículos)\n"
        for v in vs:
            status_emoji = "🟢" if v['status'] == "Disponível" else "🔴"
            detalhe_grupos += f"  {status_emoji} Placa: {v['placa']} | Motorista: {v['motorista']} | Tel: {v['telefone']} | Hora: {v['horario_mensagem']} | Status: {v['status']} | Msg: \"{v['mensagem_original']}\"\n"
    
    # Histórico semanal
    historico_txt = ""
    for h in historico:
        historico_txt += f"  - {h['data_operacao']}: {h['total']} registros ({h['disp']} disponíveis, {h['indisp']} indisponíveis)\n"
    
    system_prompt = f"""Você é o Assistente Inteligente da Giannone Transportes, uma empresa de logística e transporte rodoviário.
Sua função é responder perguntas sobre o sistema de monitoramento de veículos com base nos dados REAIS do banco de dados fornecidos abaixo.

## RESUMO DE HOJE ({hoje})
- Total de veículos registrados: {len(veiculos_hoje)}
- Disponíveis: {len(disponiveis)}
- Indisponíveis: {len(indisponiveis)}
- Grupos ativos: {total_grupos}

## DETALHAMENTO POR GRUPO (HOJE):
{detalhe_grupos if detalhe_grupos else "Nenhum veículo registrado hoje."}

## RANKING MENSAL DE DISPONIBILIDADE ({mes_atual}):
{top_mes_txt if top_mes_txt else "Sem dados suficientes para o mês."}

## HISTÓRICO SEMANAL:
{historico_txt if historico_txt else "Sem dados históricos."}

## ESTATÍSTICAS GERAIS:
- Total de registros no banco: {total_geral}

## REGRAS DE RESPOSTA:
- Responda SEMPRE em português do Brasil.
- Seja conciso, objetivo e profissional.
- Se perguntarem sobre qual carro foi mais disponível no mês ou semana, forneça os dados do ranking acima.
- Se perguntarem sobre uma placa específica, procure nos dados acima e informe motorista, telefone, grupo e horário.
- Se perguntarem sobre um motorista, procure pelo nome nos dados.
- Se pedirem um resumo, forneça os números e destaque informações relevantes.
- Formate respostas com negrito (**texto**) para destacar informações importantes.
- Use listas e estrutura quando necessário para facilitar a leitura.
"""
    
    def _post(payload):
        rr = requests.post(url, headers=headers, json=payload, timeout=30)
        print(f"CHAT LLM url={url} model={model} status={rr.status_code} raw={rr.text[:800]}")
        return rr

    try:
        url = get_llm_url(config)
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        msgs = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": req.pergunta}
        ]
        payload = {
            "model": model,
            "messages": msgs,
            "max_tokens": 1200,
            "max_completion_tokens": 1200,
            "temperature": 0.7,
            "stream": False
        }
        r = _post(payload)
        if r.ok:
            try:
                txt = parse_llm_response(r.text).strip()
            except Exception as pe:
                print(f"CHAT parse error (tentativa 1): {pe}")
                # Tentativa 2: sem temperature/max_tokens (alguns routers rejeitam)
                # e com system fundido no user (alguns modelos free só aceitam user)
                merged = system_prompt + "\n\nPergunta do usuário: " + req.pergunta
                payload2 = {
                    "model": model,
                    "messages": [{"role": "user", "content": merged}],
                    "stream": False
                }
                r2 = _post(payload2)
                if not r2.ok:
                    raise Exception(f"HTTP {r2.status_code}: {r2.text[:500]}")
                try:
                    txt = parse_llm_response(r2.text).strip()
                except Exception as pe2:
                    print(f"CHAT parse error (tentativa 2): {pe2} raw={r2.text[:800]}")
                    # modelo retornou 200 mas content vazio -> explica causa provável
                    raise Exception(
                        f"O modelo '{model}' retornou resposta vazia (content:\"\" "
                        f"finish_reason:stop). Isso é limite/bug do modelo free ou do router, "
                        f"não do app. Troque o modelo (ex: google/gemini-2.5-flash-lite ou "
                        f"llama-3.3-70b-versatile) ou aumente o limite no router. Raw: {r2.text[:300]}"
                    )
            print(f"CHAT parsed txt len={len(txt)} txt={txt[:300]}")
            if not txt:
                raise Exception(
                    f"O modelo '{model}' retornou resposta vazia. Troque o modelo nas "
                    f"Configurações (o Testar IA usa prompt mínimo e também falharia). Raw: {r.text[:300]}"
                )
            return {"resposta": txt}
        else:
            raise Exception(f"HTTP {r.status_code}: {r.text[:500]}")
    except HTTPException:
        raise
    except Exception as e:
        import traceback; traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Erro na IA: {str(e)}")

# --------- HELPER & ROTAS DE RELATÓRIOS GERENCIAIS ---------
def calcular_intervalo_datas(periodo: str = "mes", data_inicio: str = None, data_fim: str = None):
    agora = datetime.now(timezone(timedelta(hours=-3))).date()
    if periodo == "hoje":
        dt_ini = agora
        dt_fim = agora
    elif periodo == "semana":
        dt_ini = agora - timedelta(days=agora.weekday())
        dt_fim = agora
    elif periodo == "mes":
        dt_ini = agora.replace(day=1)
        dt_fim = agora
    elif periodo == "30dias":
        dt_ini = agora - timedelta(days=30)
        dt_fim = agora
    elif periodo == "custom" and data_inicio and data_fim:
        try:
            dt_ini = datetime.strptime(data_inicio, "%Y-%m-%d").date()
            dt_fim = datetime.strptime(data_fim, "%Y-%m-%d").date()
        except:
            dt_ini = agora.replace(day=1)
            dt_fim = agora
    else:
        dt_ini = agora.replace(day=1)
        dt_fim = agora

    return dt_ini.strftime("%Y-%m-%d"), dt_fim.strftime("%Y-%m-%d")

@app.get("/api/relatorios/resumo")
async def relatorio_resumo(
    periodo: str = "mes", 
    data_inicio: str = None, 
    data_fim: str = None, 
    grupo: str = None,
    current_user: dict = Depends(get_current_user)
):
    dt_ini, dt_fim = calcular_intervalo_datas(periodo, data_inicio, data_fim)
    conn = get_db_connection()
    
    filtro_grupo = ""
    params_grupo = []
    if grupo:
        filtro_grupo = " AND grupo = ?"
        params_grupo = [grupo]

    # 1. Ranking de veículos mais disponíveis (compat: sem GROUP_CONCAT)
    if USE_POSTGRES:
        agg_motoristas = "STRING_AGG(DISTINCT motorista, ', ')"
    else:
        agg_motoristas = "GROUP_CONCAT(DISTINCT motorista)"
    sql_ranking = f"""
        SELECT 
            placa,
            COUNT(*) as total_registros,
            SUM(CASE WHEN status = 'Disponível' THEN 1 ELSE 0 END) as disponivel_cnt,
            SUM(CASE WHEN status = 'Indisponível' THEN 1 ELSE 0 END) as indisponivel_cnt,
            MAX(data_operacao) as ultima_data,
            {agg_motoristas} as motoristas
        FROM veiculos
        WHERE data_operacao BETWEEN ? AND ? {filtro_grupo}
        GROUP BY placa
        ORDER BY disponivel_cnt DESC, total_registros DESC
        LIMIT 30
    """
    ranking = [dict(r) for r in conn.execute(sql_ranking, [dt_ini, dt_fim] + params_grupo).fetchall()]
    
    # 2. Resumo geral de status
    sql_status = f"""
        SELECT 
            COUNT(*) as total_registros,
            SUM(CASE WHEN status = 'Disponível' THEN 1 ELSE 0 END) as total_disponiveis,
            SUM(CASE WHEN status = 'Indisponível' THEN 1 ELSE 0 END) as total_indisponiveis,
            COUNT(DISTINCT placa) as total_placas_unicas,
            COUNT(DISTINCT telefone) as total_motoristas_unicos
        FROM veiculos
        WHERE data_operacao BETWEEN ? AND ? {filtro_grupo}
    """
    status_row = dict(conn.execute(sql_status, [dt_ini, dt_fim] + params_grupo).fetchone() or {})
    
    # 3. Resumo por grupo
    sql_grupos = f"""
        SELECT 
            grupo,
            COUNT(*) as total,
            SUM(CASE WHEN status = 'Disponível' THEN 1 ELSE 0 END) as disponiveis
        FROM veiculos
        WHERE data_operacao BETWEEN ? AND ?
        GROUP BY grupo
        ORDER BY total DESC
    """
    grupos = [dict(g) for g in conn.execute(sql_grupos, [dt_ini, dt_fim]).fetchall()]
    
    # 4. Top motoristas mais ativos
    sql_motoristas = f"""
        SELECT 
            motorista,
            telefone,
            COUNT(*) as total_registros,
            COUNT(DISTINCT placa) as total_placas
        FROM veiculos
        WHERE data_operacao BETWEEN ? AND ? {filtro_grupo}
        GROUP BY telefone
        ORDER BY total_registros DESC
        LIMIT 10
    """
    motoristas = [dict(m) for m in conn.execute(sql_motoristas, [dt_ini, dt_fim] + params_grupo).fetchall()]
    
    conn.close()
    
    total = status_row.get("total_registros", 0) or 0
    disp = status_row.get("total_disponiveis", 0) or 0
    taxa_disp = round((disp / total * 100), 1) if total > 0 else 0.0
    
    top_veiculo = ranking[0]["placa"] if ranking else "Nenhum"
    top_veiculo_cnt = ranking[0]["disponivel_cnt"] if ranking else 0
    top_motorista = motoristas[0]["motorista"] if motoristas else "Nenhum"

    return {
        "periodo": {"data_inicio": dt_ini, "data_fim": dt_fim, "tipo": periodo},
        "indicadores": {
            "top_veiculo": top_veiculo,
            "top_veiculo_dias": top_veiculo_cnt,
            "taxa_disponibilidade": taxa_disp,
            "total_registros": total,
            "total_disponiveis": disp,
            "total_indisponiveis": status_row.get("total_indisponiveis", 0) or 0,
            "total_placas_unicas": status_row.get("total_placas_unicas", 0) or 0,
            "top_motorista": top_motorista
        },
        "ranking_veiculos": ranking,
        "grupos": grupos,
        "motoristas": motoristas
    }

@app.get("/api/relatorios/detalhado")
async def relatorio_detalhado(
    periodo: str = "mes",
    data_inicio: str = None,
    data_fim: str = None,
    grupo: str = None,
    placa: str = None,
    status: str = None,
    current_user: dict = Depends(get_current_user)
):
    dt_ini, dt_fim = calcular_intervalo_datas(periodo, data_inicio, data_fim)
    conn = get_db_connection()
    
    sql = "SELECT * FROM veiculos WHERE data_operacao BETWEEN ? AND ?"
    params = [dt_ini, dt_fim]
    
    if grupo:
        sql += " AND grupo = ?"
        params.append(grupo)
    if placa:
        sql += " AND placa LIKE ?"
        params.append(f"%{placa.strip().upper()}%")
    if status:
        sql += " AND status = ?"
        params.append(status)
        
    sql += " ORDER BY data_operacao DESC, horario_mensagem DESC"
    
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.get("/api/relatorios/exportar")
async def exportar_relatorio_csv(
    periodo: str = "mes",
    data_inicio: str = None,
    data_fim: str = None,
    grupo: str = None,
    status: str = None,
    current_user: dict = Depends(get_current_user)
):
    dt_ini, dt_fim = calcular_intervalo_datas(periodo, data_inicio, data_fim)
    conn = get_db_connection()
    
    sql = "SELECT data_operacao, horario_mensagem, grupo, motorista, telefone, placa, status, mensagem_original FROM veiculos WHERE data_operacao BETWEEN ? AND ?"
    params = [dt_ini, dt_fim]
    
    if grupo:
        sql += " AND grupo = ?"
        params.append(grupo)
    if status:
        sql += " AND status = ?"
        params.append(status)
        
    sql += " ORDER BY data_operacao DESC, horario_mensagem DESC"
    
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(["Data Operacao", "Horario", "Grupo", "Motorista", "Telefone", "Placa", "Status", "Mensagem Original"])
    
    for r in rows:
        writer.writerow([
            r["data_operacao"],
            r["horario_mensagem"],
            r["grupo"],
            r["motorista"],
            r["telefone"],
            r["placa"],
            r["status"],
            r["mensagem_original"]
        ])
        
    csv_bytes = output.getvalue().encode("utf-8-sig")
    filename = f"relatorio_giannone_{dt_ini}_a_{dt_fim}.csv"
    return Response(
        content=csv_bytes,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

