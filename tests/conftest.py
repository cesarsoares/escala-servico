"""Ajustes que valem para a suíte inteira, aplicados ANTES de importar `app.main`.

**Backup automático desligado.** O laço diário (`services/backup.gerar_automatico`)
é iniciado no `lifespan` da aplicação, e todo `TestClient(app)` executa o
lifespan. Ele olha para `settings.database_url`, que nos testes continua sendo o
default — o banco de desenvolvimento na raiz do projeto. Ligado, a suíte
encheria `./backups/` com cópias do banco real de quem desenvolve, cada vez que
alguém rodasse `pytest`. Os testes que exercitam o backup automático o chamam
diretamente, com `tmp_path`.

Isto é módulo de conftest, e não fixture, de propósito: precisa valer no momento
do IMPORT dos módulos de teste, antes que qualquer `TestClient` seja construído.
"""
from app.config import settings

settings.backup_automatico = False
