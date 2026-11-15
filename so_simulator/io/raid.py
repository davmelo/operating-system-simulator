"""Abstração de armazenamento em arranjos de discos: disco único, RAID 0, 1, 4 e 5.

O Sistema de Arquivos enxerga apenas blocos LÓGICOS 0..capacidade-1. Aqui eles são
traduzidos para (disco, bloco físico). Cada "faixa" (stripe) é o conjunto de blocos
de mesmo número físico nos vários discos; no RAID 4/5, uma faixa tem n-1 blocos de
dados + 1 bloco de paridade (XOR dos dados).
"""
import math
from contextlib import contextmanager

from ..fs.util import fmt
from .erros import ArranjoInoperante, IOSimError
from .physical_disk import PhysicalDisk

MIN_DISCOS = {"single": 1, "0": 2, "1": 2, "4": 3, "5": 3}


def xor_blocks(*blocks):
    """XOR byte a byte de blocos do mesmo tamanho."""
    tamanho = len(blocks[0])
    acc = 0
    for b in blocks:
        acc ^= int.from_bytes(b, "big")
    return acc.to_bytes(tamanho, "big")


def _lista_discos(idxs):
    return " XOR ".join(f"Disco {k + 1}" for k in idxs)


class RAIDArray:
    kind = "hdd"

    def __init__(self, nivel="single", n_discos=None, block_size=4096, capacidade=16,
                 tracks=200, head=53):
        nivel = "single" if str(nivel) in ("none", "single") else str(nivel)
        if nivel not in MIN_DISCOS:
            raise IOSimError(f"nível de RAID inválido: {nivel} (use 0, 1, 4 ou 5)")
        n_discos = MIN_DISCOS[nivel] if n_discos is None else n_discos
        if nivel == "single" and n_discos != 1:
            raise IOSimError("disco único usa exatamente 1 disco (use --raid 0/1/4/5 para arranjos)")
        if n_discos < MIN_DISCOS[nivel]:
            raise IOSimError(f"RAID {nivel} precisa de pelo menos {MIN_DISCOS[nivel]} discos")
        self.nivel, self.n = nivel, n_discos
        self.block_size, self.capacidade, self.tracks = block_size, capacidade, tracks
        self.dados_por_faixa = {"single": 1, "0": n_discos, "1": 1}.get(nivel, n_discos - 1)
        self.tolerancia = {"single": 0, "0": 0, "1": n_discos - 1}.get(nivel, 1)
        self.n_faixas = math.ceil(capacidade / self.dados_por_faixa)
        self.disks = [PhysicalDisk(i + 1, tracks, block_size, self.n_faixas, head)
                      for i in range(n_discos)]
        self.distribuicao = []       # linhas "[E/S - DISTRIBUIÇÃO EM DISCO]" da última escrita
        self.eventos = []            # outros avisos (leitura degradada etc.)

    # ------------------------------------------------------------ descrição
    @property
    def nome(self):
        return "Disco único (sem RAID)" if self.nivel == "single" else f"RAID {self.nivel}"

    def descricao(self):
        if self.nivel == "single":
            return f"Disco único, sem RAID (Blocos de {fmt(self.block_size)})"
        return f"{self.nome} ({self.n} Discos Físicos, Blocos de {fmt(self.block_size)})"

    @property
    def capacidade_bruta(self):
        return self.n_faixas * self.n * self.block_size

    # ------------------------------------------------------------ mapeamento
    def disco_paridade(self, faixa):
        if self.nivel == "4":
            return self.n - 1                       # RAID 4: paridade sempre no último disco
        if self.nivel == "5":
            return (self.n - 1 - faixa) % self.n    # RAID 5: paridade rotativa
        return None

    def discos_dados(self, faixa):
        """Índices (0-based) dos discos com dados da faixa, na ordem lógica."""
        if self.nivel in ("single", "1"):
            return [0]
        p = self.disco_paridade(faixa)
        return [k for k in range(self.n) if k != p]

    def localizar(self, l):
        """Bloco lógico → (faixa, posição na faixa, índice do disco)."""
        faixa, j = divmod(l, self.dados_por_faixa)
        return faixa, j, self.discos_dados(faixa)[j]

    def _lote(self, faixa, j):
        return faixa * self.dados_por_faixa + j

    # ------------------------------------------------------------ estado
    def falhos(self):
        return [d for d in self.disks if d.failed]

    def vivos(self):
        return [d for d in self.disks if not d.failed]

    @property
    def degradado(self):
        return bool(self.falhos())

    @contextmanager
    def silencio(self):
        """Acessos dentro do bloco não geram requisições para o escalonador."""
        antes = [d.registrar for d in self.disks]
        for d in self.disks:
            d.registrar = False
        try:
            yield
        finally:
            for d, a in zip(self.disks, antes):
                d.registrar = a

    def limpar_pendencias(self):
        for d in self.disks:
            d.pending.clear()
        self.distribuicao.clear()
        self.eventos.clear()

    def reiniciar_estatisticas(self):
        self.limpar_pendencias()
        for d in self.disks:
            d.leituras = d.escritas = d.movimento = 0

    def trim(self, l):
        """Liberar um bloco lógico não mexe nos discos (só a metadados do FS)."""

    # ------------------------------------------------------------ leitura
    def reconstruir(self, faixa, idx):
        """Reconstrói o bloco do disco `idx` na faixa: XOR de todos os outros discos."""
        outros = [k for k in range(self.n) if k != idx]
        if any(self.disks[k].failed for k in outros):
            raise ArranjoInoperante(f"{self.nome}: mais de um disco falho na faixa {faixa}; "
                                    "não é possível reconstruir")
        return xor_blocks(*(self.disks[k].read(faixa) for k in outros)), outros

    def read(self, l):
        faixa, j, idx = self.localizar(l)
        if self.nivel == "1":
            vivos = self.vivos()
            if not vivos:
                raise ArranjoInoperante("RAID 1: todos os discos falharam")
            alvo = min(vivos, key=lambda d: (abs(d.head - d.track_of(faixa)), d.id))
            if self.disks[0].failed:
                self.eventos.append(f"Leitura degradada: Bloco {l:02d} lido do espelho (Disco {alvo.id})")
            return alvo.read(faixa)
        disco = self.disks[idx]
        if not disco.failed:
            return disco.read(faixa)
        if self.nivel in ("single", "0"):
            raise ArranjoInoperante(f"Disco {disco.id} falhou e {self.nome} não tem redundância: "
                                    f"o bloco {l} foi perdido")
        dado, outros = self.reconstruir(faixa, idx)
        self.eventos.append(f"Leitura degradada: Bloco {l:02d} (Disco {disco.id} falho) "
                            f"reconstruído via XOR: {_lista_discos(outros)}")
        return dado

    # ------------------------------------------------------------ escrita
    def _linha_dado(self, idx, l, rotulo, extra=""):
        return f"Disco {idx + 1}: [Bloco {l:02d}: {rotulo(l)} ({fmt(self.block_size)})]{extra}"

    def write_batch(self, blocos, rotulo=lambda b: f"Bloco lógico {b}"):
        """Grava vários blocos lógicos de uma vez ({bloco: bytes}).
        Em lote dá para detectar faixas completas no RAID 4/5 (escrita sem leituras)."""
        mostrar = self.n > 1
        if self.nivel in ("single", "0"):
            for l, dados in blocos.items():
                faixa, _, idx = self.localizar(l)
                disco = self.disks[idx]
                if disco.failed:
                    raise ArranjoInoperante(f"Disco {disco.id} falhou e {self.nome} não tem "
                                            f"redundância: não dá para gravar o bloco {l}")
                disco.write(faixa, dados)
                if mostrar:
                    self.distribuicao.append(self._linha_dado(idx, l, rotulo))
        elif self.nivel == "1":
            for l, dados in blocos.items():
                if not self.vivos():
                    raise ArranjoInoperante("RAID 1: todos os discos falharam")
                for d in self.disks:
                    if d.failed:
                        self.distribuicao.append(f"Disco {d.id}: FALHO — gravação do Bloco {l:02d} omitida")
                    else:
                        d.write(l, dados)
                        self.distribuicao.append(self._linha_dado(d.id - 1, l, rotulo, " (espelho)"))
        else:
            por_faixa = {}
            for l, dados in blocos.items():
                faixa, j, _ = self.localizar(l)
                por_faixa.setdefault(faixa, {})[j] = dados
            for faixa in sorted(por_faixa):
                if len(por_faixa) > 1:
                    self.distribuicao.append(f"Faixa {faixa}:")
                self._escrever_faixa(faixa, por_faixa[faixa], rotulo)

    def _escrever_faixa(self, faixa, novos, rotulo):
        if len(self.falhos()) > 1:
            raise ArranjoInoperante(f"{self.nome}: mais de um disco falho; arranjo inoperante")
        p = self.disco_paridade(faixa)
        dd = self.discos_dados(faixa)
        d = len(dd)
        disco_p = self.disks[p]
        linhas = {}

        def grava_dados():
            for j, dados in novos.items():
                disco = self.disks[dd[j]]
                l = self._lote(faixa, j)
                if disco.failed:
                    linhas[dd[j]] = self._linha_dado(dd[j], l, rotulo,
                                                     " — DISCO FALHO: gravação omitida (dado fica na paridade)")
                else:
                    disco.write(faixa, dados)
                    linhas[dd[j]] = self._linha_dado(dd[j], l, rotulo)

        if len(novos) == d:
            # Faixa completa: paridade = XOR dos dados novos, sem nenhuma leitura.
            paridade = xor_blocks(*(novos[j] for j in range(d)))
            grava_dados()
            rotulo_xor = ", ".join(f"Bloco {self._lote(faixa, j):02d}" for j in range(d))
            if disco_p.failed:
                linhas[p] = f"Disco {p + 1}: [PARIDADE : indisponível — DISCO FALHO (refeita no rebuild)]"
            else:
                disco_p.write(faixa, paridade)
                linhas[p] = f"Disco {p + 1}: [PARIDADE : XOR({rotulo_xor})]"
        elif not disco_p.failed and all(not self.disks[dd[j]].failed for j in novos):
            # Escrita parcial saudável: read-modify-write  P' = P xor D_antigo xor D_novo
            paridade = disco_p.read(faixa)
            for j, dados in novos.items():
                paridade = xor_blocks(paridade, self.disks[dd[j]].read(faixa), dados)
            grava_dados()
            disco_p.write(faixa, paridade)
            n_io = len(novos) + 1
            linhas[p] = (f"Disco {p + 1}: [PARIDADE : P' = P XOR D_antigo XOR D_novo "
                         f"(read-modify-write: {n_io} leituras + {n_io} escritas)]")
        elif disco_p.failed:
            # Sem paridade disponível: só os dados são gravados.
            grava_dados()
            linhas[p] = f"Disco {p + 1}: [PARIDADE : indisponível — DISCO FALHO (refeita no rebuild)]"
        else:
            # Um disco de dados envolvido falhou: a paridade passa a carregar o dado dele.
            atual = []
            for j in range(d):
                atual.append(novos[j] if j in novos else self.disks[dd[j]].read(faixa))
            paridade = xor_blocks(*atual)
            grava_dados()
            disco_p.write(faixa, paridade)
            linhas[p] = f"Disco {p + 1}: [PARIDADE : XOR(todos os dados da faixa) — inclui o dado do disco falho]"
        self.distribuicao += [linhas[k] for k in sorted(linhas)]

    # ------------------------------------------------------------ falha / recuperação
    def _disco(self, disco_id):
        if not 1 <= disco_id <= self.n:
            raise IOSimError(f"disco inválido: {disco_id} (o arranjo tem {self.n})")
        return self.disks[disco_id - 1]

    def fail(self, disco_id):
        disco = self._disco(disco_id)
        if disco.failed:
            raise IOSimError(f"Disco {disco_id} já está em falha")
        disco.fail()
        livres = self.tolerancia - len(self.falhos())
        if livres < 0:
            estado = "ARRANJO INOPERANTE (falhas acima da tolerância)"
        elif self.nivel in ("4", "5"):
            estado = "DEGRADADO (leituras desse disco serão reconstruídas via XOR)"
        elif self.nivel == "1":
            estado = f"DEGRADADO (dados nos espelhos restantes; tolera mais {livres} falha(s))"
        else:
            estado = "SEM REDUNDÂNCIA: os dados desse disco foram perdidos"
        return [f"Falha forçada no Disco {disco_id}! Conteúdo do disco perdido.",
                f"Estado do arranjo: {estado}"]

    def rebuild(self, disco_id):
        disco = self._disco(disco_id)
        if not disco.failed:
            raise IOSimError(f"Disco {disco_id} não está em falha")
        idx = disco_id - 1
        if self.nivel in ("4", "5") and any(k.failed for k in self.disks if k is not disco):
            raise ArranjoInoperante("há outro disco falho: reconstrução impossível")
        if self.nivel == "1" and not [k for k in self.disks if not k.failed]:
            raise ArranjoInoperante("nenhum espelho sobrevivente: dados perdidos")
        disco.replace()
        if self.nivel in ("single", "0"):
            return [f"Disco {disco_id} substituído por um disco novo e VAZIO.",
                    f"{self.nome} não tem redundância: os dados que estavam nele não voltam."]
        linhas = [f"Disco {disco_id} substituído; reconstruindo {self.n_faixas} faixas..."]
        for faixa in range(self.n_faixas):
            if self.nivel == "1":
                origem = min((k for k in self.disks if k is not disco and not k.failed),
                             key=lambda k: (abs(k.head - k.track_of(faixa)), k.id))
                disco.write(faixa, origem.read(faixa))
                linhas.append(f"Faixa {faixa}: copiada do Disco {origem.id} [OK]")
            else:
                dado, outros = self.reconstruir(faixa, idx)
                disco.write(faixa, dado)
                papel = "paridade" if idx == self.disco_paridade(faixa) else "dados"
                linhas.append(f"Faixa {faixa}: {papel} do Disco {disco_id} = {_lista_discos(outros)} [OK]")
        linhas.append(f"Reconstrução do Disco {disco_id} concluída.")
        return linhas

    def verificar(self):
        """Confere a consistência: paridade (RAID 4/5) ou espelhos iguais (RAID 1).
        Retorna (ok, linhas)."""
        if self.nivel in ("single", "0"):
            return True, [f"{self.nome}: sem paridade/espelho para verificar."]
        if self.degradado:
            return False, ["Arranjo degradado: rode raid_rebuild antes de verificar."]
        ruins = []
        for faixa in range(self.n_faixas):
            blocos = [d.peek(faixa) for d in self.disks]
            if self.nivel == "1":
                ok = all(b == blocos[0] for b in blocos)
            else:
                ok = not any(xor_blocks(*blocos))
            if not ok:
                ruins.append(faixa)
        if ruins:
            return False, [f"INCONSISTENTE nas faixas: {ruins}"]
        what = "espelhos idênticos" if self.nivel == "1" else "XOR de cada faixa = 0 (paridade correta)"
        return True, [f"{self.n_faixas} faixas verificadas: {what} [OK]"]

    def teste_falha(self, l, disco_id=None):
        """Falha FORÇADA e temporária (não apaga dados) para mostrar a recuperação do bloco `l`.
        Retorna (linhas, ok)."""
        if self.degradado:
            raise IOSimError("arranjo já degradado: rode raid_rebuild antes do teste")
        faixa, _, idx = self.localizar(l)
        alvo = (disco_id - 1) if disco_id else idx
        disco = self._disco(alvo + 1)
        linhas = [f"* Falha forçada no Disco {disco.id}!"]
        ok = True
        disco.failed = True
        try:
            with self.silencio():
                if self.nivel in ("single", "0"):
                    ok = alvo != idx
                    if ok:
                        linhas.append(f"* Bloco {l:02d} está no Disco {idx + 1}, não afetado -> leitura normal [OK]")
                    else:
                        linhas.append(f"* {self.nome} não tem redundância: Bloco {l:02d} perdido -> Reconstrução [FALHA]")
                elif self.nivel == "1":
                    esperado = self.disks[1 if alvo == 0 else 0].peek(faixa)
                    outro = self.read(l)
                    ok = outro == esperado
                    origem = next(k.id for k in self.vivos())
                    linhas.append(f"* Recuperando Bloco {l:02d} via espelho (Disco {origem}) "
                                  f"-> Reconstrução [{'OK' if ok else 'FALHA'}]")
                else:
                    esperado = self.disks[idx].peek(faixa)
                    if alvo == idx:
                        dado, outros = self.reconstruir(faixa, idx)
                        ok = dado == esperado
                        linhas.append(f"* Recuperando Bloco {l:02d} via operação XOR: ({_lista_discos(outros)}) "
                                      f"-> Reconstrução [{'OK' if ok else 'FALHA'}]")
                    else:
                        ok = self.disks[idx].read(faixa) == esperado
                        papel = "de paridade" if alvo == self.disco_paridade(faixa) else "de outro dado"
                        linhas.append(f"* O Disco {disco.id} guarda o bloco {papel}; Bloco {l:02d} segue no "
                                      f"Disco {idx + 1} -> leitura normal [OK]")
        finally:
            disco.failed = False
        return linhas, ok

    # ------------------------------------------------------------ relatórios
    def layout(self):
        """Matriz faixa × disco para o raid_status: 'L02' = bloco lógico, 'P' = paridade."""
        linhas = []
        for faixa in range(self.n_faixas):
            p = self.disco_paridade(faixa)
            celulas = []
            if self.nivel == "1":
                celulas = [f"L{faixa:02d}" if faixa < self.capacidade else "-"] * self.n
            else:
                dd = self.discos_dados(faixa)
                for k in range(self.n):
                    if k == p:
                        celulas.append("P")
                    else:
                        l = self._lote(faixa, dd.index(k)) if self.nivel != "single" else faixa
                        celulas.append(f"L{l:02d}" if l < self.capacidade else "-")
            linhas.append((faixa, celulas))
        return linhas
