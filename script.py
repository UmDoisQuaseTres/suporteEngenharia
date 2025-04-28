import os
import json
import hmac
import hashlib
import time
import sqlite3
from flask import Flask, request, abort, Response, g, jsonify # Usar jsonify
from flask_cors import CORS # Para CORS
from dotenv import load_dotenv
import logging # Para logs

# --- Configuração ---
load_dotenv()
app = Flask(__name__)
CORS(app) # Habilita CORS

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

VERIFY_TOKEN = os.environ.get("WHATSAPP_VERIFY_TOKEN", "fallback_verify_token")
APP_SECRET = os.environ.get("WHATSAPP_APP_SECRET")
if not APP_SECRET:
    logging.warning("WHATSAPP_APP_SECRET não está definida no .env. A validação de assinatura falhará.")

DATABASE = 'whatsapp_data_v2.db' # Novo nome para evitar conflito com a estrutura antiga

# --- Funções do Banco de Dados ---

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        logging.info(f"Conectando ao banco de dados: {DATABASE}")
        try:
            db = g._database = sqlite3.connect(DATABASE, timeout=10)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA journal_mode=WAL;")
            logging.info("Conexão com DB estabelecida.")
        except sqlite3.Error as e:
            logging.error(f"Erro ao conectar ao banco de dados: {e}")
            raise
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        try:
            db.close()
            logging.info("Conexão com o banco de dados fechada.")
        except sqlite3.Error as e:
            logging.error(f"Erro ao fechar a conexão com o banco de dados: {e}")

def init_db():
    """Cria as tabelas do banco de dados com a nova estrutura."""
    logging.info("Tentando inicializar o banco de dados (v2)...")
    try:
        with app.app_context():
            db = get_db()
            cursor = db.cursor()
            logging.info("Criando tabela 'conversations' (se não existir)...")
            # --- ALTERAÇÃO NO SCHEMA ---
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS conversations (
                    sender_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL CHECK(status IN ('open', 'closed')),
                    creation_timestamp INTEGER NOT NULL, -- Timestamp de criação/reabertura
                    closed_timestamp INTEGER             -- Timestamp de fechamento (NULL se aberta)
                )
            ''')
            logging.info("Criando tabela 'counters' (se não existir)...")
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS counters (
                    counter_name TEXT PRIMARY KEY,
                    value INTEGER NOT NULL
                )
            ''')
            logging.info("Inserindo contadores iniciais (se não existirem)...")
            cursor.execute("INSERT OR IGNORE INTO counters (counter_name, value) VALUES (?, ?)", ('new_conversation_count', 0))
            cursor.execute("INSERT OR IGNORE INTO counters (counter_name, value) VALUES (?, ?)", ('open_conversation_count', 0))
            cursor.execute("INSERT OR IGNORE INTO counters (counter_name, value) VALUES (?, ?)", ('closed_conversation_count', 0))
            db.commit()
            logging.info("Banco de dados inicializado com sucesso (v2).")
    except sqlite3.Error as e:
        logging.error(f"Erro de SQLite durante init_db: {e}")
    except Exception as e:
        logging.error(f"Erro inesperado durante init_db: {e}")

# --- Endpoint do Webhook ---

@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    if request.method == 'GET':
        # (Lógica de verificação GET inalterada)
        verify_token = request.args.get('hub.verify_token')
        challenge = request.args.get('hub.challenge')
        mode = request.args.get('hub.mode')
        logging.info(f"Webhook GET - Mode: {mode}, Token Recebido: {'***' if verify_token else 'Nenhum'}, Challenge: {challenge}")
        if mode == 'subscribe' and verify_token == VERIFY_TOKEN:
            logging.info("Webhook verificado com sucesso!")
            return Response(challenge, status=200)
        else:
            logging.warning(f"Falha na verificação do webhook. Esperado: {VERIFY_TOKEN}, Recebido: {verify_token}")
            abort(403)

    elif request.method == 'POST':
        # (Lógica de validação de assinatura POST inalterada)
        signature_header = request.headers.get('X-Hub-Signature-256', '')
        if not APP_SECRET: logging.error("APP_SECRET não configurado."); abort(500)
        if not signature_header.startswith('sha256='): logging.warning("Formato assinatura inválido."); abort(403)
        signature_hash = signature_header.split('=')[1]
        request_body = request.data
        try:
            expected_hash = hmac.new(APP_SECRET.encode('utf-8'), request_body, hashlib.sha256).hexdigest()
        except Exception as e: logging.error(f"Erro HMAC: {e}"); abort(500)
        if not hmac.compare_digest(signature_hash, expected_hash): logging.warning("Assinatura inválida."); abort(403)
        logging.info("Assinatura verificada.")

        # Processar Payload
        data = request.get_json()
        db = None
        try:
            db = get_db()
            cursor = db.cursor()

            if data.get('object') == 'whatsapp_business_account':
                for entry in data.get('entry', []):
                    for change in entry.get('changes', []):
                        value = change.get('value', {})
                        if 'messages' in value:
                            for message in value.get('messages', []):
                                if 'from' in message and message.get('type'):
                                    sender_id = message['from']
                                    # Usa o timestamp da mensagem ou o tempo atual como fallback
                                    timestamp = int(message.get('timestamp', int(time.time())))
                                    logging.info(f"Msg recebida: {sender_id} @ {timestamp}")

                                    # --- LÓGICA COM BANCO DE DADOS ALTERADA ---
                                    cursor.execute("SELECT status FROM conversations WHERE sender_id = ?", (sender_id,))
                                    result = cursor.fetchone()

                                    is_new_or_reopened = False
                                    if result is None:
                                        is_new_or_reopened = True
                                        logging.info(f"Primeira msg de {sender_id}.")
                                    elif result['status'] == 'closed':
                                        is_new_or_reopened = True
                                        logging.info(f"Reabrindo conversa com {sender_id}.")

                                    if is_new_or_reopened:
                                        # Atualiza contadores (lógica mantida do seu código)
                                        cursor.execute("UPDATE counters SET value = value + 1 WHERE counter_name = 'new_conversation_count'")
                                        cursor.execute("UPDATE counters SET value = value + 1 WHERE counter_name = 'open_conversation_count'")

                                        # --- ALTERAÇÃO NO INSERT/REPLACE ---
                                        # Insere ou atualiza: status='open', define creation_timestamp, closed_timestamp=NULL
                                        cursor.execute('''
                                            INSERT OR REPLACE INTO conversations (sender_id, status, creation_timestamp, closed_timestamp)
                                            VALUES (?, 'open', ?, NULL)
                                        ''', (sender_id, timestamp)) # Usa o timestamp da mensagem como criação/reabertura
                                        db.commit()
                                        logging.info(f"Conversa com {sender_id} marcada/atualizada como ABERTA (creation: {timestamp}).")

                                        # (Log dos contadores mantido do seu código)
                                        cursor.execute("SELECT value FROM counters WHERE counter_name = 'new_conversation_count'")
                                        count_result = cursor.fetchone(); current_count = count_result['value'] if count_result else 'ERRO'
                                        logging.info(f"CONTADOR NOVAS CONVERSAS: {current_count}")

                                    else:
                                        # Conversa já estava aberta, não faz nada na tabela conversations
                                        logging.info(f"Msg recebida na conversa já aberta com {sender_id} (nenhuma atualização de DB necessária).")

            return jsonify(success=True), 200

        except sqlite3.Error as e:
            logging.error(f"Erro DB no POST: {e}")
            if db: db.rollback()
            return jsonify(success=False, error="Database error"), 200
        except Exception as e:
            logging.exception("Erro inesperado no POST:")
            if db: db.rollback()
            return jsonify(success=False, error="Internal server error"), 200

    else:
        abort(405)

# --- Endpoints para o Software C# ---

@app.route('/count', methods=['GET'])
def get_count():
    """Retorna as contagens atuais (lendo da tabela counters)."""
    # (Lógica inalterada, pois lê a tabela 'counters' que você manteve)
    try:
        db = get_db()
        cursor = db.cursor()
        counters = {}
        for counter_name in ['new_conversation_count', 'open_conversation_count', 'closed_conversation_count']:
            cursor.execute("SELECT value FROM counters WHERE counter_name = ?", (counter_name,))
            result = cursor.fetchone()
            counters[counter_name] = result['value'] if result else 0
        logging.info(f"Retornando contagens: {counters}")
        return jsonify(counters)
    except sqlite3.Error as e:
        logging.error(f"Erro DB em /count: {e}")
        return jsonify(error="Erro ao acessar banco de dados"), 500
    except Exception as e:
        logging.exception("Erro inesperado em /count:")
        return jsonify(error="Erro interno do servidor"), 500


@app.route('/status', methods=['GET'])
def get_all_statuses():
    """Retorna o status e os timestamps de todas as conversas."""
    all_statuses = {}
    try:
        db = get_db()
        cursor = db.cursor()
        # --- ALTERAÇÃO NO SELECT ---
        # Seleciona os novos campos de timestamp, ordena por criação
        cursor.execute("SELECT sender_id, status, creation_timestamp, closed_timestamp FROM conversations ORDER BY creation_timestamp DESC")
        results = cursor.fetchall()
        for row in results:
            all_statuses[row['sender_id']] = dict(row)
        logging.info(f"Retornando {len(all_statuses)} status de conversas.")
        return jsonify(all_statuses)
    except sqlite3.Error as e:
        logging.error(f"Erro de DB em /status: {e}")
        return jsonify(error="Erro ao acessar banco de dados"), 500
    except Exception as e:
        logging.exception("Erro inesperado em /status:")
        return jsonify(error="Erro interno do servidor"), 500


@app.route('/close/<sender_id>', methods=['POST'])
def close_conversation(sender_id):
    """Marca uma conversa como 'closed' e registra o closed_timestamp."""
    logging.info(f"Req para fechar conversa: {sender_id}")
    db = None
    try:
        db = get_db()
        cursor = db.cursor()
        cursor.execute("SELECT status FROM conversations WHERE sender_id = ?", (sender_id,))
        result = cursor.fetchone()

        if result and result['status'] == 'open':
            closed_time = int(time.time()) # Pega o tempo atual para fechar
            # --- ALTERAÇÃO NO UPDATE ---
            # Define status='closed' e closed_timestamp
            cursor.execute("UPDATE conversations SET status = 'closed', closed_timestamp = ? WHERE sender_id = ?",
                           (closed_time, sender_id))

            # Atualiza contadores (lógica mantida do seu código)
            cursor.execute("UPDATE counters SET value = value - 1 WHERE counter_name = 'open_conversation_count'")
            cursor.execute("UPDATE counters SET value = value + 1 WHERE counter_name = 'closed_conversation_count'")
            cursor.execute("UPDATE counters SET value = value - 1 WHERE counter_name = 'new_conversation_count'")

            db.commit()
            logging.info(f"Conversa com {sender_id} marcada como FECHADA @ {closed_time}.")
            return jsonify(status="closed")
        elif result and result['status'] == 'closed':
            logging.info(f"Conversa com {sender_id} já estava fechada.")
            return jsonify(status="already_closed")
        else:
            logging.warning(f"Conversa não encontrada para fechar: {sender_id}")
            return jsonify(status="not_found"), 404
    except sqlite3.Error as e:
        logging.error(f"Erro de DB em /close/{sender_id}: {e}")
        if db: db.rollback()
        return jsonify(error="Erro ao acessar banco de dados"), 500
    except Exception as e:
        logging.exception(f"Erro inesperado em /close/{sender_id}:")
        if db: db.rollback()
        return jsonify(error="Erro interno do servidor"), 500


@app.route('/recalculate-counters', methods=['POST'])
def recalculate_counters():
    """Recalcula os contadores com base nos registros atuais (mantido do seu código)."""
    # (Lógica inalterada)
    try:
        db = get_db()
        cursor = db.cursor()
        cursor.execute("SELECT COUNT(*) as count FROM conversations WHERE status = 'open'")
        open_count = cursor.fetchone()['count']
        cursor.execute("SELECT COUNT(*) as count FROM conversations WHERE status = 'closed'")
        closed_count = cursor.fetchone()['count']
        cursor.execute("UPDATE counters SET value = ? WHERE counter_name = ?", (open_count, 'open_conversation_count'))
        cursor.execute("UPDATE counters SET value = ? WHERE counter_name = ?", (closed_count, 'closed_conversation_count'))
        cursor.execute("UPDATE counters SET value = ? WHERE counter_name = ?", (open_count, 'new_conversation_count')) # Ajusta 'new' para igual a 'open'
        db.commit()
        logging.info("Contadores recalculados.")
        return jsonify({ "success": True, "open_conversation_count": open_count, "closed_conversation_count": closed_count, "new_conversation_count": open_count })
    except sqlite3.Error as e:
        logging.error(f"Erro de DB em /recalculate-counters: {e}")
        if db: db.rollback(); return jsonify(error="Erro ao acessar banco de dados"), 500
    except Exception as e:
        logging.exception("Erro inesperado em /recalculate-counters:")
        if db: db.rollback(); return jsonify(error="Erro interno do servidor"), 500

# --- Inicialização ---
if __name__ == '__main__':
    init_db()
    port = int(os.environ.get("PORT", 5000))
    logging.info(f"Iniciando Flask app na porta {port}...")
    app.run(host='0.0.0.0', port=port, debug=True) # Mude debug=False em produção