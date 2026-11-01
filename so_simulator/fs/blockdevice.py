class BlockDevice:
    """Disco simulado: só sabe ler e escrever blocos de tamanho fixo.

    A Etapa 2 (braço de disco, RAID) pode substituir/estender esta classe
    mantendo a mesma interface.
    """

    def __init__(self, block_size=4096, n_blocks=16):
        self.block_size = block_size
        self.n_blocks = n_blocks
        self._blocks = [None] * n_blocks
        self.leituras = 0
        self.escritas = 0

    def _check(self, i):
        if not 0 <= i < self.n_blocks:
            raise IndexError(f"bloco {i} fora do disco")

    def read_block(self, i):
        self._check(i)
        self.leituras += 1
        return (self._blocks[i] or b"").ljust(self.block_size, b"\0")

    def write_block(self, i, dados):
        self._check(i)
        if len(dados) > self.block_size:
            raise ValueError("dados maiores que o bloco")
        self.escritas += 1
        self._blocks[i] = bytes(dados)

    def clear_block(self, i):
        self._check(i)
        self._blocks[i] = None
