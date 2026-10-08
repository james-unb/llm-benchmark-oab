"""Download seletivo de cadernos tipo 1 e gabaritos oficiais da 1ª fase."""

from __future__ import annotations

import re
import time
import unicodedata
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

BASE_URL = "https://examedeordem.oab.org.br/EditaisProvas"
FIRST_PHASE_MARKERS = ("1ª fase", "1a fase", "primeira fase", "prova objetiva")
SECOND_PHASE_MARKERS = ("2ª fase", "2a fase", "segunda fase", "prático-profissional", "pratico-profissional")


def _normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto.casefold())
    return "".join(c for c in texto if not unicodedata.combining(c))


def _slug(texto: str) -> str:
    texto = texto.casefold()
    texto = re.sub(r"[^a-z0-9]+", "_", texto).strip("_")
    return texto


def _chave_pasta(texto: str) -> str:
    chave = re.sub(r"[^a-z0-9]+", "", _normalizar(texto))
    return re.sub(r"(?<=\d)o(?=exame)", "", chave)


def _chave_data(texto: str) -> str | None:
    m = re.search(r"(?<!\d)(\d{1,2})[._/ -]?(\d{2})[._/ -]?(20\d{2})(?!\d)", texto)
    if not m:
        return None
    return f"{int(m.group(1)):02d}{m.group(2)}{m.group(3)}"


def _tipo_documento(secao: str, titulo: str) -> str | None:
    secao_n = _normalizar(secao)
    titulo_n = _normalizar(titulo)
    # Mantém uma única aplicação por número de exame para não misturar
    # caderno/gabarito da reaplicação local ao exame nacional.
    if any(m in titulo_n for m in ("reaplicacao", "examinandos de salvador", "examinandos de boa vista")):
        return None
    contexto = f"{secao_n} {titulo_n}"
    if any(m in contexto for m in SECOND_PHASE_MARKERS):
        return None
    primeira_fase = any(m in secao_n or m in titulo_n for m in FIRST_PHASE_MARKERS)
    if "anulacao" in titulo_n and "prova objetiva" in titulo_n:
        return "comunicado_anulacao"
    if not primeira_fase:
        # Algumas páginas antigas agrupam somente os arquivos da objetiva.
        if "caderno de prova" not in titulo_n and "gabarito" not in titulo_n:
            return None
        if "prova objetiva" not in contexto and "caderno de prova" not in titulo_n and "gabarito" not in titulo_n:
            return None

    if "gabarito" in titulo_n:
        caderno = re.search(r"caderno\s+de\s+prova\s+(?:n[ºo.]?\s*)?0?(\d)\b", titulo_n)
        if caderno and int(caderno.group(1)) != 1:
            return None
        if "preliminar" in titulo_n:
            return "gabarito_preliminar"
        if any(p in titulo_n for p in ("definitiv", "final", "apos recurso", "apos recursos")):
            return "gabarito_definitivo"
        return "gabarito_oficial"

    # A página atual chama o primeiro caderno de Tipo 1; páginas de 2010
    # chamam-no Caderno de Prova 01. Não aceitar Tipo 2/3/4 por engano.
    if re.search(r"\btipo\s*[-º°]?\s*1\b", titulo_n):
        return "prova_tipo_1"
    if re.search(r"\bcaderno\s+de\s+prova\s+(?:n[ºo.]?\s*)?0?1\b", titulo_n):
        return "prova_tipo_1"
    return None


def _ordinal_exame(nome: str) -> int | None:
    nome_n = _normalizar(nome)
    legado = re.search(r"\b2010[ ._-]*([23])\b", nome_n)
    if legado:
        return int(legado.group(1)) - 2
    prefixo = re.match(r"\s*(\d{1,2})(?:[º°o])?(?=\s|_|-|$)", nome_n)
    if prefixo:
        return int(prefixo.group(1))
    m = re.search(r"\b(\d{1,2})\s*[º°o]?\s*(?:exame|eou)", nome_n)
    if m:
        return int(m.group(1))
    m = re.search(r"\b([ivxlcdm]{1,8})\s+exame\b", nome_n)
    if not m:
        return None
    simbolos = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
    romano = m.group(1)
    total = 0
    for i, char in enumerate(romano):
        valor = simbolos[char]
        total += -valor if i + 1 < len(romano) and valor < simbolos[romano[i + 1]] else valor
    return total


def baixar_documentos(pasta_saida: Path, exame_inicial: str = "XVI") -> int:
    """Baixa somente prova tipo 1 e gabaritos da primeira fase.

    Os links são lidos por tabela e cabeçalho da página. Isso evita capturar
    as peças e padrões de resposta da segunda fase por conterem a palavra prova.
    """
    pasta_saida.mkdir(parents=True, exist_ok=True)
    quantidade = 0
    limite = _ordinal_exame(f"{exame_inicial} EXAME")
    if limite is None:
        raise ValueError(f"Número de exame inicial inválido: {exame_inicial}")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(accept_downloads=False)
        page.goto(f"{BASE_URL}?NumeroExame=0", wait_until="domcontentloaded", timeout=60000)
        page.locator("select#cmb-edital").wait_for(timeout=30000)
        exames = page.locator("select#cmb-edital option").evaluate_all(
            "els => els.map(e => ({id:e.value, nome:e.textContent.trim()}))"
        )
        exames = [e for e in exames if e["id"] != "0" and e["nome"]]

        for exame in reversed(exames):
            ordinal = _ordinal_exame(exame["nome"])
            if ordinal is None or ordinal < limite:
                continue
            url = f"{BASE_URL}?NumeroExame={exame['id']}"
            print(f"\n{exame['nome']}", flush=True)
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            try:
                page.locator("table.tabela-arquivo").first.wait_for(timeout=20000)
            except PlaywrightTimeoutError:
                print("  Página sem tabela de documentos; será revisada na próxima execução.")
                continue
            tabelas = page.locator("table.tabela-arquivo").evaluate_all(
                """tables => tables.map(t => ({
                    secao: (t.querySelector('thead')?.innerText || '').trim(),
                    documentos: [...t.querySelectorAll('tbody a[href]')].map(a => ({
                        titulo: a.innerText.trim(), url: a.href
                    }))
                }))"""
            )
            pasta_exame = next(
                (p for p in pasta_saida.iterdir() if p.is_dir() and _ordinal_exame(p.name) == ordinal),
                None,
            )
            documentos_tipo1 = [
                documento
                for tabela in tabelas
                for documento in tabela["documentos"]
                if _tipo_documento(tabela["secao"], documento["titulo"]) == "prova_tipo_1"
            ]
            data_prova = next(
                (_chave_data(documento["titulo"]) for documento in documentos_tipo1 if _chave_data(documento["titulo"])),
                None,
            )
            if pasta_exame is None:
                if data_prova:
                    dia, mes, ano = int(data_prova[:2]), int(data_prova[2:4]), data_prova[4:]
                    trimestre = (mes - 1) // 3 + 1
                    identificador = f"2010.{ordinal + 2}" if ordinal in (0, 1) else str(ordinal)
                    pasta_exame = pasta_saida / f"{identificador}_{ano}_T{trimestre}"
                else:
                    pasta_exame = pasta_saida / _slug(exame["nome"])
            for tabela in tabelas:
                for documento in tabela["documentos"]:
                    tipo = _tipo_documento(tabela["secao"], documento["titulo"])
                    if tipo is None or not documento["url"].split("?", 1)[0].lower().endswith(".pdf"):
                        continue
                    data_documento = _chave_data(documento["titulo"])
                    if data_documento and pasta_exame.exists():
                        duplicado = False
                        for existente in pasta_exame.glob("*.pdf"):
                            tipo_existente = _tipo_documento("Provas e Gabaritos (1a fase - Prova Objetiva)", existente.name)
                            if tipo_existente == tipo and _chave_data(existente.name) == data_documento:
                                duplicado = True
                                break
                        if duplicado:
                            print(f"  Já existe: {documento['titulo']}")
                            continue
                    # As chaves preliminar e definitiva são necessárias para
                    # marcar alterações; a chave única antiga também é guardada.
                    titulo = re.sub(r"\s+", " ", documento["titulo"]).strip()
                    nome = re.sub(r'[<>:"/\\|?*]', "_", titulo)
                    destino = pasta_exame / f"{pasta_exame.name} - {tipo} - {nome}.pdf"
                    if destino.exists() and destino.stat().st_size > 0:
                        print(f"  Já existe: {destino.name}")
                        continue
                    try:
                        resposta = page.request.get(documento["url"].replace("http://s.oab.org.br", "https://s.oab.org.br"), timeout=60000)
                        if not resposta.ok or not resposta.body().startswith(b"%PDF"):
                            print(f"  FALHOU ({resposta.status}): {documento['url']}")
                            continue
                        destino.parent.mkdir(parents=True, exist_ok=True)
                        destino.write_bytes(resposta.body())
                        quantidade += 1
                        print(f"  Baixado: {destino.name}", flush=True)
                        time.sleep(0.15)
                    except Exception as exc:  # a coleta segue para os outros documentos
                        print(f"  FALHOU: {documento['url']} ({exc})")
        browser.close()
    return quantidade
