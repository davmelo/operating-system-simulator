from ..fs.erros import FSError


class MemoriaError(FSError):
    """Erro do gerenciador de memória (o shell mostra a mensagem ao usuário)."""


class FragmentacaoExterna(MemoriaError):
    """Há memória livre suficiente, mas nenhum bloco contíguo grande o bastante."""
