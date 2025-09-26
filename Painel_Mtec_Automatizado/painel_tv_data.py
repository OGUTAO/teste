import pandas as pd
from sqlalchemy import create_engine
from datetime import datetime, timedelta
import os
import pytz

# --- CONFIGURAÇÕES ---
DB_URL = os.environ.get('DATABASE_URL', 'postgresql+psycopg2://postgres:2025@localhost:5432/pedidos_db')
engine = create_engine(DB_URL)
FUSO_BRASILIA = pytz.timezone("America/Sao_Paulo")
META_SEMANAL = 200

def carregar_dados_completos():
    """Carrega e processa todos os dados necessários do banco de dados."""
    query = """
        SELECT 
            p.id, p.status_id, p.equipamento, p.pv, p.descricao_servico,
            s.nome_status, p.data_criacao, p.quantidade, p.urgente,
            p.data_conclusao, i.nome as nome_imagem, p.prioridade
        FROM 
            pedidos_tb p
        JOIN
            status_td s ON p.status_id = s.id
        LEFT JOIN                               
            imagem_td i ON p.imagem_id = i.id 
        ORDER BY
            p.urgente DESC, p.prioridade ASC;
    """
    try:
        df = pd.read_sql(query, engine)
        if df.empty:
            return pd.DataFrame()
            
        # Converte datas, tratando possíveis erros
        df['data_criacao'] = pd.to_datetime(df['data_criacao'], errors='coerce', utc=True).dt.tz_convert(FUSO_BRASILIA)
        df['data_conclusao'] = pd.to_datetime(df['data_conclusao'], errors='coerce', utc=True).dt.tz_convert(FUSO_BRASILIA)
        return df
    except Exception as e:
        print(f"Erro ao carregar dados do banco: {e}")
        return pd.DataFrame()


def get_painel_data():
    """Função principal que busca e organiza todos os dados para a API do painel."""
    df_full = carregar_dados_completos()
    
    dados_vazios = {
        "prioridades": [], "backlog": {"lista": [], "total": 0}, "aguardando": {"lista": [], "total": 0},
        "pendentes": {"lista": [], "total": 0},
        "concluidos_hoje": {"lista": [], "total_pedidos": 0, "total_maquinas": 0},
        "cancelados_hoje": {"lista": [], "total_pedidos": 0, "total_maquinas": 0},
        "metricas": {
            "total_mes_pedidos": 0, "total_mes_maquinas": 0, "media_diaria_pedidos": 0.0, "media_diaria_maquinas": 0.0,
            "recorde_dia_data": "N/A", "recorde_dia_pedidos": 0, "recorde_dia_maquinas": 0
        },
        "desempenho_semanal": [], "meta_semanal": META_SEMANAL
    }

    if df_full.empty:
        return dados_vazios

    try:
        # --- Filtros de Status ---
        STATUS_ID_CONCLUIDO = 4
        STATUS_ID_CANCELADO = 6
        STATUS_ID_PENDENTE = 5
        STATUS_ID_BACKLOG = 2
        STATUS_ID_AGUARDANDO = 1

        hoje = datetime.now(FUSO_BRASILIA)
        inicio_do_dia = hoje.replace(hour=0, minute=0, second=0, microsecond=0)

        df_em_andamento = df_full[~df_full['status_id'].isin([STATUS_ID_CONCLUIDO, STATUS_ID_CANCELADO])]
        
        # --- Processamento dos Dados ---
        prioridades = df_em_andamento[~df_em_andamento['status_id'].isin([STATUS_ID_AGUARDANDO, STATUS_ID_PENDENTE])].head(4).to_dict('records')
        backlog = df_em_andamento[df_em_andamento['status_id'] == STATUS_ID_BACKLOG].to_dict('records')
        aguardando = df_em_andamento[df_em_andamento['status_id'] == STATUS_ID_AGUARDANDO].to_dict('records')
        pendentes = df_em_andamento[df_em_andamento['status_id'] == STATUS_ID_PENDENTE].to_dict('records')
        
        df_finalizados_hoje = df_full[df_full['data_conclusao'] >= inicio_do_dia]
        concluidos_hoje = df_finalizados_hoje[df_finalizados_hoje['status_id'] == STATUS_ID_CONCLUIDO].to_dict('records')
        cancelados_hoje = df_finalizados_hoje[df_finalizados_hoje['status_id'] == STATUS_ID_CANCELADO].to_dict('records')

        inicio_mes_atual = hoje.replace(day=1, hour=0, minute=0, second=0)
        df_concluidos_mes_atual = df_full[(df_full['status_id'] == STATUS_ID_CONCLUIDO) & (df_full['data_conclusao'] >= inicio_mes_atual)]
        
        total_mes_pedidos = len(df_concluidos_mes_atual)
        total_mes_maquinas = int(df_concluidos_mes_atual['quantidade'].sum())
        dias_corridos_mes = hoje.day
        media_diaria_pedidos = total_mes_pedidos / dias_corridos_mes if dias_corridos_mes > 0 else 0
        media_diaria_maquinas = total_mes_maquinas / dias_corridos_mes if dias_corridos_mes > 0 else 0

        recorde_dia_data, recorde_dia_pedidos, recorde_dia_maquinas = "N/A", 0, 0
        if not df_concluidos_mes_atual.empty and not df_concluidos_mes_atual['data_conclusao'].isnull().all():
            producao_diaria = df_concluidos_mes_atual.dropna(subset=['data_conclusao']).groupby(df_concluidos_mes_atual['data_conclusao'].dt.date)
            if producao_diaria.groups:
                recorde_dia = producao_diaria['quantidade'].sum().idxmax()
                df_recorde = df_concluidos_mes_atual[df_concluidos_mes_atual['data_conclusao'].dt.date == recorde_dia]
                recorde_dia_data = recorde_dia.strftime('%d/%m/%Y')
                recorde_dia_pedidos = len(df_recorde)
                recorde_dia_maquinas = int(df_recorde['quantidade'].sum())

        df_concluidos_full = df_full[(df_full['status_id'] == STATUS_ID_CONCLUIDO) & (df_full['data_conclusao'].notna())].copy()
        desempenho_semanal_list = []
        if not df_concluidos_full.empty:
            df_concluidos_full['semana_inicio'] = df_concluidos_full['data_conclusao'].dt.to_period('W-MON').apply(lambda p: p.start_time.date())
            desempenho_semanal = df_concluidos_full.groupby('semana_inicio')['quantidade'].sum().tail(4)
            desempenho_semanal_list = [{"semana": k.strftime('%d/%m'), "valor": int(v)} for k, v in desempenho_semanal.items()]

        dados = {
            "prioridades": prioridades,
            "backlog": {"lista": backlog[:5], "total": len(backlog)},
            "aguardando": {"lista": aguardando[:5], "total": len(aguardando)},
            "pendentes": {"lista": pendentes[:5], "total": len(pendentes)},
            "concluidos_hoje": {"lista": concluidos_hoje, "total_pedidos": len(concluidos_hoje), "total_maquinas": int(pd.DataFrame(concluidos_hoje)['quantidade'].sum()) if concluidos_hoje else 0},
            "cancelados_hoje": {"lista": cancelados_hoje, "total_pedidos": len(cancelados_hoje), "total_maquinas": int(pd.DataFrame(cancelados_hoje)['quantidade'].sum()) if cancelados_hoje else 0},
            "metricas": {
                "total_mes_pedidos": total_mes_pedidos, "total_mes_maquinas": total_mes_maquinas,
                "media_diaria_pedidos": round(media_diaria_pedidos, 1), "media_diaria_maquinas": round(media_diaria_maquinas, 1),
                "recorde_dia_data": recorde_dia_data, "recorde_dia_pedidos": recorde_dia_pedidos, "recorde_dia_maquinas": recorde_dia_maquinas
            },
            "desempenho_semanal": desempenho_semanal_list,
            "meta_semanal": META_SEMANAL
        }
        return dados

    except Exception as e:
        print(f"ERRO CRÍTICO ao processar dados para o painel: {e}")
        import traceback
        traceback.print_exc()
        return {"error": "Falha ao processar os dados.", "details": str(e)}
