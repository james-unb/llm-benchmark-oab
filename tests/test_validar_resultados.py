"""Testes do validador com base e resultados sintéticos, sem chamar LLM."""
import contextlib
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path

import avaliar_oab as A
import validar_resultados as V


class ValidarResultados(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name) / "base.csv"
        self.resultado = Path(self.tmp.name) / "resultado.csv"
        self.config = self.resultado.with_suffix(".json")
        campos = ["numero_exame", "numero_questao", "materia", "enunciado",
                  "alternativa_a", "alternativa_b", "alternativa_c", "alternativa_d",
                  "resposta_preliminar", "resposta_definitiva", "questao_extraida"]
        with self.base.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(campos)
            for n, g in [(1, "A"), (2, "B"), (3, "ANULADA")]:
                w.writerow(["47", n, "Civil", "Enunciado", "a", "b", "c", "d", "A", g, True])
        self.c = {"modelo": "simulado", "onde": "ollama", "temperatura": 0.0,
                  "max_tokens": 1024, "gabarito": "definitivo", "prova": ["47"],
                  "limite": None, "dados": str(self.base), "semente": A.SEMENTE,
                  "sha256_dados": V.sha256(self.base.read_bytes()),
                  "sha256_script": V.sha256(Path(A.__file__).read_bytes()),
                  "sha256_prompt": V.sha256(A.PROMPT.encode())}
        self.linhas = [dict(zip(A.CAMPOS, ["47", "1", "Civil", "A", "definitivo", "A", 1, 1.0, "A"])),
                       dict(zip(A.CAMPOS, ["47", "2", "Civil", "B", "definitivo", "A", 0, 3.0, "A"]))]
        self.gravar()

    def gravar(self):
        self.config.write_text(json.dumps(self.c), encoding="utf-8")
        with self.resultado.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, A.CAMPOS)
            w.writeheader()
            w.writerows(self.linhas)

    def validar(self, parcial=False):
        return V.validar(self.resultado, self.base, parcial)

    def test_resultado_valido(self):
        r = self.validar()
        self.assertEqual(r["acuracia"], 0.5)
        self.assertEqual(r["tempo_medio"], 2.0)
        self.assertFalse(r["parcial"])

    def test_rejeita_duplicata(self):
        self.linhas.append(self.linhas[0].copy())
        self.gravar()
        with self.assertRaisesRegex(ValueError, "duplicata"):
            self.validar()

    def test_rejeita_gabarito_incorreto(self):
        self.linhas[0]["gabarito"] = "C"
        self.gravar()
        with self.assertRaisesRegex(ValueError, "gabarito diverge"):
            self.validar()

    def test_rejeita_acerto_inconsistente(self):
        self.linhas[1]["acertou"] = 1
        self.gravar()
        with self.assertRaisesRegex(ValueError, "acertou inconsistente"):
            self.validar()

    def test_rejeita_anulada(self):
        self.linhas.append(dict(self.linhas[0], numero_questao="3"))
        self.gravar()
        with self.assertRaisesRegex(ValueError, "não elegível"):
            self.validar()

    def test_rejeita_hash_base(self):
        self.c["sha256_dados"] = "0" * 64
        self.gravar()
        with self.assertRaisesRegex(ValueError, "Hash incompatível"):
            self.validar()

    def test_rejeita_hash_script(self):
        self.c["sha256_script"] = "0" * 64
        self.gravar()
        with self.assertRaisesRegex(ValueError, "Hash incompatível"):
            self.validar()

    def test_rejeita_hash_prompt(self):
        self.c["sha256_prompt"] = "0" * 64
        self.gravar()
        with self.assertRaisesRegex(ValueError, "Hash do prompt"):
            self.validar()

    def test_rejeita_resposta_inconsistente(self):
        self.linhas[1]["texto_bruto"] = "B"
        self.gravar()
        with self.assertRaisesRegex(ValueError, "diverge de texto_bruto"):
            self.validar()

    def test_aceita_resposta_sem_letra_como_erro(self):
        self.linhas[1].update(resposta_modelo="", texto_bruto="Não sei")
        self.gravar()
        self.assertEqual(self.validar()["sem_letra"], 1)

    def test_rejeita_tempo_invalido(self):
        self.linhas[0]["segundos"] = "nan"
        self.gravar()
        with self.assertRaisesRegex(ValueError, "tempo inválido"):
            self.validar()

    def test_parcial_exige_opcao_explicita(self):
        self.linhas.pop()
        self.gravar()
        with self.assertRaisesRegex(ValueError, "ausentes"):
            self.validar()
        self.assertTrue(self.validar(parcial=True)["parcial"])

    def test_limite_respeita_selecao_do_avaliador(self):
        self.c["limite"] = 1
        q = A.carregar_questoes(self.base.read_bytes(), "definitivo")[0]
        self.linhas = [r for r in self.linhas if r["numero_questao"] == q["numero_questao"]]
        self.gravar()
        self.assertEqual(self.validar()["esperadas"], 1)

    def test_json_ausente(self):
        self.config.unlink()
        with self.assertRaises(FileNotFoundError):
            self.validar()


    def rodar_com_resumo(self, *args):
        resumo = Path(self.tmp.name) / "resumo.md"
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            codigo = V.main([str(self.resultado), "--base", str(self.base),
                             "--resumo", str(resumo), *args])
        return codigo, resumo.read_text(encoding="utf-8")

    def test_resumo_valido(self):
        codigo, resumo = self.rodar_com_resumo()
        self.assertEqual(codigo, 0)
        self.assertIn("OK: 1 | Parciais: 0 | Erros: 0", resumo)
        self.assertIn("simulado | 47 | definitivo | 2/2 | 1 | 50.0% | 0 | 2.00 | OK", resumo)

    def test_resumo_invalido(self):
        self.linhas[1]["acertou"] = 1
        self.gravar()
        codigo, resumo = self.rodar_com_resumo()
        self.assertEqual(codigo, 1)
        self.assertIn("Erros: 1", resumo)
        self.assertIn("coluna acertou inconsistente", resumo)
        self.assertIn("| ERRO |", resumo)
        self.assertNotIn("50.0%", resumo)

    def test_resumo_parcial(self):
        self.linhas.pop()
        self.gravar()
        codigo, resumo = self.rodar_com_resumo("--permitir-parcial")
        self.assertEqual(codigo, 0)
        self.assertIn("Parciais: 1", resumo)
        self.assertIn("1/2 | 1 | 100.0%", resumo)
        self.assertIn("| PARCIAL |", resumo)

    def test_resumo_misto_preserva_falha(self):
        outro = self.resultado.with_name("invalido.csv")
        outro.write_bytes(self.resultado.read_bytes())
        # O segundo resultado não tem JSON correspondente.
        codigo, resumo = self.rodar_com_resumo(str(outro))
        self.assertEqual(codigo, 1)
        self.assertIn("OK: 1 | Parciais: 0 | Erros: 1", resumo)
        self.assertIn("invalido.csv", resumo)
        self.assertIn("| ERRO |", resumo)

    def test_resumo_escapa_markdown(self):
        self.c["modelo"] = "modelo|<b>\n[link]"
        self.gravar()
        codigo, resumo = self.rodar_com_resumo()
        self.assertEqual(codigo, 0)
        self.assertIn("modelo&#124;&lt;b&gt; &#91;link&#93;", resumo)
        self.assertNotIn("modelo|<b>", resumo)

    def test_resumo_sem_resultados(self):
        self.assertIn("Nenhum resultado novo ou modificado.", V.gerar_resumo([]))

    def test_hash_quebras_de_linha_nos_dois_sentidos(self):
        caminho = Path(self.tmp.name) / "linhas.txt"
        lf = b"a\nb\n"
        crlf = b"a\r\nb\r\n"
        for conteudo, esperado in ((lf, crlf), (crlf, lf), (lf, lf), (crlf, crlf)):
            with self.subTest(conteudo=conteudo, esperado=esperado):
                caminho.write_bytes(conteudo)
                self.assertEqual(V.ler_versao(caminho, V.sha256(esperado)), esperado)
        with self.assertRaisesRegex(ValueError, "Hash incompatível"):
            V.ler_versao(caminho, V.sha256(b"outro"))


if __name__ == "__main__":
    unittest.main()
