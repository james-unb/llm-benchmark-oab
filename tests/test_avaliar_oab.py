"""Testes do fluxo do avaliar_oab.py, sem chamar modelo nenhum (a pergunta é simulada).

    python3 -m unittest discover -s tests
"""
import contextlib
import csv
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import avaliar_oab as A  # noqa: E402

CAB = ["numero_exame", "numero_questao", "materia", "enunciado", "alternativa_a", "alternativa_b", "alternativa_c",
       "alternativa_d", "resposta_preliminar", "resposta_definitiva", "questao_extraida"]


def base(tmp):
    """Base pequena: exame 46 com definitivo, exame 47 só com preliminar, uma anulada e uma do 2010.2."""
    linhas = [
        ["46", "1", "Civil", "Q1", "a", "b", "c", "d", "A", "A", "True"],
        ["46", "2", "Penal", "Q2", "a", "b", "c", "d", "B", "B", "True"],
        ["46", "3", "Penal", "Q3", "a", "b", "c", "d", "C", "ANULADA", "True"],
        ["47", "1", "Civil", "Q4", "a", "b", "c", "d", "D", "", "True"],
        ["47", "2", "Civil", "Q5", "a", "b", "c", "d", "A", "", "True"],
        ["2010.2", "1", "Civil", "Q6", "a", "b", "c", "d", "A", "A", "True"],
    ]
    p = Path(tmp) / "questoes.csv"
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(CAB)
        w.writerows(linhas)
    return p


def ler_csv(caminho):
    with open(caminho, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def rodar(args, resposta="A"):
    """Roda o main com a pergunta simulada; devolve o texto impresso."""
    saida = io.StringIO()
    with mock.patch.object(A, "perguntar", return_value=resposta), contextlib.redirect_stdout(saida):
        A.main(args)
    return saida.getvalue()


class Gabarito(unittest.TestCase):
    def test_definitivo_deixa_de_fora_quem_so_tem_preliminar(self):
        with tempfile.TemporaryDirectory() as t:
            qs = A.carregar_questoes(base(t).read_bytes(), "definitivo")
        self.assertEqual(sorted((q["numero_exame"], q["numero_questao"]) for q in qs), [("46", "1"), ("46", "2")])
        self.assertTrue(all(q["fonte_gabarito"] == "definitivo" for q in qs))

    def test_preliminar_marca_a_fonte(self):
        with tempfile.TemporaryDirectory() as t:
            qs = A.carregar_questoes(base(t).read_bytes(), "preliminar")
        fontes = {(q["numero_exame"], q["numero_questao"]): q["fonte_gabarito"] for q in qs}
        self.assertEqual(fontes, {("46", "1"): "definitivo", ("46", "2"): "definitivo",
                                  ("47", "1"): "preliminar", ("47", "2"): "preliminar"})

    def test_anulada_e_2010_ficam_de_fora(self):
        with tempfile.TemporaryDirectory() as t:
            qs = A.carregar_questoes(base(t).read_bytes(), "preliminar")
        self.assertNotIn(("46", "3"), {(q["numero_exame"], q["numero_questao"]) for q in qs})
        self.assertNotIn("2010.2", {q["numero_exame"] for q in qs})


class Saida(unittest.TestCase):
    def test_grava_csv_e_configuracao_com_resposta_inteira(self):
        with tempfile.TemporaryDirectory() as t:
            csv_saida = Path(t) / "r.csv"
            longa = "A" + " explicação" * 100
            rodar(["--modelo", "m", "--dados", str(base(t)), "--saida", str(csv_saida)], resposta=longa)
            linhas = ler_csv(csv_saida)
            config = json.loads(csv_saida.with_suffix(".json").read_text(encoding="utf-8"))
        self.assertEqual(len(linhas), 2)
        self.assertEqual(linhas[0]["texto_bruto"], longa)
        self.assertEqual(config["gabarito"], "definitivo")
        self.assertEqual(len(config["sha256_dados"]), 64)

    def test_padrao_grava_em_resultados(self):
        with tempfile.TemporaryDirectory() as t, mock.patch.object(A, "PASTA_RESULTADOS", Path(t) / "resultados"):
            rodar(["--modelo", "org/m:1", "--dados", str(base(t))])
            self.assertTrue((Path(t) / "resultados" / "resultados_org_m_1.csv").exists())
            self.assertTrue((Path(t) / "resultados" / "resultados_org_m_1.json").exists())


class Retomada(unittest.TestCase):
    def test_retoma_com_a_mesma_configuracao(self):
        with tempfile.TemporaryDirectory() as t:
            args = ["--modelo", "m", "--dados", str(base(t)), "--saida", str(Path(t) / "r.csv")]
            rodar(args + ["--limite", "1"])
            texto = rodar(args)
            linhas = ler_csv(Path(t) / "r.csv")
        self.assertEqual(len(linhas), 2)
        self.assertNotIn("PARCIAL", texto)

    def test_recusa_temperatura_diferente(self):
        with tempfile.TemporaryDirectory() as t:
            args = ["--modelo", "m", "--dados", str(base(t)), "--saida", str(Path(t) / "r.csv")]
            rodar(args)
            with self.assertRaises(SystemExit) as e:
                rodar(args + ["--temperatura", "0.7"])
        self.assertIn("temperatura", str(e.exception.code))

    def test_recusa_script_diferente(self):
        with tempfile.TemporaryDirectory() as t:
            saida = Path(t) / "r.csv"
            config = {k: "mesmo_valor" for k in A.CONFIG_QUE_TRAVA}
            config["sha256_script"] = "versao_1"
            A.conferir_retomada(saida, config)
            config["sha256_script"] = "versao_2"
            with self.assertRaises(SystemExit) as e:
                A.conferir_retomada(saida, config)
        self.assertIn("sha256_script", str(e.exception.code))

    def test_recusa_base_diferente(self):
        with tempfile.TemporaryDirectory() as t:
            p = base(t)
            args = ["--modelo", "m", "--dados", str(p), "--saida", str(Path(t) / "r.csv")]
            rodar(args)
            p.write_text(p.read_text(encoding="utf-8").replace("Q1", "Q1 corrigida"), encoding="utf-8")
            with self.assertRaises(SystemExit) as e:
                rodar(args)
        self.assertIn("sha256_dados", str(e.exception.code))

    def test_recusa_csv_antigo_sem_configuracao(self):
        with tempfile.TemporaryDirectory() as t:
            antigo = Path(t) / "r.csv"
            antigo.write_text("numero_exame,numero_questao\n46,1\n", encoding="utf-8")
            with self.assertRaises(SystemExit) as e:
                rodar(["--modelo", "m", "--dados", str(base(t)), "--saida", str(antigo)])
        self.assertIn("não tem a configuração", str(e.exception.code))


class Validacao(unittest.TestCase):
    def test_exame_inexistente(self):
        with tempfile.TemporaryDirectory() as t, self.assertRaises(SystemExit), \
                contextlib.redirect_stderr(io.StringIO()):
            rodar(["--modelo", "m", "--dados", str(base(t)), "--saida", str(Path(t) / "r.csv"), "--prova", "99"])

    def test_limite_zero(self):
        with tempfile.TemporaryDirectory() as t, self.assertRaises(SystemExit), \
                contextlib.redirect_stderr(io.StringIO()):
            rodar(["--modelo", "m", "--dados", str(base(t)), "--saida", str(Path(t) / "r.csv"), "--limite", "0"])

    def test_resumo_avisa_rodada_parcial(self):
        with tempfile.TemporaryDirectory() as t:
            texto = rodar(["--modelo", "m", "--dados", str(base(t)), "--saida", str(Path(t) / "r.csv"),
                           "--limite", "1"])
        self.assertIn("PARCIAL", texto)
        self.assertIn("1 de 1 pedidas (2 na seleção completa)", texto)

    def test_resumo_so_conta_a_selecao_atual(self):
        with tempfile.TemporaryDirectory() as t:
            args = ["--modelo", "m", "--dados", str(base(t)), "--saida", str(Path(t) / "r.csv"),
                    "--gabarito", "preliminar"]
            rodar(args)
            texto = rodar(args + ["--prova", "47"])
        self.assertIn("Questões respondidas: 2 de 2", texto)
        self.assertIn("o resumo considera só as 2 desta seleção", texto)


if __name__ == "__main__":
    unittest.main()
