from dataclasses import dataclass


@dataclass
class IORequest:
    """Requisição de E/S a um disco físico.

    op    : "R" (leitura) ou "W" (escrita)
    block : bloco físico dentro do disco
    track : trilha onde o bloco está (é isto que o escalonador enxerga)
    data  : conteúdo (só nas escritas)
    disk  : id (1, 2, 3...) do disco que atende a requisição
    """
    op: str
    block: int
    track: int = 0
    data: bytes | None = None
    disk: int = 0
