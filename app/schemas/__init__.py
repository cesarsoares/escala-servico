"""Contratos Pydantic (entrada/saída da API).

Espelham os modelos ORM (app/models/) mas NÃO são o banco: validam a entrada e
moldam a saída. As regras de negócio pesadas (fila, folga, concorrência) seguem
no domínio (app/domain/) e na camada de serviço — aqui ficam só as validações
locais que espelham as constraints (piso de folga, fim>=inicio, etc.).
"""
from app.schemas.base import Entrada, Resposta
from app.schemas.calendario import (
    FeriadoCreate, FeriadoOut, FeriadoUpdate,
    OverrideDiaOut, OverrideDiaUpsert,
)
from app.schemas.escala import (
    ConcorrenteIn,
    EscalaConcorrenteCreate, EscalaConcorrenteOut,
    EscalaCreate, EscalaDetalheOut, EscalaOut, EscalaUpdate,
    ParticipacaoCreate, ParticipacaoOut, ParticipanteIn,
    PostoCreate, PostoOut,
)
from app.schemas.gestao import (
    AuditoriaOut, UsuarioCreate, UsuarioOut, UsuarioUpdate,
)
from app.schemas.impedimento import (
    ImpedimentoCreate, ImpedimentoDetalheOut, ImpedimentoOut, ImpedimentoUpdate,
)
from app.schemas.militar import (
    FichaImportadaOut, MilitarCreate, MilitarDetalheOut, MilitarOut, MilitarResumoOut,
    MilitarUpdate, RascunhoMilitar,
)
from app.schemas.referencia import (
    CirculoHierarquicoOut,
    OrganizacaoMilitarCreate, OrganizacaoMilitarOut, OrganizacaoMilitarUpdate,
    PostoGraduacaoDetalheOut, PostoGraduacaoOut,
    TipoImpedimentoCreate, TipoImpedimentoOut, TipoImpedimentoUpdate,
)
from app.schemas.servico import (
    EscalacaoDiaOut, EscalacaoOut, EscalacaoPedido,
    PermutaCreate, PermutaOut,
    ServicoCreate, ServicoDetalheOut, ServicoOut,
)

__all__ = [
    "Entrada", "Resposta",
    # referência
    "OrganizacaoMilitarCreate", "OrganizacaoMilitarUpdate", "OrganizacaoMilitarOut",
    "CirculoHierarquicoOut",
    "PostoGraduacaoOut", "PostoGraduacaoDetalheOut",
    "TipoImpedimentoCreate", "TipoImpedimentoUpdate", "TipoImpedimentoOut",
    # militar
    "MilitarCreate", "MilitarUpdate", "MilitarOut", "MilitarDetalheOut", "MilitarResumoOut",
    "RascunhoMilitar", "FichaImportadaOut",
    # escala
    "EscalaCreate", "EscalaUpdate", "EscalaOut", "EscalaDetalheOut",
    "PostoCreate", "PostoOut",
    "ParticipacaoCreate", "ParticipacaoOut", "ParticipanteIn",
    "EscalaConcorrenteCreate", "EscalaConcorrenteOut", "ConcorrenteIn",
    # calendário
    "FeriadoCreate", "FeriadoUpdate", "FeriadoOut",
    "OverrideDiaUpsert", "OverrideDiaOut",
    # impedimento
    "ImpedimentoCreate", "ImpedimentoUpdate", "ImpedimentoOut", "ImpedimentoDetalheOut",
    # serviço
    "ServicoCreate", "ServicoOut", "ServicoDetalheOut",
    "PermutaCreate", "PermutaOut",
    "EscalacaoPedido", "EscalacaoDiaOut", "EscalacaoOut",
    # gestão
    "UsuarioCreate", "UsuarioUpdate", "UsuarioOut",
    "AuditoriaOut",
]
