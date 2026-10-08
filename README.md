# Questões da 1ª fase da OAB

Projeto do time de dados para coletar provas objetivas tipo 1, estruturar uma linha por questão e entregar os dados ao time de LLM.

## Escopo e fonte

O acervo contém **46 exames**: 2010.2, 2010.3 e os exames IV a XLVII. A coleta inclui somente o caderno tipo 1 da 1ª fase, os gabaritos preliminares e definitivos publicados e comunicados oficiais de anulação ou retificação. Não inclui os cadernos tipo 2–4 nem as provas da 2ª fase.

Os PDFs estão em `provas_oab/`, organizados por `numero_exame_ano_Ttrimestre` (por exemplo, `16_2015_T1`). Os identificadores oficiais antigos são mantidos: `2010.2_2010_T3` e `2010.3_2011_T1`. Os trimestres são os trimestres civis da aplicação. Os arquivos preservam o identificador da pasta no nome.

A fonte é a [página oficial de Editais e Provas da OAB/FGV](https://examedeordem.oab.org.br/EditaisProvas?NumeroExame=0). O downloader trata links PDF antigos publicados em HTTP e acessa-os por HTTPS.

O parser também atende aos formatos históricos: 2010.2 e 2010.3 têm 100 questões; os outros exames deste acervo têm 80. O questionário de percepção anexado ao caderno 2010.2 não é uma questão. Nos gabaritos dos exames XII a XIV, o parser lê a tabela que relaciona os tipos de caderno e extrai as respostas do tipo 1. A matéria permanece vazia antes do XVI porque a distribuição histórica ainda não foi validada.

O [plano de extração retificado](plano_extracao_feito.pdf) registra a estrutura e as decisões aplicadas.

## Preparar e executar

Instale o [uv](https://docs.astral.sh/uv/) e execute os comandos na raiz do repositório. É necessário instalar o Chromium do Playwright para baixar os documentos:

```powershell
uv sync
uv run playwright install chromium
uv run extensao tudo --exame-inicial 2010.2
```

O comando baixa documentos que ainda faltam e extrai o acervo local. O corte padrão do downloader é o XVI Exame; use `--exame-inicial 2010.2` para percorrer também os exames antigos. Para executar uma etapa isolada:

```powershell
uv run extensao baixar --exame-inicial 2010.2
uv run extensao extrair
```

Os caminhos são relativos à raiz do repositório: PDFs em `provas_oab/` e resultados em `dados/`. O OCR local é acionado quando a codificação do texto do PDF está corrompida; o RapidOCR pode baixar os modelos necessários na primeira execução.

## Dados entregues

Os artefatos gerados estão versionados em `dados/`:

- `questoes_oab.sqlite`: banco SQLite pronto para consulta;
- `questoes_oab.sql`: schema, registros e view, compatíveis com SQLite;
- `questoes_oab.csv`: CSV UTF-8 com BOM.

Cada linha representa uma questão e inclui número e data do exame, tipo da prova (sempre 1), número da questão, matéria, enunciado, alternativas A–D, respostas preliminar e definitiva, `questao_extraida`, `gabarito_definitivo` e caminhos relativos dos PDFs fonte. Alternativa E não é armazenada. Valores sem fonte ou conteúdo permanecem vazios no CSV e `NULL` no SQLite/SQL.

`resposta_definitiva` contém a resposta final ou `ANULADA`. `gabarito_definitivo` é `true` quando uma fonte oficial confirma a resposta definitiva e `false` quando não há gabarito definitivo separado confirmado. Em edições em que todos os recursos foram indeferidos, a banca manteve o preliminar e não publicou um arquivo definitivo separado: nesses casos, a resposta preliminar continua preenchida, enquanto `resposta_definitiva` e `fonte_gabarito_definitivo` ficam vazias e `gabarito_definitivo` fica `false`. Esse vazio é esperado e não indica falha de coleta de um arquivo existente. Quando comunicado oficial confirma que o preliminar foi mantido integralmente, ele também é registrado como resposta definitiva.

`questao_extraida=false` marca registros com enunciado ou alternativas A–D incompletos. Os registros ficam na tabela principal para consulta e revisão. A view `questoes_llm` filtra questões anuladas, incompletas ou sem resposta definitiva confirmada.

## Uso pelo time de LLM e estado do acervo

Na versão atual, a tabela principal tem **3.720 questões** em **46 exames**; a view `questoes_llm` contém **1.819 questões**. Dez registros têm `questao_extraida=false` e ficam fora da view. A matéria não foi preenchida nos exames anteriores ao XVI porque falta validar a classificação histórica por faixa.

Consulta inicial:

```sql
SELECT numero_exame, data_exame, numero_questao, materia, enunciado,
       alternativa_a, alternativa_b, alternativa_c, alternativa_d,
       resposta_preliminar, resposta_definitiva
FROM questoes_llm;
```
