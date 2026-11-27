"""Valida resultados do avaliador OAB contra a base e o script locais.

Exemplo: python validar_resultados.py resultados/resultados_modelo_prova47.csv
Não baixa URLs nem executa modelos. Use a revisão correspondente à rodada.
"""
import argparse
import csv
import hashlib
import html
import json
import math
import re
import sys
from pathlib import Path

import avaliar_oab as A

RAIZ = Path(__file__).resolve().parent
LETRAS = set("ABCD")


def sha256(bruto):
    return hashlib.sha256(bruto).hexdigest()


def ler_versao(caminho, esperado):
    bruto = caminho.read_bytes()

    if sha256(bruto) == esperado:
        return bruto

    # Compatibilidade entre Windows e Linux.
    normalizado = bruto.replace(b"\r\n", b"\n")

    for candidato in (
        normalizado,
        normalizado.replace(b"\n", b"\r\n")
    ):
        if sha256(candidato) == esperado:
            return candidato

    raise ValueError(
        f"Hash incompatível: {caminho}. "
        "Use a revisão da rodada."
    )


def inteiro_positivo(v):
    return type(v) is int and v > 0


def numero_finito(v):
    return type(v) in (int, float) and math.isfinite(v)


def ler_config(caminho):
    c = json.loads(caminho.read_text(encoding="utf-8"))
    if not isinstance(c, dict):
        raise ValueError("O JSON deve ser um objeto.")
    campos = set(A.CONFIG_QUE_TRAVA) | {"prova", "limite", "dados"}
    if campos - c.keys():
        raise ValueError(f"Campos ausentes no JSON: {sorted(campos - c.keys())}")
    for k in ("sha256_dados", "sha256_script", "sha256_prompt"):
        if not isinstance(c[k], str) or not re.fullmatch(r"[0-9a-f]{64}", c[k]):
            raise ValueError(f"{k} inválido.")
    for k in ("modelo", "dados"):
        if not isinstance(c[k], str) or not c[k].strip():
            raise ValueError(f"{k} inválido.")
    if c["onde"] not in ("ollama", "openrouter"):
        raise ValueError("Serviço inválido.")
    if c["gabarito"] not in ("definitivo", "preliminar"):
        raise ValueError("Regra de gabarito inválida.")
    if not numero_finito(c["temperatura"]) or c["temperatura"] < 0:
        raise ValueError("Temperatura inválida.")
    if not inteiro_positivo(c["max_tokens"]):
        raise ValueError("max_tokens inválido.")
    if type(c["semente"]) is not int or c["semente"] != A.SEMENTE:
        raise ValueError("Semente incompatível com o avaliador.")
    if c["limite"] is not None and not inteiro_positivo(c["limite"]):
        raise ValueError("Limite inválido.")
    provas = c["prova"]
    if provas is not None and (not isinstance(provas, list) or not provas
            or any(not isinstance(x, str) or not x for x in provas)
            or len(provas) != len(set(provas))):
        raise ValueError("Seleção de provas inválida.")
    return c


def chave(q):
    return q["numero_exame"], q["numero_questao"]


def validar(caminho, base, permitir_parcial=False):
    caminho, base = Path(caminho), Path(base)
    c = ler_config(caminho.with_suffix(".json"))
    ler_versao(Path(A.__file__), c["sha256_script"])
    if sha256(A.PROMPT.encode()) != c["sha256_prompt"]:
        raise ValueError("Hash do prompt incompatível.")
    bruto = ler_versao(base, c["sha256_dados"])
    selecionadas = A.carregar_questoes(bruto, c["gabarito"])
    if c["prova"] is not None:
        disponiveis = {q["numero_exame"] for q in selecionadas}
        faltam = set(c["prova"]) - disponiveis
        if faltam:
            raise ValueError(f"Provas sem questões elegíveis: {sorted(faltam)}")
        selecionadas = [q for q in selecionadas if q["numero_exame"] in c["prova"]]
    selecionadas = selecionadas[:c["limite"]]
    esperadas = {chave(q): q for q in selecionadas}
    if not esperadas or len(esperadas) != len(selecionadas):
        raise ValueError("Seleção vazia ou com questões duplicadas na base.")

    vistos = set()
    erros = []
    acertos = invalidas = 0
    tempos = []
    with caminho.open(encoding="utf-8-sig", newline="") as f:
        leitor = csv.DictReader(f, strict=True)
        if leitor.fieldnames != A.CAMPOS:
            raise ValueError("Cabeçalho CSV incompatível com o avaliador.")
        for r in leitor:
            prefixo = f"Linha {leitor.line_num}"
            if None in r or any(v is None for v in r.values()):
                erros.append(f"{prefixo}: quantidade de campos incorreta.")
                continue
            k = chave(r)
            prefixo = f"Exame {k[0]}, questão {k[1]}"
            if k in vistos:
                erros.append(f"{prefixo}: duplicata.")
                continue
            vistos.add(k)
            q = esperadas.get(k)
            if q is None:
                erros.append(f"{prefixo}: questão não elegível ou fora da seleção.")
                continue
            for campo in ("materia", "gabarito", "fonte_gabarito"):
                if r[campo] != q[campo]:
                    erros.append(f"{prefixo}: {campo} diverge da base.")
            letra = r["resposta_modelo"]
            if letra not in LETRAS | {""}:
                erros.append(f"{prefixo}: letra inválida no campo resposta_modelo.")
            if letra != A.ler_letra(r["texto_bruto"]):
                erros.append(f"{prefixo}: resposta_modelo diverge de texto_bruto.")
            acerto = int(letra == q["gabarito"])
            if r["acertou"] != str(acerto):
                erros.append(f"{prefixo}: coluna acertou inconsistente.")
            acertos += acerto
            invalidas += int(not letra)
            try:
                tempo = float(r["segundos"])
                if not math.isfinite(tempo) or tempo < 0:
                    raise ValueError
                tempos.append(tempo)
            except ValueError:
                erros.append(f"{prefixo}: tempo inválido.")
    ausentes = esperadas.keys() - vistos
    if ausentes and not permitir_parcial:
        erros.append(f"{len(ausentes)} questões esperadas ausentes (ex.: {sorted(ausentes)[:5]}).")
    if not (vistos & esperadas.keys()):
        erros.append("Nenhuma questão elegível respondida.")
    if erros:
        raise ValueError("\n".join(erros))
    n = len(vistos)
    return {"respondidas": n, "esperadas": len(esperadas), "acertos": acertos,
            "acuracia": acertos / n, "sem_letra": invalidas,
            "tempo_medio": sum(tempos) / n, "parcial": bool(ausentes),
            "modelo": c["modelo"], "provas": c["prova"], "gabarito": c["gabarito"]}


def texto_md(valor):
    """Evita que campos externos alterem a estrutura do relatório Markdown."""
    texto = " ".join(str(valor).split())
    texto = texto[:300] + ("…" if len(texto) > 300 else "")
    return "".join(
        f"&#{ord(c)};" if c in "|`*_[]!~#\\" else html.escape(c, quote=True)
        for c in texto
    )


def gerar_resumo(registros):
    """Retorna um relatório por arquivo, sem agregar rodadas distintas."""
    linhas = ["## Validação dos resultados", ""]
    if not registros:
        return "\n".join(linhas + ["Nenhum resultado novo ou modificado.", ""])
    falhas = sum("erro" in item for item in registros)
    parciais = sum("erro" not in item and item["resultado"]["parcial"]
                   for item in registros)
    completos = len(registros) - falhas - parciais
    linhas.extend([
        f"Arquivos: {len(registros)} | OK: {completos} | "
        f"Parciais: {parciais} | Erros: {falhas}", "",
        "| Arquivo | Modelo | Provas | Gabarito | Questões | Acertos | Acurácia | Sem letra | Tempo médio (s) | Status |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---|",
    ])
    for item in registros:
        nome = texto_md(item["arquivo"])
        if "erro" in item:
            linhas.append(f"| {nome} | — | — | — | — | — | — | — | — | ERRO |")
            continue
        r = item["resultado"]
        provas = ", ".join(r["provas"]) if r["provas"] is not None else "Todas"
        status = "PARCIAL" if r["parcial"] else "OK"
        linhas.append(
            f"| {nome} | {texto_md(r['modelo'])} | {texto_md(provas)} | "
            f"{texto_md(r['gabarito'])} | {r['respondidas']}/{r['esperadas']} | "
            f"{r['acertos']} | {r['acuracia']:.1%} | {r['sem_letra']} | "
            f"{r['tempo_medio']:.2f} | {status} |"
        )
    if falhas:
        linhas.extend(["", "### Inconsistências", ""])
        for item in registros:
            if "erro" not in item:
                continue
            erros = item["erro"].splitlines()
            linhas.append(f"- **{texto_md(item['arquivo'])}**")
            for erro in erros[:5]:
                linhas.append(f"  - {texto_md(erro)}")
            if len(erros) > 5:
                linhas.append(f"  - Mais {len(erros) - 5} linhas de erro nos logs.")
    return "\n".join(linhas) + "\n"


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("csv", nargs="+", type=Path)
    p.add_argument("--base", type=Path, default=RAIZ / "dados/questoes_oab.csv")
    p.add_argument("--permitir-parcial", action="store_true",
                   help="Aceita uma rodada interrompida, identificada como parcial no resumo.")
    p.add_argument("--resumo", type=Path, help="Caminho do relatório Markdown.")
    a = p.parse_intermixed_args(argv)
    falhou = False
    registros = []
    for caminho in a.csv:
        try:
            r = validar(caminho, a.base, a.permitir_parcial)
            registros.append({"arquivo": str(caminho), "resultado": r})
            status = "PARCIAL" if r["parcial"] else "OK"
            print(f"{status}: {caminho} | {r['respondidas']}/{r['esperadas']} questões | "
                  f"{r['acertos']} acertos | {r['acuracia']:.1%} | "
                  f"{r['sem_letra']} sem letra | {r['tempo_medio']:.2f} s/questão")
        except (OSError, ValueError, KeyError, TypeError, csv.Error) as e:
            falhou = True
            registros.append({"arquivo": str(caminho), "erro": str(e)})
            print(f"ERRO: {caminho}\n{e}", file=sys.stderr)
    if a.resumo is not None:
        try:
            with a.resumo.open("a", encoding="utf-8", newline="\n") as f:
                f.write(gerar_resumo(registros))
        except OSError as e:
            falhou = True
            print(f"ERRO ao gravar resumo: {e}", file=sys.stderr)
    return int(falhou)


if __name__ == "__main__":
    sys.exit(main())
