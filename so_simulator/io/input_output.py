"""Camada de E/S: fica entre o Sistema de Arquivos (Etapa 1) e o armazenamento físico.

Tem a mesma interface do BlockDevice (read_block / write_block / clear_block /
block_size / n_blocks), então o FileSystem a usa sem saber que existe escalonamento,
RAID ou SSD por baixo:

    FileSystem → InputOutput → RAIDArray (discos com trilhas) | SSD (FTL)

Funcionamento:
  * escritas ficam na fila e são aplicadas no flush() (fim de cada comando do shell),
    o que permite detectar faixas completas no RAID 5 e enxergar várias requisições
    de uma vez no escalonador;
  * leituras são atendidas na hora; as requisições físicas são registradas nos discos;
  * no flush, o escalonador ordena as requisições de cada disco, move a cabeça e
    imprime o plano (Exemplos 2.1 e 2.2 do enunciado).
"""
from ..fs.util import fmt
from .erros import IOSimError
from .raid import RAIDArray
from .scheduler import ALGORITMOS, NOMES, escalonar, formatar
from .ssd import SSD


class InputOutput:
    def __init__(self, backend, algo="fcfs"):
        self.backend = backend
        self.algo = "fcfs"
        self.set_algo(algo)
        self.log = True
        self.etiqueta = "E/S"                            # prefixo dos logs ("E/S - SWAP" no swap)
        self.origem = "Sistema de Arquivos"              # quem gera as solicitações
        self.rotulo = lambda b: f"Bloco lógico {b}"      # o main troca por nome de arquivo/parte
        self._pend_w = {}
        self._ops = []                                   # (op, bloco) lógicos do comando atual
        self._valid = set()                              # blocos lógicos já gravados
        self.leituras = self.escritas = 0
        self.ultimo_escrito = None

    # ------------------------------------------------------------ interface de BlockDevice
    @property
    def block_size(self):
        return self.backend.block_size

    @property
    def n_blocks(self):
        return self.backend.capacidade

    def _check(self, i):
        if not 0 <= i < self.n_blocks:
            raise IndexError(f"bloco {i} fora do disco")

    def read_block(self, i):
        self._check(i)
        self.leituras += 1
        if i in self._pend_w:                            # escrita ainda na fila
            return self._pend_w[i]
        if i not in self._valid:                         # nunca gravado: zeros, sem E/S
            return bytes(self.block_size)
        self._ops.append(("R", i))
        return self.backend.read(i)

    def write_block(self, i, dados):
        self._check(i)
        if len(dados) > self.block_size:
            raise ValueError("dados maiores que o bloco")
        self.escritas += 1
        self._pend_w[i] = bytes(dados).ljust(self.block_size, b"\0")
        self._ops.append(("W", i))

    def clear_block(self, i):
        """O FS liberou o bloco (só metadado: nenhum acesso ao disco; no SSD vira TRIM)."""
        self._check(i)
        if i in self._pend_w:
            del self._pend_w[i]
            self._ops = [o for o in self._ops if o != ("W", i)]
        if i in self._valid:
            self._valid.discard(i)
            self.backend.trim(i)

    # ------------------------------------------------------------ configuração
    def set_algo(self, algo):
        if algo not in ALGORITMOS:
            raise IOSimError(f"algoritmo inválido: {algo} (use {', '.join(ALGORITMOS)})")
        self.algo = algo

    def trocar_backend(self, novo):
        """Troca RAID/SSD preservando os dados: o FS usa endereços lógicos, que não mudam."""
        self.flush(silencioso=True)
        antigo = self.backend
        if novo.capacidade != antigo.capacidade or novo.block_size != antigo.block_size:
            raise IOSimError("capacidade do novo dispositivo difere da atual")
        dados = {}
        if antigo.kind == "hdd":
            with antigo.silencio():
                for b in sorted(self._valid):
                    dados[b] = antigo.read(b)
        else:
            for b in sorted(self._valid):
                dados[b] = antigo.read(b)
        if dados:
            novo.write_batch(dados)
        novo.limpar_pendencias()
        novo.reiniciar_estatisticas()
        self.backend = novo
        self._ops.clear()

    # ------------------------------------------------------------ flush: escalona e mostra
    def flush(self, silencioso=False):
        escritas, self._pend_w = self._pend_w, {}
        ops, self._ops = self._ops, []
        be = self.backend
        if escritas:
            try:
                be.write_batch(escritas, self.rotulo)
            except Exception:
                be.limpar_pendencias()
                raise
            self._valid.update(escritas)
            self.ultimo_escrito = list(escritas)[-1]
        mostrar = self.log and not silencioso
        if be.kind == "ssd":
            self._flush_ssd(ops, mostrar)
        else:
            self._flush_hdd(ops, mostrar)

    def _cabecalho(self, ops):
        if not ops:
            return
        nomes = {"R": "leitura", "W": "gravação"}
        itens = [f"{nomes[op]} do bloco {b}" for op, b in ops]
        if len(itens) > 8:
            itens = itens[:8] + [f"... (+{len(itens) - 8})"]
        print(f"[{self.etiqueta}] Solicitações do {self.origem}: {', '.join(itens)}")

    def _flush_hdd(self, ops, mostrar):
        be = self.backend
        planos = []
        for d in be.disks:
            if not d.pending:
                continue
            trilhas = [r.track for r in d.pending]
            inicio = d.head
            plano = escalonar(self.algo, inicio, trilhas, d.tracks - 1, d.direcao)
            for trilha, _ in plano.passos[1:]:
                d.seek(trilha)
            d.direcao = plano.direcao_final
            d.pending.clear()
            planos.append((d, inicio, trilhas, plano))
        dist, eventos = list(be.distribuicao), list(be.eventos)
        be.distribuicao.clear()
        be.eventos.clear()
        if not mostrar or not (ops or planos or dist or eventos):
            return
        multi = be.n > 1
        self._cabecalho(ops)
        if multi:
            print(f"Arranjo Ativo: {be.descricao()}")
        if dist:
            print("\n[E/S - DISTRIBUIÇÃO EM DISCO]")
            print("\n".join(dist))
        for e in eventos:
            print(f"[{self.etiqueta}] {e}")
        for d, inicio, trilhas, plano in planos:
            pre = f"[{self.etiqueta} - Disco {d.id}]" if multi else f"[{self.etiqueta}]"
            if len(trilhas) == 1:
                print(f"\n{pre} 1 requisição na trilha {trilhas[0]} | cabeça: trilha {inicio} → "
                      f"{plano.final} ({plano.total} trilhas)")
                continue
            print(f"\n{pre} Requisições de leitura/escrita pendentes nas trilhas: {trilhas}")
            print(f"Posição atual da cabeça de leitura: Trilha {inicio}\n")
            print("\n".join(formatar(plano)))
        if multi and len(planos) > 1:
            print(f"\nMovimento total do braço (todos os discos): {sum(p[3].total for p in planos)} trilhas")

    def _flush_ssd(self, ops, mostrar):
        be = self.backend
        eventos = list(be.eventos)
        be.limpar_pendencias()
        if not mostrar or not (ops or eventos):
            return
        self._cabecalho(ops)
        print(f"Dispositivo Ativo: {be.descricao()}")
        print(f"[{self.etiqueta} - SSD] SSD não tem braço: sem escalonamento de trilhas; "
              "acesso por páginas via FTL")
        for e in eventos:
            print(f"  {e}")
        print(f"[{self.etiqueta} - SSD] {be.resumo()}")

    # ------------------------------------------------------------ utilidades de RAID
    def teste_falha(self, bloco=None, disco=None):
        """Falha forçada + recuperação do bloco (Exemplo 2.2). Retorna True se recuperou."""
        be = self.backend
        if be.kind != "hdd":
            raise IOSimError("o teste de falha é para arranjos de discos (HDD/RAID)")
        self.flush(silencioso=True)
        if bloco is None:
            bloco = self.ultimo_escrito
        if bloco is None or bloco not in self._valid:
            raise IOSimError("nenhum bloco gravado para testar (use write_file antes ou --bloco N)")
        linhas, ok = be.teste_falha(bloco, disco)
        print("\n[TESTE DE FALHA SIMULADO]")
        print("\n".join(linhas))
        return ok
