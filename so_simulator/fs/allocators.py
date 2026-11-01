import math
from .erros import FSError


class Allocator:
    """Decide QUAIS blocos cada arquivo usa e mantém a tabela de alocação (slots).

    slots[i] é None (livre) ou um dict {node, kind, seq, used}; kind = RAIZ | INODE | DADO.
    Subclasses implementam apenas escolher(n).
    """
    nome = "base"

    def __init__(self, device):
        self.dev = device
        self.slots = [None] * device.n_blocks

    # --- utilidades
    def reservar_raiz(self, root):
        self.slots[0] = {"node": root, "kind": "RAIZ"}

    def livres(self):
        return [i for i, s in enumerate(self.slots) if s is None]

    def faixas_livres(self):
        """Sequências de blocos livres: lista de (início, tamanho)."""
        faixas, i, n = [], 0, len(self.slots)
        while i < n:
            if self.slots[i] is None:
                j = i
                while j + 1 < n and self.slots[j + 1] is None:
                    j += 1
                faixas.append((i, j - i + 1))
                i = j + 1
            else:
                i += 1
        return faixas

    # --- a ser implementado por cada estratégia
    def escolher(self, n):
        """Retorna (bloco_inode_ou_None, [blocos_de_dados]) ou levanta FSError."""
        raise NotImplementedError

    def fragmentacao_externa(self):
        """Retorna (bytes_de_fragmentação_externa, precisa_compactar)."""
        return 0, False

    # --- operações comuns
    def allocate(self, node, size):
        bs = self.dev.block_size
        inode, dados = self.escolher(math.ceil(size / bs))   # falha antes de alterar o estado
        if inode is not None:
            self.slots[inode] = {"node": node, "kind": "INODE"}
        for k, b in enumerate(dados):
            self.slots[b] = {"node": node, "kind": "DADO", "seq": k + 1,
                             "used": min(bs, size - k * bs)}
        node.blocks, node.inode, node.size = dados, inode, size

    def release(self, node):
        usados = node.blocks + ([node.inode] if node.inode is not None else [])
        for b in usados:
            self.slots[b] = None
            self.dev.clear_block(b)
        node.blocks, node.inode = [], None


class ContiguaAllocator(Allocator):
    """Blocos seguidos (first-fit). Gera fragmentação externa."""
    nome = "contigua"

    def escolher(self, n):
        if n == 0:
            return None, []
        inicio = next((s for s, ln in self.faixas_livres() if ln >= n), None)
        if inicio is None:
            if n <= len(self.livres()):
                raise FSError(f"fragmentação externa: há {len(self.livres())} blocos livres, "
                              f"mas nenhuma sequência de {n}. Rode 'compact'.")
            raise FSError("disco cheio")
        return None, list(range(inicio, inicio + n))

    def fragmentacao_externa(self):
        faixas = self.faixas_livres()
        if len(faixas) <= 1:
            return 0, False
        total = sum(ln for _, ln in faixas)
        return (total - max(ln for _, ln in faixas)) * self.dev.block_size, True


class IndexadaAllocator(Allocator):
    """1 bloco de índice (i-node) + n blocos de dados em qualquer lugar livre."""
    nome = "indexada"

    def escolher(self, n):
        livres = self.livres()
        if len(livres) < n + 1:
            raise FSError("disco cheio")
        return livres[0], livres[1:n + 1]


ALOCADORES = {"contigua": ContiguaAllocator, "indexada": IndexadaAllocator}
