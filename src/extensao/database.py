"""Exporta as questões estruturadas para SQLite, SQL e CSV."""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

CAMPOS = (
    "numero_exame", "data_exame", "tipo_prova", "numero_questao", "materia",
    "enunciado", "alternativa_a", "alternativa_b", "alternativa_c", "alternativa_d",
    "resposta_preliminar", "resposta_definitiva",
    "questao_extraida", "fonte_prova", "fonte_gabarito_preliminar",
    "fonte_gabarito_definitivo", "gabarito_definitivo",
)

DDL = """CREATE TABLE IF NOT EXISTS questoes_oab (
    id INTEGER PRIMARY KEY,
    numero_exame TEXT NOT NULL,
    data_exame TEXT,
    tipo_prova INTEGER NOT NULL CHECK (tipo_prova = 1),
    numero_questao INTEGER NOT NULL,
    materia TEXT,
    enunciado TEXT,
    alternativa_a TEXT,
    alternativa_b TEXT,
    alternativa_c TEXT,
    alternativa_d TEXT,
    resposta_preliminar TEXT,
    resposta_definitiva TEXT,
    questao_extraida INTEGER NOT NULL CHECK (questao_extraida IN (0, 1)),
    fonte_prova TEXT NOT NULL,
    fonte_gabarito_preliminar TEXT,
    fonte_gabarito_definitivo TEXT,
    gabarito_definitivo TEXT NOT NULL CHECK (gabarito_definitivo IN ('true', 'false')),
    UNIQUE (numero_exame, tipo_prova, numero_questao)
);
CREATE INDEX IF NOT EXISTS idx_questoes_oab_exame ON questoes_oab(numero_exame, numero_questao);
CREATE INDEX IF NOT EXISTS idx_questoes_oab_materia ON questoes_oab(materia);
CREATE VIEW IF NOT EXISTS questoes_llm AS
SELECT * FROM questoes_oab
WHERE questao_extraida = 1 AND enunciado IS NOT NULL
  AND resposta_definitiva IS NOT NULL
  AND resposta_definitiva <> 'ANULADA'
  AND gabarito_definitivo = 'true';
"""


def exportar(registros: list[dict[str, object]], pasta_saida: Path) -> None:
    pasta_saida.mkdir(parents=True, exist_ok=True)
    caminho_db = pasta_saida / "questoes_oab.sqlite"
    caminho_sql = pasta_saida / "questoes_oab.sql"
    caminho_csv = pasta_saida / "questoes_oab.csv"

    with sqlite3.connect(caminho_db) as conexao:
        conexao.executescript("DROP VIEW IF EXISTS questoes_llm; DROP TABLE IF EXISTS questoes_oab; " + DDL)
        nomes = ", ".join(CAMPOS)
        placeholders = ", ".join("?" for _ in CAMPOS)
        sql_insert = f"INSERT OR REPLACE INTO questoes_oab ({nomes}) VALUES ({placeholders})"
        valores = [tuple(_sqlite(r.get(campo)) for campo in CAMPOS) for r in registros]
        conexao.executemany(sql_insert, valores)
        conexao.commit()
        dump = "\n".join(conexao.iterdump()) + "\n"

    caminho_sql.write_text(dump, encoding="utf-8")
    with caminho_csv.open("w", encoding="utf-8-sig", newline="") as arquivo:
        writer = csv.DictWriter(arquivo, fieldnames=CAMPOS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(registros)


def _sqlite(valor: object) -> object:
    if isinstance(valor, bool):
        return int(valor)
    return valor
