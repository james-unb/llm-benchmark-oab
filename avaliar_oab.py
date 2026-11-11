# /// script
# requires-python = ">=3.10"
# [tool.uv]
# python-preference = "only-managed"  # Python do uv já traz certificados SSL
# ///
"""Aplica as questões da 1ª fase da OAB a um modelo de linguagem e mede acertos e tempo.

Exemplos:
    uv run avaliar_oab.py --modelo gemma4:e2b-it-q4_K_M --limite 20
    uv run avaliar_oab.py --modelo google/gemma-4-26b-a4b-it:free --onde openrouter
    uv run avaliar_oab.py --modelo gemma4:e2b-it-q4_K_M --prova 47
"""
import argparse
import csv
import io
import json
import math
import os
import random
import re
import time
import urllib.error
import urllib.request

URL_CSV = "https://raw.githubusercontent.com/james-unb/llm-benchmark-oab/main/dados/questoes_oab.csv"
URL_OLLAMA = "http://localhost:11434/api/chat"
URL_OPENROUTER = "https://openrouter.ai/api/v1/chat/completions"

PROMPT = (
    "Você está fazendo a prova da 1ª fase do Exame da OAB. Leia a questão e responda "
    "somente com a letra da alternativa correta (A, B, C ou D), sem explicação.\n\n"
    "{enunciado}\n\nA) {alternativa_a}\nB) {alternativa_b}\nC) {alternativa_c}\nD) {alternativa_d}\n\nResposta:"
)
CAMPOS = ["numero_exame", "numero_questao", "materia", "gabarito", "resposta_modelo",
          "acertou", "segundos", "texto_bruto"]


def carregar_questoes():
    with urllib.request.urlopen(URL_CSV) as r:
        linhas = csv.DictReader(io.StringIO(r.read().decode("utf-8-sig")))
        questoes = []
        for q in linhas:
            # Gabarito preliminar vale quando a banca manteve o preliminar e não publicou definitivo.
            q["gabarito"] = q["resposta_definitiva"] or q["resposta_preliminar"]
            # 2010.2 e 2010.3 tinham alternativa E, que o banco não guarda.
            if (q["questao_extraida"] == "True" and q["gabarito"] in ("A", "B", "C", "D")
                    and q["numero_exame"] not in ("2010.2", "2010.3")):
                questoes.append(q)
    # Mesma ordem embaralhada para todo mundo: as primeiras N questões são a mesma amostra.
    random.Random(42).shuffle(questoes)
    return questoes


def perguntar(onde, modelo, texto, temperatura, max_tokens):
    if onde == "ollama":
        url, cabecalho = URL_OLLAMA, {}
        corpo = {"model": modelo, "messages": [{"role": "user", "content": texto}], "stream": False, "think": False,
                 "options": {"temperature": temperatura, "num_predict": max_tokens}}
    else:
        url, cabecalho = URL_OPENROUTER, {"Authorization": "Bearer " + os.environ["OPENROUTER_API_KEY"]}
        corpo = {"model": modelo, "messages": [{"role": "user", "content": texto}],
                 "temperature": temperatura, "max_tokens": max_tokens}
    pedido = urllib.request.Request(url, json.dumps(corpo).encode(),
                                    {"Content-Type": "application/json", **cabecalho})
    with urllib.request.urlopen(pedido, timeout=600) as r:
        resposta = json.load(r)
    if onde == "ollama":
        return resposta["message"]["content"]
    return resposta["choices"][0]["message"]["content"] or ""


def ler_letra(texto):
    texto = texto.strip()
    m = re.fullmatch(r"\(?([ABCD])\)?[.):]?", texto)
    if not m:
        m = re.search(r"(?i:letra|alternativa|resposta)(?:\s*(?i:correta|certa|é|e|a|:))*\s*\(?([ABCD])\b", texto)
    if not m:
        m = re.match(r"\(?([ABCD])\)[\s.]", texto)
    return m.group(1) if m else ""


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--modelo", required=True)
    p.add_argument("--onde", choices=["ollama", "openrouter"], default="ollama")
    p.add_argument("--limite", type=int, help="quantas questões rodar (vazio = todas)")
    p.add_argument("--prova", nargs="+", help="só estes exames, pelo numero_exame (ex.: --prova 46 47)")
    p.add_argument("--temperatura", type=float, default=0.0)
    p.add_argument("--max-tokens", type=int, default=1024)
    a = p.parse_args()

    questoes = carregar_questoes()
    if a.prova:
        questoes = [q for q in questoes if q["numero_exame"] in a.prova]
    questoes = questoes[: a.limite]
    # Arquivo separado por prova, para o resumo não misturar com a rodada completa.
    sufixo = "_prova" + "-".join(a.prova) if a.prova else ""
    saida = "resultados_" + re.sub(r"[^\w.-]", "_", a.modelo) + sufixo + ".csv"
    novo = not os.path.exists(saida)
    feitas = set()
    if not novo:
        with open(saida, encoding="utf-8") as f:
            feitas = {(r["numero_exame"], r["numero_questao"]) for r in csv.DictReader(f)}

    try:
        responder(a, questoes, saida, feitas, novo)
    except urllib.error.HTTPError as e:
        if e.code != 429:
            raise
        print("\nLimite diário do OpenRouter atingido. Rode o mesmo comando amanhã para continuar.")

    resumir(a, saida)


def responder(a, questoes, saida, feitas, novo):
    with open(saida, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, CAMPOS)
        if novo:
            w.writeheader()
        for i, q in enumerate(questoes, 1):
            if (q["numero_exame"], q["numero_questao"]) in feitas:
                continue
            for tentativa in range(3):
                try:
                    inicio = time.perf_counter()
                    bruto = perguntar(a.onde, a.modelo, PROMPT.format(**q), a.temperatura, a.max_tokens)
                    segundos = time.perf_counter() - inicio
                    break
                except urllib.error.HTTPError as e:
                    # 429 = limite do OpenRouter: espera um minuto; três seguidos = limite do dia.
                    if e.code != 429 or tentativa == 2:
                        raise
                    print("Limite por minuto atingido, esperando 60 s...")
                    time.sleep(60)
            if a.onde == "openrouter":
                time.sleep(3)  # gratuito aceita no máximo 20 perguntas por minuto
            letra = ler_letra(bruto)
            w.writerow({"numero_exame": q["numero_exame"], "numero_questao": q["numero_questao"],
                        "materia": q["materia"], "gabarito": q["gabarito"], "resposta_modelo": letra,
                        "acertou": int(letra == q["gabarito"]), "segundos": round(segundos, 2),
                        "texto_bruto": bruto[:500]})
            f.flush()
            print(f"{i}/{len(questoes)}  gabarito {q['gabarito']}  modelo {letra or '?'}  {segundos:.1f} s")


def resumir(a, saida):
    with open(saida, encoding="utf-8") as f:
        r = list(csv.DictReader(f))
    n = len(r)
    if not n:
        print("Nenhuma questão respondida ainda.")
        return
    acc = sum(int(x["acertou"]) for x in r) / n
    erro = 1.96 * math.sqrt(acc * (1 - acc) / n)
    print(f"\nModelo: {a.modelo}   questões: {n}")
    print(f"Acurácia: {acc:.1%}  (IC 95%: {max(acc - erro, 0):.1%} a {min(acc + erro, 1):.1%})")
    print(f"Sem letra válida: {sum(not x['resposta_modelo'] for x in r)}")
    print(f"Tempo médio por questão: {sum(float(x['segundos']) for x in r) / n:.1f} s")
    print(f"Resultados em: {saida}")


if __name__ == "__main__":
    main()
