import pandas as pd
import psycopg2
import os
from datetime import datetime

# --- FUNÇÃO DE CONEXÃO ATUALIZADA ---
def get_db_connection():
    """
    Cria e retorna uma conexão com o banco.
    Tenta usar a variável de ambiente DATABASE_URL (para rodar no Docker).
    Se não encontrar, usa as configurações locais (para rodar no seu PC).
    """
    database_url = os.environ.get('DATABASE_URL')

    if database_url:
        print("Conectando via DATABASE_URL (ambiente Docker)...")
        # Ajusta a URL para o formato que o psycopg2 espera
        conn_str = database_url.replace("postgresql://", "postgres://")
        return psycopg2.connect(conn_str)
    else:
        print("AVISO: DATABASE_URL não definida. Usando configuração local (localhost).")
        return psycopg2.connect(
            host="localhost",
            database="pedidos_db",
            user="postgres",
            password="2025",
            client_encoding='utf8'
        )

def migrar_dados_pedidos():
    conn = None
    planilha_path = os.path.join("dados", "Status_dos_pedidos.xlsm")
    
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        print("Limpando registros antigos importados da planilha...")
        cur.execute("DELETE FROM public.pedidos_tb WHERE perfil_alteracao = 'Importada Planilha';")

        status_aguardando_chegada = "Aguardando Chegada"
        print(f"Buscando o ID para o status: '{status_aguardando_chegada}'...")
        cur.execute("SELECT id FROM public.status_td WHERE nome_status = %s;", (status_aguardando_chegada,))
        result = cur.fetchone()
        
        if result is None:
            print(f"Erro: O status '{status_aguardando_chegada}' não foi encontrado na tabela status_td.")
            return
            
        status_id = result[0]
        print(f"ID para '{status_aguardando_chegada}' encontrado: {status_id}")

        print(f"Lendo todos os dados da planilha '{planilha_path}'...")
        df_excel = pd.read_excel(planilha_path, sheet_name=0)
        df_excel.dropna(how='all', inplace=True)
        
        print(f"Encontrados {len(df_excel)} pedidos na planilha.")

        df_excel.columns = df_excel.columns.str.strip().str.lower().str.replace(' ', '_')

        df_excel = df_excel.rename(columns={
            "pedido": "codigo_pedido",
            "equipamento": "equipamento",
            "pv": "pv",
            "servico": "descricao_servico",
            "data_status": "data_criacao",
            "qtd_maquinas": "quantidade"
        })

        df_excel['prioridade'] = range(1, len(df_excel) + 1)
        
        print("Inserindo novos dados na tabela pedidos_tb...")
        pedidos_processados = 0
        for index, row in df_excel.iterrows():
            # Se 'codigo_pedido' for vazio (NaN no pandas), converte para None (NULL no SQL)
            codigo_pedido = row.get('codigo_pedido')
            if pd.isna(codigo_pedido):
                codigo_pedido = None

            data_criacao = datetime.now()
            perfil_altecao = "Importada Planilha"
            status_urgente = False

            # Query sem a cláusula ON CONFLICT para permitir múltiplos nulos
            query = """
            INSERT INTO public.pedidos_tb (codigo_pedido, equipamento, pv, descricao_servico, status_id, data_criacao, quantidade, prioridade, perfil_alteracao, urgente)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
            """
            cur.execute(query, (
                codigo_pedido, 
                row.get('equipamento'), 
                row.get('pv'),
                row.get('descricao_servico'), 
                status_id, 
                data_criacao,
                row.get('quantidade'), 
                row.get('prioridade'), 
                perfil_altecao,
                status_urgente
            ))
            pedidos_processados += 1

        conn.commit()
        print(f"Dados migrados com sucesso! Total de {pedidos_processados} pedidos processados.")

    except psycopg2.Error as e:
        print(f"Erro no banco de dados: {e}")
        print("Isso pode ter ocorrido se houver valores duplicados na coluna 'pedido' do seu arquivo Excel.")
        if conn:
            conn.rollback()
    except FileNotFoundError:
        print(f"Erro: O arquivo de planilha não foi encontrado. Verifique se o caminho '{planilha_path}' está correto.")
    except KeyError as e:
        print(f"Erro: A coluna {e} não foi encontrada. Verifique os nomes das colunas na sua planilha Excel.")
    except Exception as e:
        print(f"Ocorreu um erro inesperado: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            cur.close()
            conn.close()

if __name__ == "__main__":
    migrar_dados_pedidos()