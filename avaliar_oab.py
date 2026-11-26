# /// script
# requires-python = ">=3.10"
# [tool.uv]
# python-preference = "only-managed"  # Python do uv já traz certificados SSL
# ///
"""Aplica as questões da 1ª fase da OAB a um modelo de linguagem e mede acertos e tempo.

Exemplos:
    uv run avaliar_oab.py --modelo gemma4:e2b-it-q4_K_M --limite 20
    uv run avaliar_oab.py --modelo google/gemma-4-26b-a4b-it --onde openrouter
    uv run avaliar_oab.py --modelo gemma4:e2b-it-q4_K_M --prova 46 47
    uv run avaliar_oab.py --modelo gemma4:e2b-it-q4_K_M --gabarito preliminar
"""
import argparse
import csv
import hashlib
import io
import json
import math
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

URL_CSV = "https://raw.githubusercontent.com/james-unb/llm-benchmark-oab/main/dados/questoes_oab.csv"
URL_OLLAMA = "http://localhost:11434/api/chat"
URL_OPENROUTER = "https://openrouter.ai/api/v1/chat/completions"
PASTA_RESULTADOS = Path(__file__).resolve().parent / "resultados"
SEMENTE = 42

PROMPT = (
    "Você está fazendo a prova da 1ª fase do Exame da OAB. Leia a questão e responda "
    "somente com a letra da alternativa correta (A, B, C ou D), sem explicação.\n\n"
    "{enunciado}\n\nA) {alternativa_a}\nB) {alternativa_b}\nC) {alternativa_c}\nD) {alternativa_d}\n\nResposta:"
)
CAMPOS = ["numero_exame", "numero_questao", "materia", "gabarito", "fonte_gabarito", "resposta_modelo",
          "acertou", "segundos", "texto_bruto"]
# Parâmetros que, se mudarem, tornam as respostas antigas incomparáveis com as novas.
CONFIG_QUE_TRAVA = ["modelo", "onde", "temperatura", "max_tokens", "gabarito", "sha256_dados", "sha256_prompt",
                    "sha256_script", "semente"]


def sha256(dados):
    return hashlib.sha256(dados).hexdigest()


def baixar_dados(origem):
    """Lê a base de um caminho local ou de uma URL. Devolve os bytes e o sha256 deles."""
    if re.match(r"https?://", origem):
        with urllib.request.urlopen(origem) as r:
            bruto = r.read()
    else:
        bruto = Path(origem).read_bytes()
    return bruto, sha256(bruto)


def gabarito_de(q, regra):
    """Gabarito da questão e de onde ele veio.

    "definitivo": só vale o gabarito definitivo publicado pela banca (questões sem definitivo ficam de fora).
    "preliminar": usa o definitivo quando existe e, quando não existe, o preliminar. A base não registra se a
    banca manteve o preliminar, então essas questões ficam marcadas com fonte_gabarito = "preliminar".
    """
    if q["resposta_definitiva"]:
        return q["resposta_definitiva"], "definitivo"
    if regra == "preliminar" and q["resposta_preliminar"]:
        return q["resposta_preliminar"], "preliminar"
    return "", ""


def carregar_questoes(bruto, regra):
    questoes = []
    for q in csv.DictReader(io.StringIO(bruto.decode("utf-8-sig"))):
        q["gabarito"], q["fonte_gabarito"] = gabarito_de(q, regra)
        # 2010.2 e 2010.3 tinham alternativa E, que o banco não guarda. "ANULADA" também fica de fora.
        if (q["questao_extraida"] == "True" and q["gabarito"] in ("A", "B", "C", "D")
                and q["numero_exame"] not in ("2010.2", "2010.3")):
            questoes.append(q)
    # Mesma ordem embaralhada para todo mundo: as primeiras N questões são a mesma amostra.
    random.Random(SEMENTE).shuffle(questoes)
    return questoes


def perguntar(onde, modelo, texto, temperatura, max_tokens):
    if onde == "ollama":
        url, cabecalho = URL_OLLAMA, {}
        corpo = {"model": modelo, "messages": [{"role": "user", "content": texto}], "stream": False, "think": False,
                 "options": {"temperature": temperatura, "num_predict": max_tokens}}
    else:
        url, cabecalho = URL_OPENROUTER, {"Authorization": "Bearer " + os.environ["OPENROUTER_API_KEY"]}
        corpo = {"model": modelo, "messages": [{"role": "user", "content": texto}],
                 "temperature": temperatura, "max_tokens": max_tokens,
                 "reasoning": {"effort": "none"}}  # raciocínio desligado, como no Ollama (think: False)
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


def nome_saida(a):
    if a.saida:
        return Path(a.saida)
    sufixo = "_prova" + "-".join(a.prova) if a.prova else ""
    sufixo += "" if a.gabarito == "definitivo" else "_gabarito-preliminar"
    return PASTA_RESULTADOS / ("resultados_" + re.sub(r"[^\w.-]", "_", a.modelo) + sufixo + ".csv")


def conferir_retomada(saida, config):
    """Grava a configuração ao lado do CSV; numa retomada, recusa se algo que muda as respostas mudou."""
    arq_config = saida.with_suffix(".json")
    if saida.exists() and not arq_config.exists():
        sys.exit(f"{saida} já existe e não tem a configuração registrada (rodada de versão antiga do script). "
                 "Use --saida com outro nome para começar uma rodada nova.")
    if arq_config.exists():
        antiga = json.loads(arq_config.read_text(encoding="utf-8"))
        diferentes = [k for k in CONFIG_QUE_TRAVA if antiga.get(k) != config[k]]
        if diferentes:
            linhas = "\n".join(f"  {k}: antes {antiga.get(k)!r}, agora {config[k]!r}" for k in diferentes)
            sys.exit(f"A rodada em {saida} foi feita com outra configuração:\n{linhas}\n"
                     "As respostas antigas não valem para esta configuração. Use --saida com outro nome.")
    arq_config.write_text(json.dumps(config, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--modelo", required=True)
    p.add_argument("--onde", choices=["ollama", "openrouter"], default="ollama")
    p.add_argument("--limite", type=int, help="quantas questões rodar (vazio = todas)")
    p.add_argument("--prova", nargs="+", help="só estes exames, pelo numero_exame (ex.: --prova 46 47)")
    p.add_argument("--gabarito", choices=["definitivo", "preliminar"], default="definitivo",
                   help="definitivo: só questões com gabarito definitivo (padrão); "
                        "preliminar: usa o preliminar onde falta o definitivo, marcado no resultado")
    p.add_argument("--temperatura", type=float, default=0.0)
    p.add_argument("--max-tokens", type=int, default=1024)
    p.add_argument("--dados", default=URL_CSV, help="URL ou caminho do questoes_oab.csv (padrão: main do repositório)")
    p.add_argument("--saida", help="caminho do CSV de resultado (padrão: resultados/resultados_<modelo>....csv)")
    a = p.parse_args(argv)
    if a.limite is not None and a.limite < 1:
        p.error("--limite precisa ser 1 ou mais")

    bruto, sha_dados = baixar_dados(a.dados)
    questoes = carregar_questoes(bruto, a.gabarito)
    if a.prova:
        existentes = {q["numero_exame"] for q in questoes}
        faltam = [x for x in a.prova if x not in existentes]
        if faltam:
            p.error(f"exame(s) sem questões válidas nesta base: {', '.join(faltam)}. "
                    f"Disponíveis: {', '.join(sorted(existentes, key=lambda s: (len(s), s)))}")
        questoes = [q for q in questoes if q["numero_exame"] in a.prova]
    total_selecao = len(questoes)
    questoes = questoes[: a.limite]

    saida = nome_saida(a)
    saida.parent.mkdir(parents=True, exist_ok=True)
    config = {"modelo": a.modelo, "onde": a.onde, "temperatura": a.temperatura, "max_tokens": a.max_tokens,
              "gabarito": a.gabarito, "prova": a.prova, "limite": a.limite, "dados": a.dados,
              "sha256_dados": sha_dados, "sha256_prompt": sha256(PROMPT.encode()), "semente": SEMENTE,
              "sha256_script": sha256(Path(__file__).read_bytes())}
    conferir_retomada(saida, config)

    novo = not saida.exists()
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

    resumir(a, saida, questoes, total_selecao)


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
                        "materia": q["materia"], "gabarito": q["gabarito"], "fonte_gabarito": q["fonte_gabarito"],
                        "resposta_modelo": letra, "acertou": int(letra == q["gabarito"]),
                        "segundos": round(segundos, 2), "texto_bruto": bruto})
            f.flush()
            print(f"{i}/{len(questoes)}  gabarito {q['gabarito']}  modelo {letra or '?'}  {segundos:.1f} s")


def resumir(a, saida, questoes, total_selecao):
    """Resume só as questões da seleção atual; avisa quando ela não é a prova inteira."""
    alvo = {(q["numero_exame"], q["numero_questao"]) for q in questoes}
    with open(saida, encoding="utf-8") as f:
        todas = list(csv.DictReader(f))
    r = [x for x in todas if (x["numero_exame"], x["numero_questao"]) in alvo]
    n = len(r)
    if not n:
        print("Nenhuma questão desta seleção respondida ainda.")
        return
    acc = sum(int(x["acertou"]) for x in r) / n
    erro = 1.96 * math.sqrt(acc * (1 - acc) / n)
    print(f"\nModelo: {a.modelo}   gabarito: {a.gabarito}   exames: {' '.join(a.prova) if a.prova else 'todos'}")
    print(f"Questões respondidas: {n} de {len(questoes)} pedidas ({total_selecao} na seleção completa)")
    if n < total_selecao:
        print("Rodada PARCIAL: a acurácia abaixo vale só para as questões respondidas, não para a prova inteira.")
    print(f"Acurácia: {acc:.1%}  (IC 95%: {max(acc - erro, 0):.1%} a {min(acc + erro, 1):.1%})")
    print(f"Sem letra válida: {sum(not x['resposta_modelo'] for x in r)}")
    prelim = sum(x["fonte_gabarito"] == "preliminar" for x in r)
    if prelim:
        print(f"Com gabarito preliminar (sem definitivo publicado): {prelim}")
    print(f"Tempo médio por questão: {sum(float(x['segundos']) for x in r) / n:.1f} s")
    if len(todas) > n:
        print(f"O arquivo tem {len(todas)} respostas; o resumo considera só as {n} desta seleção.")
    print(f"Resultados em: {saida}  (configuração em {saida.with_suffix('.json').name})")


if __name__ == "__main__":
    main()
