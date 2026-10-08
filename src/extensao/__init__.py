"""Pipeline de coleta e estruturação das provas objetivas da OAB."""

from __future__ import annotations

import argparse
from pathlib import Path

from extensao.database import exportar
from extensao.downloader import baixar_documentos
from extensao.extractor import extrair_provas


def main() -> None:
    parser = argparse.ArgumentParser(description="Coleta e estrutura questões da 1ª fase da OAB.")
    parser.add_argument("acao", choices=("baixar", "extrair", "tudo"), nargs="?", default="tudo")
    parser.add_argument("--exame-inicial", default="XVI", help="Primeiro exame a coletar (padrão: XVI, 2014.3).")
    parser.add_argument("--dados", type=Path, default=Path("provas_oab"))
    parser.add_argument("--saida", type=Path, default=Path("dados"))
    args = parser.parse_args()

    if args.acao in {"baixar", "tudo"}:
        baixar_documentos(args.dados, exame_inicial=args.exame_inicial)
    if args.acao in {"extrair", "tudo"}:
        questoes = extrair_provas(args.dados)
        exportar(questoes, args.saida)
        print(f"Questões estruturadas: {len(questoes)}")


__all__ = ["main"]
