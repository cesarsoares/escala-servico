@echo off
REM ============================================================================
REM  Sistema de Escala de Servico - inicializacao no Windows.
REM
REM  Faz o mesmo que o entrypoint.sh do container, na ordem que importa:
REM  migra o banco, semeia as referencias, anuncia a senha de primeiro acesso
REM  e sobe o servidor. Idempotente: pode rodar a cada boot.
REM
REM  Chamado pela tarefa agendada que instalar-servico.ps1 registra, mas
REM  tambem serve para testar a mao: basta executa-lo.
REM
REM  Sem acentos de proposito - o console do Windows usa a pagina de codigo
REM  850/437 e o log sairia ilegivel.
REM ============================================================================
setlocal
cd /d "%~dp0.."

set "PORTA=%~1"
if "%PORTA%"=="" set "PORTA=8000"

set "LOGDIR=dados\log"
set "LOG=%LOGDIR%\escala.log"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

REM TUDO em dados\: banco, chave de sessao, senha de primeiro acesso, backups
REM automaticos e log. E o que faz "copiar a pasta dados" ser a troca de maquina
REM inteira (manual, 2.7) - e e o mesmo arranjo do container. Sem isto o padrao
REM poria o banco na raiz do projeto, fora da pasta que se copia.
if not defined DATABASE_URL set "DATABASE_URL=sqlite:///./dados/escala.sqlite3"

REM Rotacao simples: acima de ~5 MB o log vira .old. Uma geracao basta - o que
REM interessa e o arranque de hoje, e disco cheio derruba o sistema inteiro.
set "TAM=0"
if exist "%LOG%" for %%A in ("%LOG%") do set "TAM=%%~zA"
if %TAM% GTR 5000000 (
  if exist "%LOG%.old" del "%LOG%.old"
  move /y "%LOG%" "%LOG%.old" >nul
)

REM Prefere o ambiente virtual do projeto; cai no python do PATH se nao houver.
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"

echo.>> "%LOG%"
echo ==================================================================>> "%LOG%"
echo  %DATE% %TIME% - iniciando (porta %PORTA%)>> "%LOG%"
echo ==================================================================>> "%LOG%"

"%PY%" -m alembic upgrade head >> "%LOG%" 2>&1
if errorlevel 1 (
  echo FALHA nas migracoes - o servidor NAO sera iniciado.>> "%LOG%"
  exit /b 1
)

"%PY%" -m app.seeds >> "%LOG%" 2>&1

REM Enquanto nao houver gestor, imprime a senha de primeiro acesso no log e a
REM guarda em dados\primeiro-acesso.txt. Some sozinha quando o gestor e criado.
"%PY%" -m app.seeds.primeiro_acesso >> "%LOG%" 2>&1

REM --host 0.0.0.0 para que a consulta (aberta, regra 13.1) responda ao efetivo
REM na rede da OM, e nao so nesta maquina.
"%PY%" -m uvicorn app.main:app --host 0.0.0.0 --port %PORTA% >> "%LOG%" 2>&1
