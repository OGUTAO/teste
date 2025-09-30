import pandas as pd
from sqlalchemy import create_engine
from datetime import datetime, timedelta
import os
import pytz
import traceback

# --- CONFIGURAÇÕES ---
DB_URL = os.environ.get('DATABASE_URL', 'postgresql+psycopg2://postgres:2025@localhost:5432/pedidos_db')
engine = create_engine(DB_URL)
FUSO_BRASILIA = pytz.timezone("America/Sao_Paulo")
META_SEMANAL = 200

def to_safe_dict(df):
    """Converte um DataFrame para uma lista de dicionários, garantindo que valores problemáticos como NaT sejam convertidos para None."""
    if df.empty:
        return []
    # Converte NaT (Not a Time) para None, que é serializável para JSON (null)
    return df.astype(object).where(pd.notnull(df), None).to_dict('records')

def carregar_dados_completos():
    """Carrega e processa todos os dados necessários do banco de dados."""
    query = """
        SELECT 
            p.id, p.status_id, p.equipamento, p.pv, p.descricao_servico,
            s.nome_status, p.data_criacao, p.quantidade, p.urgente,
            p.data_conclusao, i.nome as nome_imagem, p.prioridade
        FROM 
            pedidos_tb p
        LEFT JOIN
            status_td s ON p.status_id = s.id
        LEFT JOIN                               
            imagem_td i ON p.imagem_id = i.id;
    """
    try:
        df = pd.read_sql(query, engine)
        if df.empty:
            return pd.DataFrame()

        # 1. Limpeza de dados não-data
        df['nome_status'].fillna('Não Definido', inplace=True)
        df['pv'].fillna('N/A', inplace=True)
        df['equipamento'].fillna('Não informado', inplace=True)
        df['descricao_servico'].fillna('N/A', inplace=True)
        df['nome_imagem'].fillna('N/A', inplace=True)
        df['quantidade'] = pd.to_numeric(df['quantidade'], errors='coerce').fillna(0).astype(int)
        df['prioridade'] = pd.to_numeric(df['prioridade'], errors='coerce').fillna(9999).astype(int)
        df['urgente'].fillna(False, inplace=True)

        # 2. Converte colunas de data, mantendo o tipo de data para cálculos
        df['data_criacao'] = pd.to_datetime(df['data_criacao'], errors='coerce', utc=True)
        df['data_conclusao'] = pd.to_datetime(df['data_conclusao'], errors='coerce', utc=True)
        
        if not df['data_criacao'].isnull().all():
            df['data_criacao'] = df['data_criacao'].dt.tz_convert(FUSO_BRASILIA)
        if not df['data_conclusao'].isnull().all():
            df['data_conclusao'] = df['data_conclusao'].dt.tz_convert(FUSO_BRASILIA)

        # 3. Ordena os dados
        df.sort_values(by=['urgente', 'prioridade'], ascending=[False, True], inplace=True)
        
        return df
    except Exception as e:
        print(f"Erro ao carregar e limpar dados do banco: {e}")
        traceback.print_exc()
        return pd.DataFrame()

def get_painel_data():
    """Função principal que busca e organiza todos os dados para a API do painel."""
    df_full = carregar_dados_completos()
    
    dados_vazios = { "prioridades": [], "backlog": {"lista": [], "total": 0}, "aguardando": {"lista": [], "total": 0}, "pendentes": {"lista": [], "total": 0}, "em_montagem_fora_prioridade": {"lista": [], "total": 0}, "concluidos_hoje": {"lista": [], "total_pedidos": 0, "total_maquinas": 0}, "cancelados_hoje": {"lista": [], "total_pedidos": 0, "total_maquinas": 0}, "metricas": { "total_mes_pedidos": 0, "total_mes_maquinas": 0, "media_diaria_pedidos": 0.0, "media_diaria_maquinas": 0.0, "recorde_dia_data": "N/A", "recorde_dia_pedidos": 0, "recorde_dia_maquinas": 0 }, "desempenho_semanal": [], "meta_semanal": META_SEMANAL }

    if df_full.empty:
        return dados_vazios

    try:
        # Adicionado STATUS_ID_MONTAGEM
        STATUS_ID_CONCLUIDO = 4; STATUS_ID_CANCELADO = 6; STATUS_ID_PENDENTE = 5; STATUS_ID_BACKLOG = 2; STATUS_ID_AGUARDANDO = 1; STATUS_ID_MONTAGEM = 3
        
        hoje = datetime.now(FUSO_BRASILIA); inicio_do_dia = hoje.replace(hour=0, minute=0, second=0, microsecond=0)
        df_em_andamento = df_full[~df_full['status_id'].isin([STATUS_ID_CONCLUIDO, STATUS_ID_CANCELADO])]
        
        prioridades_df = df_em_andamento[~df_em_andamento['status_id'].isin([STATUS_ID_AGUARDANDO, STATUS_ID_PENDENTE])].head(4)
        prioridades_ids = set(prioridades_df['id']) # Pega os IDs dos 4 prioritários

        # Lógica para a nova coluna
        montagem_fora_df = df_em_andamento[
            (df_em_andamento['status_id'] == STATUS_ID_MONTAGEM) &
            (~df_em_andamento['id'].isin(prioridades_ids))
        ]

        backlog_df = df_em_andamento[df_em_andamento['status_id'] == STATUS_ID_BACKLOG]
        aguardando_df = df_em_andamento[df_em_andamento['status_id'] == STATUS_ID_AGUARDANDO]
        pendentes_df = df_em_andamento[df_em_andamento['status_id'] == STATUS_ID_PENDENTE]
        
        df_com_data_final = df_full.dropna(subset=['data_conclusao'])
        df_finalizados_hoje = df_com_data_final[df_com_data_final['data_conclusao'] >= inicio_do_dia]
        concluidos_hoje_df = df_finalizados_hoje[df_finalizados_hoje['status_id'] == STATUS_ID_CONCLUIDO]
        cancelados_hoje_df = df_finalizados_hoje[df_finalizados_hoje['status_id'] == STATUS_ID_CANCELADO]

        # Cálculos de Métricas
        inicio_mes_atual = hoje.replace(day=1, hour=0, minute=0, second=0)
        df_concluidos_mes_atual = df_com_data_final[(df_com_data_final['status_id'] == STATUS_ID_CONCLUIDO) & (df_com_data_final['data_conclusao'] >= inicio_mes_atual)]
        total_mes_pedidos = len(df_concluidos_mes_atual)
        total_mes_maquinas = int(df_concluidos_mes_atual['quantidade'].sum()) if not df_concluidos_mes_atual.empty else 0
        dias_corridos_mes = hoje.day
        media_diaria_pedidos = total_mes_pedidos / dias_corridos_mes if dias_corridos_mes > 0 else 0
        media_diaria_maquinas = total_mes_maquinas / dias_corridos_mes if dias_corridos_mes > 0 else 0

        recorde_dia_data, recorde_dia_pedidos, recorde_dia_maquinas = "N/A", 0, 0
        if not df_concluidos_mes_atual.empty:
            recorde_dia_series = df_concluidos_mes_atual.groupby(df_concluidos_mes_atual['data_conclusao'].dt.date)['quantidade'].sum()
            if not recorde_dia_series.empty:
                recorde_dia = recorde_dia_series.idxmax()
                df_recorde = df_concluidos_mes_atual[df_concluidos_mes_atual['data_conclusao'].dt.date == recorde_dia]
                recorde_dia_data = recorde_dia.strftime('%d/%m/%Y'); recorde_dia_pedidos = len(df_recorde); recorde_dia_maquinas = int(df_recorde['quantidade'].sum())

        df_concluidos_full = df_com_data_final[df_com_data_final['status_id'] == STATUS_ID_CONCLUIDO].copy()
        desempenho_semanal_list = []
        if not df_concluidos_full.empty:
            df_concluidos_full['semana_inicio'] = df_concluidos_full['data_conclusao'].dt.tz_localize(None).dt.to_period('W-MON').apply(lambda p: p.start_time.date())
            desempenho_semanal = df_concluidos_full.groupby('semana_inicio')['quantidade'].sum().tail(4)
            desempenho_semanal_list = [{"semana": k.strftime('%Y-%m-%d'), "valor": int(v)} for k, v in desempenho_semanal.items()]

        # Preparação final dos dados para JSON
        dados = {
            "prioridades": to_safe_dict(prioridades_df),
            "backlog": {"lista": to_safe_dict(backlog_df.head(5)), "total": len(backlog_df)},
            "aguardando": {"lista": to_safe_dict(aguardando_df.head(5)), "total": len(aguardando_df)},
            "pendentes": {"lista": to_safe_dict(pendentes_df.head(5)), "total": len(pendentes_df)},
            "em_montagem_fora_prioridade": {"lista": to_safe_dict(montagem_fora_df.head(5)), "total": len(montagem_fora_df)},
            "concluidos_hoje": {"lista": to_safe_dict(concluidos_hoje_df), "total_pedidos": len(concluidos_hoje_df), "total_maquinas": int(concluidos_hoje_df['quantidade'].sum())},
            "cancelados_hoje": {"lista": to_safe_dict(cancelados_hoje_df), "total_pedidos": len(cancelados_hoje_df), "total_maquinas": int(cancelados_hoje_df['quantidade'].sum())},
            "metricas": { "total_mes_pedidos": total_mes_pedidos, "total_mes_maquinas": total_mes_maquinas, "media_diaria_pedidos": round(media_diaria_pedidos, 1), "media_diaria_maquinas": round(media_diaria_maquinas, 1), "recorde_dia_data": recorde_dia_data, "recorde_dia_pedidos": recorde_dia_pedidos, "recorde_dia_maquinas": recorde_dia_maquinas },
            "desempenho_semanal": desempenho_semanal_list,
            "meta_semanal": META_SEMANAL
        }
        return dados
    except Exception as e:
        print(f"ERRO CRÍTICO ao processar dados para o painel: {e}")
        traceback.print_exc()
        return {"error": "Falha ao processar os dados.", "details": str(e)}