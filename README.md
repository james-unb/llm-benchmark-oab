\# Download de Provas e Gabaritos da OAB



Este projeto realiza o download automatizado das provas e dos gabaritos dos Exames de Ordem Unificados da OAB.



Os documentos são obtidos diretamente do site oficial do Conselho Federal da Ordem dos Advogados do Brasil (OAB).



\## Objetivo



O objetivo deste projeto é coletar e organizar automaticamente documentos dos Exames de Ordem para utilização em análises e projetos relacionados ao benchmark de modelos de linguagem (LLMs).



O programa percorre os exames disponíveis no site da OAB e baixa somente os documentos relacionados a:



\- Provas

\- Cadernos de prova

\- Gabaritos preliminares

\- Gabaritos definitivos



Documentos como editais, comunicados, resultados e outros arquivos administrativos não são baixados.



\## Fonte dos dados



Os documentos são obtidos diretamente do site oficial do Exame de Ordem:



https://examedeordem.oab.org.br/EditaisProvas



\## Tecnologias utilizadas



O projeto foi desenvolvido utilizando:



\- Python

\- uv

\- Playwright



O Playwright é utilizado para acessar automaticamente o site da OAB e navegar entre os diferentes Exames de Ordem.



\## Estrutura do projeto



```text

.

├── baixar\_provas\_OAB.py

├── pyproject.toml

├── uv.lock

├── README.md

└── provas\_oab/

&#x20;   ├── 46\_exame\_de\_ordem\_unificado/

&#x20;   │   ├── PROVA - ...

&#x20;   │   ├── PROVA - ...

&#x20;   │   └── GABARITO - ...

&#x20;   │

&#x20;   ├── 45\_exame\_de\_ordem\_unificado/

&#x20;   │   ├── PROVA - ...

&#x20;   │   └── GABARITO - ...

&#x20;   │

&#x20;   └── ...

