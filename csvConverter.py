import sqlite3
import csv
import os
import time

# --- Configuração ---
DATABASE_FILE = 'whatsapp_data.db'  # Nome do arquivo do banco de dados
SUMMARY_CSV_FILE = 'conversation_summary.csv' # Nome do arquivo CSV de saída do sumário

def export_conversation_summary(db_file, csv_file):
    """
    Consulta o banco de dados SQLite, calcula as contagens de conversas
    (novas, abertas, fechadas) e exporta para um arquivo CSV sumarizado.

    Args:
        db_file (str): Caminho para o arquivo do banco de dados SQLite.
        csv_file (str): Caminho para o arquivo CSV de saída do sumário.
    """
    if not os.path.exists(db_file):
        print(f"Erro: Arquivo de banco de dados '{db_file}' não encontrado.")
        return

    print(f"Conectando ao banco de dados '{db_file}' para gerar sumário...")
    conn = None
    try:
        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()

        # 1. Obter contagem de NOVAS conversas da tabela 'counters'
        print("Lendo contador de novas conversas...")
        cursor.execute("SELECT value FROM counters WHERE counter_name = ?", ('new_conversation_count',))
        new_count_result = cursor.fetchone()
        # Se o contador não existir por algum motivo, assume 0
        new_conversation_count = new_count_result[0] if new_count_result else 0
        print(f"Novas Conversas (total): {new_conversation_count}")

        # 2. Obter contagem de conversas ABERTAS da tabela 'conversations'
        print("Contando conversas abertas...")
        cursor.execute("SELECT COUNT(*) FROM conversations WHERE status = ?", ('open',))
        open_count_result = cursor.fetchone()
        open_conversation_count = open_count_result[0] if open_count_result else 0
        print(f"Conversas Abertas (atualmente): {open_conversation_count}")

        # 3. Obter contagem de conversas FECHADAS da tabela 'conversations'
        print("Contando conversas fechadas...")
        cursor.execute("SELECT COUNT(*) FROM conversations WHERE status = ?", ('closed',))
        closed_count_result = cursor.fetchone()
        closed_conversation_count = closed_count_result[0] if closed_count_result else 0
        print(f"Conversas Encerradas: {closed_conversation_count}")

        # 4. Escrever os dados sumarizados no arquivo CSV
        print(f"Escrevendo sumário para '{csv_file}'...")
        with open(csv_file, 'w', newline='', encoding='utf-8') as outfile:
            writer = csv.writer(outfile)

            # Escreve o cabeçalho
            writer.writerow(['Metrica', 'Valor'])

            # Escreve as linhas de dados para cada métrica
            writer.writerow(['Novas Conversas (Total)', new_conversation_count])
            writer.writerow(['Conversas Abertas (Atual)', open_conversation_count])
            writer.writerow(['Conversas Encerradas', closed_conversation_count])

        print(f"Sumário exportado com sucesso para '{csv_file}'.")

    except sqlite3.Error as e:
        print(f"Erro de SQLite ao gerar sumário: {e}")
    except IOError as e:
        print(f"Erro de I/O ao escrever o arquivo CSV '{csv_file}': {e}")
    except Exception as e:
        print(f"Ocorreu um erro inesperado ao gerar o sumário: {e}")
    finally:
        if conn:
            conn.close()
            print(f"Conexão com o banco de dados '{db_file}' fechada.")

# --- Execução Principal ---
if __name__ == "__main__":
    print(f"--- Iniciando exportação do sumário do banco de dados '{DATABASE_FILE}' ---")
    start_time = time.time()

    # Chama a função para exportar o sumário
    export_conversation_summary(DATABASE_FILE, SUMMARY_CSV_FILE)

    end_time = time.time()
    print(f"--- Exportação do sumário concluída em {end_time - start_time:.2f} segundos ---")