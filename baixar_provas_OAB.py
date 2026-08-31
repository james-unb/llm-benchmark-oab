"""
Baixa somente provas (cadernos) e gabaritos dos Exames de Ordem
Unificados da OAB, desde 2010.

Os arquivos são obtidos diretamente do site oficial da OAB e
organizados por exame.

Fonte:
https://examedeordem.oab.org.br/EditaisProvas
"""

import re
import time
from pathlib import Path

from playwright.sync_api import sync_playwright


BASE_URL = "https://examedeordem.oab.org.br/EditaisProvas?NumeroExame=0"

OUTPUT_DIR = Path("provas_oab")


def limpar_nome(texto: str) -> str:
    """
    Cria um nome de arquivo seguro e legível.
    """

    texto = texto.strip()

    # Remove caracteres inválidos para nomes de arquivos no Windows
    texto = re.sub(r'[<>:"/\\|?*]', "", texto)

    # Remove espaços repetidos
    texto = re.sub(r"\s+", " ", texto)

    # Limita o tamanho
    return texto[:150].strip()


def slugify(texto: str) -> str:
    """
    Cria um nome seguro para a pasta do exame.
    """

    texto = texto.strip().lower()

    texto = re.sub(r"[^\w\s-]", "", texto, flags=re.UNICODE)

    texto = re.sub(r"[\s-]+", "_", texto)

    return texto


def classificar_documento(texto: str) -> str | None:
    """
    Retorna:

    'PROVA'     -> se for prova ou caderno de prova
    'GABARITO'  -> se for gabarito
    None        -> qualquer outro documento
    """

    texto = texto.lower()

    # Documentos que NÃO queremos baixar
    palavras_excluidas = [
        "edital",
        "retificação",
        "retificacao",
        "comunicado",
        "resultado",
        "convocação",
        "convocacao",
        "cronograma",
        "inscrição",
        "inscricao",
        "recurso",
        "espelho",
        "padrão de resposta",
        "padrao de resposta",
    ]

    if any(palavra in texto for palavra in palavras_excluidas):
        return None

    # Gabaritos
    if "gabarito" in texto:
        return "GABARITO"

    # Provas e cadernos
    palavras_prova = [
        "caderno de prova",
        "caderno",
        "prova objetiva",
        "prova prático",
        "prova pratico",
        "prova tipo",
        "prova",
    ]

    if any(palavra in texto for palavra in palavras_prova):
        return "PROVA"

    return None


def baixar_arquivo(page, url: str, destino: Path) -> None:
    """
    Baixa o arquivo caso ele ainda não exista.
    """

    destino.parent.mkdir(parents=True, exist_ok=True)

    if destino.exists():

        print(f"  Já existe: {destino.name}")

        return

    resposta = page.request.get(url)

    if resposta.ok:

        destino.write_bytes(resposta.body())

        print(f"  Baixado: {destino.name}")

    else:

        print(
            f"  FALHOU ({resposta.status}): {url}"
        )


def main() -> None:

    with sync_playwright() as p:

        browser = p.chromium.launch(headless=True)

        page = browser.new_page()

        page.goto(
            BASE_URL,
            wait_until="networkidle"
        )

        page.wait_for_selector("select")

        opcoes = page.eval_on_selector_all(

            "select option",

            """
            els => els.map(e => ({
                value: e.value,
                text: e.textContent.trim()
            }))
            """

        )

        for opcao in opcoes:

            valor = opcao["value"]
            nome_exame = opcao["text"]

            if not valor or valor == "0" or not nome_exame:
                continue

            print("\n" + "=" * 60)
            print(nome_exame)
            print("=" * 60)

            page.select_option("select", valor)

            # Espera o AJAX atualizar a página
            page.wait_for_timeout(2000)

            documentos = page.eval_on_selector_all(

                "a[href$='.pdf']",

                """
                els => els.map((e, index) => {

                    let texto = e.textContent.trim();

                    // Se o link não tiver texto suficiente,
                    // pega o texto do elemento pai
                    if (!texto || texto.length < 3) {
                        texto = e.parentElement
                            ? e.parentElement.textContent.trim()
                            : "";
                    }

                    // Procura um container próximo com mais contexto
                    const container =
                        e.closest("tr, li, .row, .card, div");

                    const contexto = container
                        ? container.textContent.trim()
                        : texto;

                    return {
                        href: e.href,
                        texto: texto,
                        contexto: contexto,
                        index: index
                    };
                })
                """

            )

            documentos_filtrados = []

            for documento in documentos:

                # Junta o texto do link com o contexto ao redor
                texto_completo = (
                    documento["texto"]
                    + " "
                    + documento["contexto"]
                )

                tipo = classificar_documento(texto_completo)

                if tipo is not None:

                    documentos_filtrados.append({
                        **documento,
                        "tipo": tipo,
                        "texto_completo": texto_completo,
                    })

            if not documentos_filtrados:

                print(
                    "  Nenhuma prova ou gabarito encontrado."
                )

                continue

            pasta_exame = (
                OUTPUT_DIR
                / slugify(nome_exame)
            )

            contador_prova = 1
            contador_gabarito = 1

            for documento in documentos_filtrados:

                tipo = documento["tipo"]

                # Usa o texto do documento para criar um nome legível
                descricao = limpar_nome(
                    documento["texto"]
                )

                # Se o texto do link não for útil,
                # usamos o tipo e um contador
                if len(descricao) < 3:

                    if tipo == "PROVA":

                        descricao = (
                            f"Prova_{contador_prova}"
                        )

                        contador_prova += 1

                    else:

                        descricao = (
                            f"Gabarito_{contador_gabarito}"
                        )

                        contador_gabarito += 1

                # Nome final do arquivo
                nome_arquivo = (
                    f"{tipo} - "
                    f"{descricao}.pdf"
                )

                destino = (
                    pasta_exame
                    / nome_arquivo
                )

                # Evita sobrescrever arquivos com o mesmo nome
                numero = 2
                destino_original = destino

                while destino.exists():

                    destino = destino_original.with_name(

                        f"{destino_original.stem} "
                        f"({numero})"
                        f"{destino_original.suffix}"

                    )

                    numero += 1

                print(
                    f"\n  [{tipo}] "
                    f"{descricao}"
                )

                baixar_arquivo(
                    page,
                    documento["href"],
                    destino
                )

            time.sleep(0.5)

        browser.close()

    print("\n" + "=" * 60)
    print("CONCLUÍDO!")
    print(f"Arquivos salvos em:")
    print(OUTPUT_DIR.resolve())
    print("=" * 60)


if __name__ == "__main__":
    main()