"""Armazenamento persistente de aniversários no PostgreSQL (não usa disco efêmero do Render)."""
import os
from datetime import date
import psycopg


def configurado():
    return bool(os.getenv("DATABASE_URL", "").strip())


def conexao():
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("Configure DATABASE_URL com um PostgreSQL persistente.")
    return psycopg.connect(url, connect_timeout=8)


def listar_aniversarios():
    if not configurado():
        return {}
    with conexao() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS smartone_aniversarios "
                "(cliente_id TEXT PRIMARY KEY, data_nascimento DATE NOT NULL, "
                "atualizado_em TIMESTAMPTZ NOT NULL DEFAULT NOW())"
            )
            cur.execute("SELECT cliente_id, data_nascimento FROM smartone_aniversarios")
            return {cliente_id: nascimento.isoformat() for cliente_id, nascimento in cur.fetchall()}


def salvar_aniversario(cliente_id, nascimento):
    if not cliente_id or len(cliente_id) > 200:
        raise ValueError("Cliente inválido.")
    if not isinstance(nascimento, date) or nascimento.year < 1900 or nascimento > date.today():
        raise ValueError("Data de nascimento inválida.")
    with conexao() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS smartone_aniversarios "
                "(cliente_id TEXT PRIMARY KEY, data_nascimento DATE NOT NULL, "
                "atualizado_em TIMESTAMPTZ NOT NULL DEFAULT NOW())"
            )
            cur.execute(
                "INSERT INTO smartone_aniversarios (cliente_id, data_nascimento) "
                "VALUES (%s, %s) ON CONFLICT (cliente_id) DO UPDATE "
                "SET data_nascimento = EXCLUDED.data_nascimento, atualizado_em = NOW()",
                (cliente_id, nascimento),
            )
