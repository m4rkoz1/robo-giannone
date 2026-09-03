import os
import hashlib
import sqlite3

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

USE_POSTGRES = DATABASE_URL.startswith("postgresql://")

SQLITE_PATH = os.getenv("SQLITE_PATH", "data/giannone.db")
ADMIN_DEFAULT_PASSWORD = os.getenv("ADMIN_DEFAULT_PASSWORD", "admin123")

def verify_password(plain_password, hashed_password):
    return hashlib.sha256(plain_password.encode()).hexdigest() == hashed_password

def get_password_hash(password):
    return hashlib.sha256(password.encode()).hexdigest()

# -------- Compat layer: traduz ? -> %s automaticamente para Postgres --------
class _CompatCursor:
    def __init__(self, cursor, is_postgres):
        self._cur = cursor
        self._is_pg = is_postgres
    def execute(self, sql, params=()):
        if self._is_pg:
            sql = sql.replace("?", "%s")
        return self._cur.execute(sql, params)
    def fetchone(self): return self._cur.fetchone()
    def fetchall(self): return self._cur.fetchall()
    @property
    def rowcount(self): return self._cur.rowcount
    def __getattr__(self, name): return getattr(self._cur, name)

class _CompatConnection:
    def __init__(self, conn, is_postgres):
        self._conn = conn
        self._is_pg = is_postgres
    def cursor(self, *a, **kw):
        return _CompatCursor(self._conn.cursor(*a, **kw), self._is_pg)
    def execute(self, sql, params=()):
        if self._is_pg:
            sql = sql.replace("?", "%s")
        cur = self._conn.cursor()
        cur.execute(sql, params)
        # wrap cursor também para fetch*
        return _CompatCursor(cur, False)  # já traduziu, não traduz de novo
    def commit(self): return self._conn.commit()
    def close(self): return self._conn.close()
    def __getattr__(self, name): return getattr(self._conn, name)

def get_db_connection():
    if USE_POSTGRES:
        import psycopg2, psycopg2.extras
        raw = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
        return _CompatConnection(raw, True)
    else:
        os.makedirs(os.path.dirname(SQLITE_PATH) or "data", exist_ok=True)
        raw = sqlite3.connect(SQLITE_PATH)
        raw.row_factory = sqlite3.Row
        return _CompatConnection(raw, False)

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()

    def exec_ddl(sql_sqlite, sql_pg=None):
        sql = sql_pg if (USE_POSTGRES and sql_pg) else sql_sqlite
        try:
            cur.execute(sql)
        except Exception as e:
            print(f"DDL warning: {e}")

    # Tabelas base
    exec_ddl('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password_hash TEXT,
            role TEXT
        )
    ''', '''
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE,
            password_hash TEXT,
            role TEXT
        )
    ''')
    exec_ddl('''
        CREATE TABLE IF NOT EXISTS config (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            palavra_chave TEXT,
            regex_placa TEXT
        )
    ''', '''
        CREATE TABLE IF NOT EXISTS config (
            id SERIAL PRIMARY KEY,
            palavra_chave TEXT,
            regex_placa TEXT
        )
    ''')
    exec_ddl('''
        CREATE TABLE IF NOT EXISTS veiculos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data_operacao TEXT,
            motorista TEXT,
            telefone TEXT,
            placa TEXT,
            grupo TEXT,
            horario_mensagem TEXT,
            mensagem_original TEXT
        )
    ''', '''
        CREATE TABLE IF NOT EXISTS veiculos (
            id SERIAL PRIMARY KEY,
            data_operacao TEXT,
            motorista TEXT,
            telefone TEXT,
            placa TEXT,
            grupo TEXT,
            horario_mensagem TEXT,
            mensagem_original TEXT
        )
    ''')

    cur.execute("SELECT COUNT(*) as cnt FROM users")
    row = cur.fetchone()
    cnt = row["cnt"] if isinstance(row, dict) else row[0]
    if cnt == 0:
        cur.execute("INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                    ("admin", get_password_hash(ADMIN_DEFAULT_PASSWORD), "admin"))

    cur.execute("SELECT COUNT(*) as cnt FROM config")
    row = cur.fetchone()
    cnt = row["cnt"] if isinstance(row, dict) else row[0]
    if cnt == 0:
        cur.execute("INSERT INTO config (palavra_chave, regex_placa) VALUES (?, ?)",
                    ("dispon[ií]vel|indispon[ií]vel", r"\b[A-Za-z]{3}[-\s]*\d[A-Za-z\d]\d{2}\b"))

    migrations = [
        "ALTER TABLE config ADD COLUMN evo_url TEXT DEFAULT ''",
        "ALTER TABLE config ADD COLUMN evo_instance TEXT DEFAULT ''",
        "ALTER TABLE config ADD COLUMN evo_apikey TEXT DEFAULT ''",
        "ALTER TABLE veiculos ADD COLUMN status TEXT DEFAULT 'Disponível'",
        "ALTER TABLE veiculos ADD COLUMN message_id TEXT DEFAULT ''",
        "ALTER TABLE config ADD COLUMN msg_erro_placa TEXT DEFAULT ''",
        "ALTER TABLE config ADD COLUMN llm_api_key TEXT DEFAULT ''",
        "ALTER TABLE config ADD COLUMN llm_model TEXT DEFAULT 'meta/llama-3.3-70b-instruct'",
        "ALTER TABLE config ADD COLUMN llm_base_url TEXT DEFAULT 'https://integrate.api.nvidia.com/v1'",
    ]
    for sql in migrations:
        try:
            cur.execute(sql)
        except Exception:
            pass

    # Migração de dados: atualiza mensagem padrão do auto-responder que
    # permitia placa parcial ("ou 3 primeiras letras") para exigir a PLACA INTEIRA.
    # Só altera quem ainda tem o texto padrão antigo; mensagem customizada é preservada.
    try:
        old_msg = "⚠️ Ops, faltou uma informação!\nPara registrar corretamente seu status na Giannone, mande novamente a mensagem e *informe a PLACA completa* (ou 3 primeiras letras) junto com seu aviso."
        new_msg = "⚠️ Ops, faltou a *PLACA INTEIRA*!\nPara registrar seu status na Giannone, mande novamente a mensagem com a *placa completa do veículo* (7 caracteres, ex: ABC1234 ou ABC1D23). Sem a placa inteira não consigo registrar."
        cur.execute("UPDATE config SET msg_erro_placa = ? WHERE msg_erro_placa = ?", (new_msg, old_msg))
    except Exception:
        pass
    conn.commit()
    conn.close()
    print(f"Banco inicializado: {'Postgres' if USE_POSTGRES else 'SQLite em ' + SQLITE_PATH}")

if __name__ == "__main__":
    init_db()
