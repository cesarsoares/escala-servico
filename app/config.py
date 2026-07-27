"""Configurações lidas de variáveis de ambiente (.env)."""
from datetime import time
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./escala.sqlite3"
    # Identificação da OM no cabeçalho das telas. Uma instalação por OM, e o
    # escopo inclui batalhões — por isso a sigla NÃO fica fixa no código nem na
    # folha de estilo: a TI local define no .env ao instalar.
    om_sigla: str = "QG"                    # crachá do cabeçalho
    om_nome: str = "QG do CMS"              # nome ao lado do título
    # >=32 bytes p/ HS256; ainda assim INSEGURO — a TI local troca em produção.
    secret_key: str = "dev-inseguro-troque-em-producao-por-uma-chave-real"
    # Autenticação da gestão (regra 11). A consulta é aberta (regra 13.1); só
    # os endpoints de gestão exigem o token JWT assinado com secret_key.
    jwt_algoritmo: str = "HS256"
    token_expira_min: int = 12 * 60       # 12h — turno de serviço
    # Defaults sugeridos ao CRIAR uma escala nova. A folga e a janela reais são
    # atributos de cada escala (regras 7.2 e 2.4), não constantes globais.
    folga_minima_horas: int = 48          # regra 7.2.1 (default sugerido)
    hora_inicio_servico: str = "08:00"    # regra 2.4 (default sugerido)

    @property
    def inicio_servico(self) -> time:
        h, m = self.hora_inicio_servico.split(":")
        return time(int(h), int(m))


settings = Settings()
