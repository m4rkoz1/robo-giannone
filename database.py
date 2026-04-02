import sqlite3
import hashlib

def verify_password(plain_password, hashed_password):
    return hashlib.sha256(plain_password.encode()).hexdigest() == hashed_password

def get_password_hash(password):
    return hashlib.sha256(password.encode()).hexdigest()

def init_db():
    import os
    os.makedirs("data", exist_ok=True)
    conn = sqlite3.connect("data/giannone.db")
    cursor = conn.cursor()
    
    # Tabela de Usuários
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password_hash TEXT,
            role TEXT
        )
    ''')
    
    # Tabela de Configurações
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS config (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            palavra_chave TEXT,
            regex_placa TEXT
        )
    ''')
    
    # Tabela de Mensagens/Veículos Disponíveis
    cursor.execute('''
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
    ''')
    
    # Cria usuário admin e config padrão se não existirem
    cursor.execute("SELECT COUNT(*) FROM users")
    if cursor.fetchone()[0] == 0:
        cursor.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            ("admin", get_password_hash("admin123"), "admin")
        )
        
    cursor.execute("SELECT COUNT(*) FROM config")
    if cursor.fetchone()[0] == 0:
        cursor.execute(
            "INSERT INTO config (palavra_chave, regex_placa) VALUES (?, ?)",
            ("dispon[ií]vel|indispon[ií]vel", r"\b[A-Za-z]{3}[-\s]*\d[A-Za-z\d]\d{2}\b")
        )

    # Migração segura para adicionar novas colunas da Evolution API
    try:
        cursor.execute("ALTER TABLE config ADD COLUMN evo_url TEXT DEFAULT ''")
    except sqlite3.OperationalError: pass
    try:
        cursor.execute("ALTER TABLE config ADD COLUMN evo_instance TEXT DEFAULT ''")
    except sqlite3.OperationalError: pass
    try:
        cursor.execute("ALTER TABLE config ADD COLUMN evo_apikey TEXT DEFAULT ''")
    except sqlite3.OperationalError: pass

    # Migração da coluna Status na tabela veiculos
    try:
        cursor.execute("ALTER TABLE veiculos ADD COLUMN status TEXT DEFAULT 'Disponível'")
    except sqlite3.OperationalError: pass

    # Migração da coluna message_id na tabela veiculos
    try:
        cursor.execute("ALTER TABLE veiculos ADD COLUMN message_id TEXT DEFAULT ''")
    except sqlite3.OperationalError: pass

    # Migração LLM (IA OpenRouter)
    try:
        cursor.execute("ALTER TABLE config ADD COLUMN llm_api_key TEXT DEFAULT ''")
    except sqlite3.OperationalError: pass
    try:
        cursor.execute("ALTER TABLE config ADD COLUMN llm_model TEXT DEFAULT 'google/gemini-2.5-flash-lite-preview'")
    except sqlite3.OperationalError: pass

    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_db()
