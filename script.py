import os
import json
import hmac
import hashlib
import time
from flask import Flask, request, abort, Response
from threading import Lock # Para segurança de thread com variáveis globais

app = Flask(__name__)

# --- Configuração ---
VERIFY_TOKEN = os.environ.get("WHATSAPP_VERIFY_TOKEN", "zas")
APP_SECRET = os.environ.get("WHATSAPP_APP_SECRET", "12481c3321479a59724a976f1241de06")

# --- Gerenciamento de Estado (Simples - Use um BD em produção!) ---
# Dicionário para guardar o status das conversas { sender_id: {'status': 'open'/'closed', 'last_update': timestamp} }
conversation_status = {}
# Contador de novas conversas
new_conversation_count = 0
# Lock para proteger o acesso concorrente ao contador e ao dicionário
data_lock = Lock()

# --- Endpoint do Webhook ---

@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    global new_conversation_count # Precisamos declarar global para modificar
    global conversation_status

    if request.method == 'GET':
        # --- Verificação do Webhook (GET) ---
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
        if not signature_header.startswith('sha256='):
            print("Erro: Assinatura inválida (formato incorreto).")
            abort(403)
        signature_hash = signature_header.split('=')[1]
        request_body = request.data
        expected_hash = hmac.new(
            APP_SECRET.encode('utf-8'), request_body, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature_hash, expected_hash):
            print("Erro: Assinatura inválida (hashes não correspondem).")
            abort(403)
        print("Assinatura verificada com sucesso.")

        # 2. Processar o Payload JSON
        data = request.get_json()
        # print("Payload recebido:", json.dumps(data, indent=2))

        if data.get('object') == 'whatsapp_business_account':
            try:
                for entry in data.get('entry', []):
                    for change in entry.get('changes', []):
                        value = change.get('value', {})
                        if 'messages' in value:
                            for message in value.get('messages', []):
                                if 'from' in message and message.get('type'):
                                    sender_id = message['from']
                                    message_type = message['type']
                                    timestamp = int(message['timestamp']) # Converte para int

                                    print(f"Mensagem recebida de {sender_id} (Tipo: {message_type})")

                                    # --- LÓGICA PARA CONTAR NOVA CONVERSA ---
                                    with data_lock: # Garante acesso seguro às variáveis compartilhadas
                                        is_new_conversation = False
                                        current_status = conversation_status.get(sender_id)

                                        if not current_status:
                                            # Primeira mensagem deste remetente
                                            is_new_conversation = True
                                            print(f"Primeira mensagem detectada de {sender_id}.")
                                        elif current_status['status'] == 'closed':
                                            # Mensagem recebida após a conversa ser fechada
                                            # (Você precisará implementar a lógica de 'fechar')
                                            is_new_conversation = True
                                            print(f"Nova conversa detectada de {sender_id} (anterior estava fechada).")
                                        # Adicione outras lógicas se necessário (ex: tempo limite)
                                        # elif time.time() - current_status.get('last_update', 0) > TEMPO_LIMITE_SEGUNDOS:
                                        #    is_new_conversation = True
                                        #    print(f"Nova conversa detectada de {sender_id} (tempo limite excedido).")


                                        if is_new_conversation:
                                            new_conversation_count += 1
                                            conversation_status[sender_id] = {
                                                'status': 'open',
                                                'last_update': timestamp
                                            }
                                            print(f"CONTADOR DE NOVAS CONVERSAS: {new_conversation_count}")
                                            print(f"Conversa com {sender_id} marcada como ABERTA.")
                                        else:
                                             # Atualiza apenas o timestamp da conversa existente
                                            conversation_status[sender_id]['last_update'] = timestamp
                                            print(f"Mensagem recebida na conversa aberta com {sender_id}.")


            except Exception as e:
                print(f"Erro ao processar payload: {e}")
                pass

        return Response(status=200)
    else:
        abort(405)

# --- Rotas para verificar estado (Exemplo) ---
@app.route('/count', methods=['GET'])
def get_count():
    global new_conversation_count
    with data_lock:
        return json.dumps({"new_conversation_count": new_conversation_count})

@app.route('/status', methods=['GET'])
def get_all_statuses():
    global conversation_status
    with data_lock:
        # Cria cópia para evitar problemas de concorrência ao retornar
        status_copy = conversation_status.copy()
    return json.dumps(status_copy)

# Exemplo de rota para FECHAR uma conversa (apenas para teste)
@app.route('/close/<sender_id>', methods=['POST'])
def close_conversation(sender_id):
    global conversation_status
    with data_lock:
        if sender_id in conversation_status:
            conversation_status[sender_id]['status'] = 'closed'
            print(f"Conversa com {sender_id} marcada como FECHADA manualmente.")
            return json.dumps({"status": "closed"})
        else:
            return json.dumps({"status": "not_found"}), 404

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=True) # Mude debug=False para produção