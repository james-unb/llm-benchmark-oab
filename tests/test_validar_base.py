import contextlib
import csv
import io
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import validar_base as V


def questao(n=1, **mudancas):
    q = dict.fromkeys(V.CAMPOS, '')
    q.update(numero_exame='16', data_exame='2015-03-15', tipo_prova='1',
             numero_questao=str(n), materia='Civil', enunciado="Texto d'água;\nPergunta?",
             alternativa_a='Resposta A', alternativa_b='Resposta B',
             alternativa_c='Resposta C', alternativa_d='Resposta D',
             resposta_preliminar='A', resposta_definitiva='A', questao_extraida='True',
             fonte_prova='provas/exame.pdf', fonte_gabarito_preliminar='pre.pdf',
             fonte_gabarito_definitivo='def.pdf', gabarito_definitivo='true')
    q.update(mudancas)
    return q


def exportar(pasta, linhas):
    with (pasta/'questoes_oab.csv').open('w', encoding='utf-8-sig', newline='') as f:
        w=csv.DictWriter(f,fieldnames=V.CAMPOS); w.writeheader(); w.writerows(linhas)
    db=pasta/'questoes_oab.sqlite'
    if db.exists(): db.unlink()
    with contextlib.closing(sqlite3.connect(db)) as c:
        c.executescript(V.DDL+'\n'+'\n'.join(V.DDL_RESTANTE))
        for q in linhas:
            valores=[int(q[k]) if k in ('tipo_prova','numero_questao') else int(q[k]=='True') if k=='questao_extraida' else q[k] or None for k in V.CAMPOS]
            c.execute('INSERT INTO questoes_oab ('+', '.join(V.CAMPOS)+') VALUES ('+', '.join('?' for _ in V.CAMPOS)+')', valores)
        c.commit()
        (pasta/'questoes_oab.sql').write_text('\n'.join(c.iterdump())+'\n',encoding='utf-8')


class ValidarBase(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory(); self.addCleanup(t.cleanup)
        self.raiz=Path(t.name); self.pasta=self.raiz/'dados'; self.pasta.mkdir()
        exportar(self.pasta,[questao(),questao(2)])

    def executar(self,*args):
        out=io.StringIO()
        with contextlib.redirect_stdout(out):
            codigo=V.main(['--pasta',str(self.pasta),*args])
        return codigo,out.getvalue()

    def indice(self,linhas):
        erros=[]; indice=V.indexar(linhas,'Teste',erros)
        return indice,erros

    def test_exportacoes_iguais(self):
        codigo,saida=self.executar()
        self.assertEqual(codigo,0); self.assertIn('| SQLite | OK |',saida)
        self.assertIn('Erros estruturais: **0**',saida)

    def test_sql_divergente(self):
        p=self.pasta/'questoes_oab.sql'
        p.write_text(p.read_text(encoding='utf-8').replace('Resposta A','Alterada'),encoding='utf-8')
        codigo,saida=self.executar(); self.assertEqual(codigo,1)
        self.assertIn('diverge do CSV em alternativa_a',saida)

    def test_sqlite_divergente(self):
        with contextlib.closing(sqlite3.connect(self.pasta/'questoes_oab.sqlite')) as c:
            c.execute("UPDATE questoes_oab SET materia='Penal'"); c.commit()
        codigo,saida=self.executar(); self.assertEqual(codigo,1)
        self.assertIn('SQLite: 16/Q1 diverge',saida)

    def test_sql_nao_executa_instrucoes(self):
        p=self.pasta/'questoes_oab.sql'
        p.write_text(p.read_text(encoding='utf-8')+'\nATTACH DATABASE \'intruso.sqlite\' AS outro;',encoding='utf-8')
        codigo,saida=self.executar(); self.assertEqual(codigo,1)
        self.assertIn('nada foi executado',saida)
        self.assertFalse((self.pasta/'intruso.sqlite').exists())

    def test_sqlite_ausente_nao_cria_arquivo(self):
        p=self.pasta/'questoes_oab.sqlite'; p.unlink()
        self.assertEqual(self.executar()[0],1); self.assertFalse(p.exists())

    def test_duplicata(self):
        _,erros=self.indice([questao(),questao()]); self.assertTrue(any('duplicada' in e for e in erros))

    def test_gabarito_invalido_e_flag(self):
        _,erros=self.indice([questao(resposta_definitiva='E'),questao(2,gabarito_definitivo='false')])
        self.assertEqual(len(erros),2)

    def test_elegivel_sem_alternativa(self):
        _,erros=self.indice([questao(alternativa_c=' ')]); self.assertTrue(any('campo textual ausente' in e for e in erros))

    def test_nao_extraida_e_anulada_nao_bloqueiam(self):
        exportar(self.pasta,[questao(questao_extraida='False',enunciado='',alternativa_a=''),questao(2,resposta_definitiva='ANULADA')])
        codigo,saida=self.executar(); self.assertEqual(codigo,0); self.assertIn('| Questões não extraídas | 1 |',saida)
        self.assertIn('| Elegíveis (inclui preliminar) | 0 |',saida)

    def test_elegibilidade_preliminar_e_antigas(self):
        indice,erros=self.indice([questao(resposta_definitiva='',gabarito_definitivo='false'),questao(2,numero_exame='2010.2',resposta_definitiva='E'),questao(3)])
        self.assertFalse(erros); a=V.auditar(indice)
        self.assertEqual((len(a['definitivo']),len(a['preliminar'])),(1,2))

    def test_alertas_textuais_nao_bloqueiam(self):
        exportar(self.pasta,[questao(materia='',alternativa_a='Mesmo',alternativa_b='mesmo',alternativa_c='Outro',alternativa_d='Exame de Ordem Unificado. Questão 2. Questionário de percepção'),questao(2,questao_extraida='False')])
        codigo,saida=self.executar(); self.assertEqual(codigo,0)
        for label in ('Marcador da questão seguinte','Possível cabeçalho residual','Alternativas idênticas','Questão extraída seguida de questão ausente','Possível questionário de percepção','Alternativa D desproporcional'):
            self.assertIn('| '+label+' | 1 |',saida)

    def test_comparacao_por_chave_nao_por_ordem(self):
        a,_=self.indice([questao(),questao(2)]); b,_=self.indice([questao(2),questao()]); erros=[]
        V.comparar(a,b,'SQL',erros); self.assertFalse(erros)

    def test_comparacao_git(self):
        def git(*args):
            return subprocess.check_output(['git','-C',str(self.raiz),'-c','user.name=Teste','-c','user.email=teste@example.invalid','-c','commit.gpgsign=false',*args])
        git('init','-q'); git('add','dados/questoes_oab.csv'); git('commit','-qm','base')
        exportar(self.pasta,[questao(alternativa_d='Texto Questão 2'),questao(2)])
        with patch.object(V,'RAIZ',self.raiz):
            codigo,saida=self.executar('--comparar-ref','HEAD')
        self.assertEqual(codigo,0); self.assertIn('| Marcador da questão seguinte | 0 | 1 | +1 |',saida)
        self.assertIn('Novos (até 20): 16/Q1',saida)

    def test_comparacao_identifica_registros_adicionados_e_retirados(self):
        antes, _ = self.indice([questao(), questao(2)])
        atual, _ = self.indice([questao(), questao(3)])
        saida = V.relatorio(V.auditar(atual), [], {}, V.auditar(antes), 'base')
        self.assertIn('Registros adicionados: 1; retirados: 1.', saida)
        self.assertIn('Adicionados (até 20): 16/Q3', saida)
        self.assertIn('Retirados (até 20): 16/Q2', saida)

    def test_comparacao_inexistente_avisa(self):
        with patch.object(V,'ler_anterior',side_effect=ValueError('erro')):
            codigo,saida=self.executar('--comparar-ref','inexistente')
        self.assertEqual(codigo,0); self.assertIn('Comparação indisponível',saida)

    def test_resumo_acrescenta(self):
        p=self.raiz/'resumo.md'; p.write_text('anterior\n',encoding='utf-8')
        self.assertEqual(self.executar('--resumo',str(p))[0],0)
        self.assertTrue(p.read_text(encoding='utf-8').startswith('anterior\n'))
        self.assertIn('Integridade e qualidade',p.read_text(encoding='utf-8'))


if __name__=='__main__': unittest.main()
