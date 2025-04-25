import os
import json
import hmac
import hashlib
import time
import sqlite3  # Importa a biblioteca SQLite
from flask import Flask, request, abort, Response, g  # g é útil para gerenciar conexões
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)

# --- Configuração ---
# Use o token que você definiu no painel da Meta
VERIFY_TOKEN = os.environ.get("WHATSAPP_VERIFY_TOKEN", "fallback_verify_token") # Pode manter um fallback ou remover
APP_SECRET = os.environ.get("WHATSAPP_APP_SECRET")
# Nome do arquivo do banco de dados SQLite
DATABASE = 'whatsapp_data.db'


# --- Funções do Banco de Dados ---

def get_db():
    """Abre uma nova conexão com o banco de dados se não houver uma para a requisição atual."""
    # g é um objeto especial do Flask que é único para cada requisição.
    # Usado para armazenar dados que podem ser acessados múltiplas vezes durante uma requisição.
    db = getattr(g, '_database', None)
    if db is None:
        print(f"Conectando ao banco de dados: {DATABASE}")
        try:
            db = g._database = sqlite3.connect(DATABASE)
            # Configura para que as linhas retornadas sejam como dicionários (acesso por nome de coluna)
            db.row_factory = sqlite3.Row
            print("Conexão estabelecida.")
        except sqlite3.Error as e:
            print(f"Erro ao conectar ao banco de dados: {e}")
            raise  # Re-levanta a exceção para que o Flask a capture
    return db


@app.teardown_appcontext
def close_connection(exception):
    """Fecha a conexão com o banco de dados ao final da requisição."""
    db = getattr(g, '_database', None)
    if db is not None:
        try:
            db.close()
            print("Conexão com o banco de dados fechada.")
        except sqlite3.Error as e:
            print(f"Erro ao fechar a conexão com o banco de dados: {e}")


def init_db():
    """Cria as tabelas do banco de dados se elas não existirem."""
    print("Tentando inicializar o banco de dados...")
    try:
        # Usamos app_context para poder usar get_db fora de uma requisição HTTP
        with app.app_context():
            db = get_db()
            cursor = db.cursor()
            print("Criando tabela 'conversations' (se não existir)...")
            cursor.execute('''
                           CREATE TABLE IF NOT EXISTS conversations
                           (
                               sender_id
                               TEXT
                               PRIMARY
                               KEY,
                               status
                               TEXT
                               NOT
                               NULL,
                               last_update
                               INTEGER
                               NOT
                               NULL
                           )
                           ''')
            print("Criando tabela 'counters' (se não existir)...")
            cursor.execute('''
                           CREATE TABLE IF NOT EXISTS counters
                           (
                               counter_name
                               TEXT
                               PRIMARY
                               KEY,
                               value
                               INTEGER
                               NOT
                               NULL
                           )
                           ''')
            print("Inserindo contadores iniciais (se não existirem)...")
            # Insere os contadores iniciais apenas se eles não existirem ainda
            cursor.execute('''
                           INSERT
                           OR IGNORE INTO counters (counter_name, value) VALUES (?, ?)
                           ''', ('new_conversation_count', 0))
            cursor.execute('''
                           INSERT
                           OR IGNORE INTO counters (counter_name, value) VALUES (?, ?)
                           ''', ('open_conversation_count', 0))
            cursor.execute('''
                           INSERT
                           OR IGNORE INTO counters (counter_name, value) VALUES (?, ?)
                           ''', ('closed_conversation_count', 0))
            db.commit()  # Salva as alterações
            print("Banco de dados inicializado com sucesso.")
    except sqlite3.Error as e:
        print(f"Erro de SQLite durante init_db: {e}")
    except Exception as e:
        print(f"Erro inesperado durante init_db: {e}")


# --- Endpoint do Webhook (Modificado para usar DB) ---

@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    if request.method == 'GET':
        # --- Verificação do Webhook (GET) ---
        # (Lógica inalterada, já funciona)
        verify_token = request.args.get('hub.verify_token')
        challenge = request.args.get('hub.challenge')
        mode = request.args.get('hub.mode')
        print(f"Recebido GET - Mode: {mode}, Token: {verify_token}, Challenge: {challenge}")
        if mode == 'subscribe' and verify_token == VERIFY_TOKEN:
            print("Webhook verificado com sucesso!")
            return Response(challenge, status=200)
        else:
            print("Falha na verificação do webhook.")
            abort(403)

    elif request.method == 'POST':
        # --- Recebimento de Notificações (POST) ---

        # 1. Verificar Assinatura
        signature_header = request.headers.get('X-Hub-Signature-256', '')
        if not APP_SECRET:
            print("Erro Crítico: APP_SECRET não está configurado!")
            abort(500)  # Internal Server Error - não podemos verificar
        if not signature_header.startswith('sha256='):
            print("Erro: Assinatura inválida (formato incorreto).")
            abort(403)
        signature_hash = signature_header.split('=')[1]
        request_body = request.data
        try:
            expected_hash = hmac.new(
                APP_SECRET.encode('utf-8'), request_body, hashlib.sha256
            ).hexdigest()
        except Exception as e:
            print(f"Erro ao gerar hash HMAC: {e}")
            abort(500)

        if not hmac.compare_digest(signature_hash, expected_hash):
            print(f"Erro: Assinatura inválida. Recebido: {signature_hash}, Esperado: {expected_hash}")
            abort(403)
        print("Assinatura verificada com sucesso.")

        # 2. Processar o Payload JSON
        data = request.get_json()
        db = None  # Inicializa db para garantir que exista no bloco finally
        try:
            db = get_db()  # Obtém conexão com o DB para esta requisição
            cursor = db.cursor()

            if data.get('object') == 'whatsapp_business_account':
                for entry in data.get('entry', []):
                    for change in entry.get('changes', []):
                        value = change.get('value', {})
                        if 'messages' in value:
                            for message in value.get('messages', []):
                                if 'from' in message and message.get('type'):
                                    sender_id = message['from']
                                    message_type = message['type']
                                    timestamp = int(message['timestamp'])
                                    print(f"Mensagem recebida de {sender_id} (Tipo: {message_type})")

                                    # --- LÓGICA COM BANCO DE DADOS ---
                                    cursor.execute("SELECT status FROM conversations WHERE sender_id = ?", (sender_id,))
                                    result = cursor.fetchone()  # Retorna um objeto Row ou None

                                    is_new_conversation = False
                                    if result is None:
                                        is_new_conversation = True
                                        print(f"Primeira mensagem detectada de {sender_id}.")
                                    # Acessa o status pelo nome da coluna se result não for None
                                    elif result['status'] == 'closed':
                                        is_new_conversation = True
                                        print(f"Nova conversa detectada de {sender_id} (anterior estava fechada).")

                                    if is_new_conversation:
                                        # Incrementa contador de novas conversas
                                        cursor.execute("UPDATE counters SET value = value + 1 WHERE counter_name = ?",
                                                       ('new_conversation_count',))

                                        # Incrementa contador de conversas abertas
                                        cursor.execute("UPDATE counters SET value = value + 1 WHERE counter_name = ?",
                                                       ('open_conversation_count',))

                                        # Insere ou atualiza conversa para 'open'
                                        cursor.execute('''
                                            INSERT OR REPLACE INTO conversations (sender_id, status, last_update)
                                            VALUES (?, ?, ?)
                                        ''', (sender_id, 'open', timestamp))
                                        db.commit()  # Salva ambas as alterações (contador e status)

                                        # Busca o novo valor do contador para logar (opcional)
                                        cursor.execute("SELECT value FROM counters WHERE counter_name = ?",
                                                       ('new_conversation_count',))
                                        count_result = cursor.fetchone()
                                        current_count = count_result['value'] if count_result else 'ERRO'
                                        print(f"CONTADOR DE NOVAS CONVERSAS: {current_count}")
                                        print(f"Conversa com {sender_id} marcada como ABERTA no DB.")
                                    else:
                                        # Atualiza apenas o timestamp da conversa existente
                                        cursor.execute("UPDATE conversations SET last_update = ? WHERE sender_id = ?",
                                                       (timestamp, sender_id))
                                        db.commit()  # Salva a atualização do timestamp
                                        print(
                                            f"Mensagem recebida na conversa aberta com {sender_id} (timestamp atualizado no DB).")

            # Retorna 200 OK para a Meta mesmo se ocorrer um erro interno no processamento
            # A Meta se importa apenas em saber se você recebeu o webhook
            return Response(status=200)

        except sqlite3.Error as e:
            print(f"Erro de Banco de Dados SQLite durante POST: {e}")
            if db: db.rollback()  # Desfaz alterações se possível
            # Não aborte a requisição, apenas logue o erro e retorne 200
            return Response(status=200)
        except Exception as e:
            print(f"Erro inesperado durante POST: {e}")
            if db: db.rollback()
            # Não aborte a requisição, apenas logue o erro e retorne 200
            return Response(status=200)
        # O fechamento da conexão é tratado automaticamente por @app.teardown_appcontext

    else:
        abort(405)  # Method Not Allowed


# --- Rotas para verificar estado (Modificadas para usar DB) ---

@app.route('/count', methods=['GET'])
def get_count():
    """Retorna as contagens atuais de conversas do banco de dados."""
    try:
        db = get_db()
        cursor = db.cursor()

        counters = {}
        for counter_name in ['new_conversation_count', 'open_conversation_count', 'closed_conversation_count']:
            cursor.execute("SELECT value FROM counters WHERE counter_name = ?", (counter_name,))
            result = cursor.fetchone()
            counters[counter_name] = result['value'] if result else 0

        return json.dumps(counters)
    except sqlite3.Error as e:
        print(f"Erro de DB em /count: {e}")
        return json.dumps({"error": "Erro ao acessar banco de dados"}), 500
    except Exception as e:
        print(f"Erro inesperado em /count: {e}")
        return json.dumps({"error": "Erro interno do servidor"}), 500


@app.route('/status', methods=['GET'])
def get_all_statuses():
    """Retorna o status de todas as conversas do banco de dados."""
    all_statuses = {}
    try:
        db = get_db()
        cursor = db.cursor()
        # Seleciona todas as colunas e ordena pelas mais recentes
        cursor.execute("SELECT sender_id, status, last_update FROM conversations ORDER BY last_update DESC")
        results = cursor.fetchall()  # Pega todas as linhas
        for row in results:
            # Converte o objeto sqlite3.Row para um dicionário padrão
            all_statuses[row['sender_id']] = dict(row)
        return json.dumps(all_statuses)
    except sqlite3.Error as e:
        print(f"Erro de DB em /status: {e}")
        return json.dumps({"error": "Erro ao acessar banco de dados"}), 500
    except Exception as e:
        print(f"Erro inesperado em /status: {e}")
        return json.dumps({"error": "Erro interno do servidor"}), 500


@app.route('/close/<sender_id>', methods=['POST'])
def close_conversation(sender_id):
    """Marca uma conversa específica como 'closed' no banco de dados."""
    try:
        db = get_db()
        cursor = db.cursor()

        # Primeiro verifica se a conversa existe e está aberta
        cursor.execute("SELECT status FROM conversations WHERE sender_id = ?", (sender_id,))
        result = cursor.fetchone()

        if result and result['status'] == 'open':
            # Atualiza o status para closed
            cursor.execute("UPDATE conversations SET status = ? WHERE sender_id = ?",
                           ('closed', sender_id))

            # Decrementa contador de abertas
            cursor.execute("UPDATE counters SET value = value - 1 WHERE counter_name = ?",
                           ('open_conversation_count',))

            # Incrementa contador de fechadas
            cursor.execute("UPDATE counters SET value = value + 1 WHERE counter_name = ?",
                           ('closed_conversation_count',))

            # Decrementa contador de novas conversas
            cursor.execute("UPDATE counters SET value = value - 1 WHERE counter_name = ?",
                           ('new_conversation_count',))

            db.commit()
            print(f"Conversa com {sender_id} marcada como FECHADA no DB.")
            return json.dumps({"status": "closed"})
        elif result and result['status'] == 'closed':
            # Já estava fechada
            return json.dumps({"status": "already_closed"})
        else:
            # Se o sender_id não existia na tabela
            print(f"Tentativa de fechar conversa com {sender_id}, mas não foi encontrada.")
            return json.dumps({"status": "not_found"}), 404
    except sqlite3.Error as e:
        print(f"Erro de DB em /close/{sender_id}: {e}")
        if db: db.rollback()
        return json.dumps({"error": "Erro ao acessar banco de dados"}), 500
    except Exception as e:
        print(f"Erro inesperado em /close/{sender_id}: {e}")
        if db: db.rollback()
        return json.dumps({"error": "Erro interno do servidor"}), 500


@app.route('/recalculate-counters', methods=['POST'])
def recalculate_counters():
    """Recalcula os contadores com base nos registros atuais."""
    try:
        db = get_db()
        cursor = db.cursor()

        # Conta conversas abertas
        cursor.execute("SELECT COUNT(*) as count FROM conversations WHERE status = 'open'")
        open_count = cursor.fetchone()['count']

        # Conta conversas fechadas
        cursor.execute("SELECT COUNT(*) as count FROM conversations WHERE status = 'closed'")
        closed_count = cursor.fetchone()['count']

        # Atualiza os contadores
        cursor.execute("UPDATE counters SET value = ? WHERE counter_name = ?",
                       (open_count, 'open_conversation_count'))
        cursor.execute("UPDATE counters SET value = ? WHERE counter_name = ?",
                       (closed_count, 'closed_conversation_count'))
        cursor.execute("UPDATE counters SET value = ? WHERE counter_name = ?",
                       (open_count, 'new_conversation_count'))

        db.commit()

        return json.dumps({
            "success": True,
            "open_conversation_count": open_count,
            "closed_conversation_count": closed_count,
            "new_conversation_count": open_count
        })
    except sqlite3.Error as e:
        print(f"Erro de DB em /recalculate-counters: {e}")
        if db: db.rollback()
        return json.dumps({"error": "Erro ao acessar banco de dados"}), 500
    except Exception as e:
        print(f"Erro inesperado em /recalculate-counters: {e}")
        if db: db.rollback()
        return json.dumps({"error": "Erro interno do servidor"}), 500


# --- Inicialização ---
if __name__ == '__main__':
    # Garante que o banco de dados e as tabelas sejam criados na inicialização
    init_db()
    port = int(os.environ.get("PORT", 5000))
    # !! MUDE debug=False para produção !!
    app.run(host='0.0.0.0', port=port, debug=True)