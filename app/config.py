"""Configurações lidas de variáveis de ambiente (.env)."""
import os
import secrets
from datetime import time
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def chave_persistente(caminho: str) -> str:
    """Lê a chave de assinatura do arquivo; cria uma aleatória na primeira vez.

    Existe para que uma instalação nova NUNCA rode com chave conhecida. O
    código é público (o repositório está no GitHub): uma chave padrão embutida
    permitiria a qualquer um forjar o cookie de sessão do gestor e entrar na
    gestão de qualquer OM que não tivesse trocado o valor — e trocar dependia
    de a TI local lembrar de fazê-lo.

    O arquivo mora junto do banco (volume do container), então a chave sobrevive
    a `docker compose up` e ninguém é deslogado a cada reinício.
    """
    arquivo = Path(caminho)
    if arquivo.is_file():
        guardada = arquivo.read_text(encoding="utf-8").strip()
        if guardada:
            return guardada
    nova = secrets.token_urlsafe(48)
    try:
        arquivo.parent.mkdir(parents=True, exist_ok=True)
        arquivo.write_text(nova + "\n", encoding="utf-8")
        os.chmod(arquivo, 0o600)          # no Windows o efeito é parcial
    except OSError:
        # Sem onde gravar (volume só-leitura), a chave vale para este processo:
        # as sessões caem no próximo restart, mas o sistema sobe — e jamais com
        # uma chave que alguém possa adivinhar lendo o código.
        pass
    return nova


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./escala.sqlite3"
    # Identificação da OM no cabeçalho das telas. Uma instalação por OM, e o
    # escopo inclui batalhões — por isso a sigla NÃO fica fixa no código nem na
    # folha de estilo: a TI local define no .env ao instalar.
    om_sigla: str = "QG"                    # crachá do cabeçalho
    om_nome: str = "QG do CMS"              # nome ao lado do título
    # Assina o cookie de sessão do gestor. Em branco = gerada e guardada em
    # `secret_key_file` no primeiro boot (ver `chave_persistente`). Definir
    # SECRET_KEY no ambiente continua valendo e tem precedência.
    secret_key: str = ""
    secret_key_file: str = "dados/secret_key"
    # Autenticação da gestão (regra 11). A consulta é aberta (regra 13.1); só
    # os endpoints de gestão exigem o token JWT assinado com secret_key.
    jwt_algoritmo: str = "HS256"
    token_expira_min: int = 12 * 60       # 12h — turno de serviço
    # Backup automático diário em dados/backups (últimos 7). Ligado por padrão:
    # o cenário que o justifica — "esta máquina está com problema, preciso subir
    # outra com o estado de ontem" — não sobrevive a depender de alguém lembrar
    # de clicar. Desligar só faz sentido em teste ou quando o backup é feito por
    # fora (snapshot de VM, rotina do próprio TI).
    backup_automatico: bool = True
    # Defaults sugeridos ao CRIAR uma escala nova. A folga e a janela reais são
    # atributos de cada escala (regras 7.2 e 2.4), não constantes globais.
    folga_minima_horas: int = 48          # regra 7.2.1 (default sugerido)
    hora_inicio_servico: str = "08:00"    # regra 2.4 (default sugerido)

    @model_validator(mode="after")
    def _garantir_secret_key(self):
        """Sem SECRET_KEY no ambiente, usa (ou cria) a do arquivo — nunca uma
        chave embutida no código."""
        if not self.secret_key.strip():
            self.secret_key = chave_persistente(self.secret_key_file)
        return self

    @property
    def inicio_servico(self) -> time:
        h, m = self.hora_inicio_servico.split(":")
        return time(int(h), int(m))


settings = Settings()
