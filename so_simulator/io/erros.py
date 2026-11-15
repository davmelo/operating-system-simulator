from ..fs.erros import FSError


class IOSimError(FSError):
    """Erro da camada de E/S (o shell mostra a mensagem ao usuário)."""


class DiscoFalhou(IOSimError):
    """Acesso a um disco físico que está em estado de falha."""


class ArranjoInoperante(IOSimError):
    """Falhas acima da tolerância do arranjo: os dados não podem ser acessados."""
