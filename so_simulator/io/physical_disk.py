from .erros import DiscoFalhou, IOSimError
from .request import IORequest


class PhysicalDisk:
    """Disco rígido simulado: blocos + trilhas + posição da cabeça de leitura.

    Os `n_blocks` blocos ficam distribuídos uniformemente pelas `tracks` trilhas
    (bloco b → trilha b * tracks // n_blocks). Assim, mesmo um disco pequeno
    (16 blocos) exercita o braço ao longo de todo o prato (0..tracks-1).

    Cada read/write apenas ATENDE o dado e registra uma IORequest em `pending`.
    Quem decide a ORDEM de atendimento (e o deslocamento do braço) é o escalonador,
    chamado pelo InputOutput.flush().
    """

    def __init__(self, id, tracks, block_size, n_blocks, head=0):
        self.id = id
        self.tracks = tracks
        self.block_size = block_size
        self.n_blocks = n_blocks
        self.head = min(max(head, 0), tracks - 1)
        self.direcao = "up"              # sentido atual do braço (SCAN/C-SCAN)
        self.failed = False
        self.registrar = True            # False: acessos "silenciosos" (testes/diagnóstico)
        self.pending = []                # IORequest aguardando escalonamento
        self._blocks = [None] * n_blocks
        self.leituras = self.escritas = self.movimento = 0

    # ------------------------------------------------------------ geometria
    def track_of(self, pblock):
        return pblock * self.tracks // self.n_blocks

    def seek(self, track):
        """Move a cabeça até `track` e retorna o deslocamento (nº de trilhas)."""
        delta = abs(track - self.head)
        self.head = track
        self.movimento += delta
        return delta

    # ------------------------------------------------------------ acesso
    def _check(self, pblock):
        if self.failed:
            raise DiscoFalhou(f"Disco {self.id} está em falha")
        if not 0 <= pblock < self.n_blocks:
            raise IOSimError(f"bloco {pblock} fora do Disco {self.id}")

    def _registrar(self, op, pblock, data=None):
        if self.registrar:
            self.pending.append(IORequest(op, pblock, self.track_of(pblock), data, self.id))

    def read(self, pblock) -> bytes:
        self._check(pblock)
        self.leituras += 1
        self._registrar("R", pblock)
        return self._blocks[pblock] or bytes(self.block_size)

    def write(self, pblock, data):
        self._check(pblock)
        if len(data) > self.block_size:
            raise IOSimError("dados maiores que o bloco")
        self.escritas += 1
        self._registrar("W", pblock, data)
        self._blocks[pblock] = bytes(data).ljust(self.block_size, b"\0")

    def peek(self, pblock) -> bytes:
        """Lê o conteúdo SEM contar como E/S (apenas para testes e diagnóstico)."""
        return self._blocks[pblock] or bytes(self.block_size)

    # ------------------------------------------------------------ falhas
    def fail(self):
        """Falha definitiva: o disco deixa de responder e o conteúdo é perdido."""
        self.failed = True
        self._blocks = [None] * self.n_blocks
        self.pending.clear()

    def replace(self):
        """Troca por um disco novo (em branco, funcionando)."""
        self.failed = False
        self._blocks = [None] * self.n_blocks
        self.pending.clear()
