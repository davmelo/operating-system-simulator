"""SSD simulado: acesso por páginas, escrita fora-do-lugar (FTL), coleta de lixo e Wear Leveling.

Diferenças em relação ao HDD:
  * não há braço/trilhas → nada a escalonar; o acesso é direto por página;
  * só se pode escrever em página LIVRE; para reaproveitar é preciso APAGAR o bloco
    inteiro (vários páginas de uma vez), e cada bloco aguenta um nº limitado de apagamentos;
  * por isso a FTL (Flash Translation Layer) mantém um mapa página lógica → física,
    grava sempre em página nova (a antiga fica INVÁLIDA) e a coleta de lixo (GC)
    recupera blocos cheios de páginas inválidas;
  * o Wear Leveling distribui os apagamentos para nenhum bloco morrer antes dos outros.
"""
import math
import random

from ..fs.util import fmt
from .erros import IOSimError

LIVRE, VALIDA, INVALIDA = "L", "V", "I"
MODOS_WL = ("none", "dynamic", "static")


class SSD:
    kind = "ssd"

    def __init__(self, capacidade, block_size=4096, paginas_por_bloco=4, blocos=None,
                 wear_leveling="static", limiar=4):
        if wear_leveling not in MODOS_WL:
            raise IOSimError(f"wear leveling inválido: {wear_leveling} (use {', '.join(MODOS_WL)})")
        if paginas_por_bloco < 1:
            raise IOSimError("páginas por bloco deve ser >= 1")
        self.capacidade = capacidade            # páginas lógicas (= blocos do FS)
        self.block_size = block_size
        self.ppb = paginas_por_bloco
        minimo = math.ceil(capacidade / self.ppb) + 2
        self.n_blocos = max(blocos or 2 * math.ceil(capacidade / self.ppb), minimo)
        self.wl = wear_leveling
        self.limiar = limiar                    # diferença máx-mín de apagamentos tolerada (WL estático)

        n, p = self.n_blocos, self.ppb
        self.estado = [[LIVRE] * p for _ in range(n)]
        self.dados = [[None] * p for _ in range(n)]
        self.dono = {}                          # (bloco, página) -> página lógica
        self.mapa = {}                          # página lógica -> (bloco, página)  [a FTL]
        self.apagamentos = [0] * n
        self.livres = list(range(n))            # blocos apagados, prontos para uso
        self.ativo, self.ponteiro = None, 0     # bloco que está sendo preenchido
        self._em_gc = False
        self.eventos = []
        self.distribuicao = []
        self.reiniciar_estatisticas()

    # ------------------------------------------------------------ estatísticas
    def reiniciar_estatisticas(self):
        self.escritas_logicas = self.escritas_fisicas = self.leituras = 0
        self.gcs = self.migracoes_wl = 0

    @property
    def total_apagamentos(self):
        return sum(self.apagamentos)

    @property
    def amplificacao(self):
        return self.escritas_fisicas / self.escritas_logicas if self.escritas_logicas else 0.0

    def descricao(self):
        return (f"SSD ({self.n_blocos} blocos × {self.ppb} páginas de {fmt(self.block_size)}; "
                f"{self.capacidade} páginas lógicas; Wear Leveling: {self.wl})")

    def resumo(self):
        return (f"Escritas lógicas: {self.escritas_logicas} | físicas: {self.escritas_fisicas} "
                f"(amplificação {self.amplificacao:.2f}x) | GCs: {self.gcs} | "
                f"apagamentos: mín {min(self.apagamentos)} / máx {max(self.apagamentos)}")

    def limpar_pendencias(self):
        self.eventos.clear()
        self.distribuicao.clear()

    # ------------------------------------------------------------ política de alocação
    def _escolher_livre(self):
        if self.wl == "none":
            return min(self.livres)                         # primeiro bloco livre
        return min(self.livres, key=lambda b: (self.apagamentos[b], b))   # o MENOS desgastado

    def _alocar_pagina(self):
        while self.ativo is None or self.ponteiro == self.ppb:
            if self.ativo is not None:
                self.ativo = None                           # bloco cheio: fica "selado"
            if self._em_gc or len(self.livres) >= 2:        # sempre sobra 1 bloco livre p/ o GC
                b = self._escolher_livre()
                self.livres.remove(b)
                self.ativo, self.ponteiro = b, 0
            else:
                self._coletar_lixo()
        pag = (self.ativo, self.ponteiro)
        self.ponteiro += 1
        return pag

    def _invalidar(self, pag):
        b, p = pag
        self.estado[b][p] = INVALIDA
        self.dados[b][p] = None
        self.dono.pop(pag, None)

    def _programar(self, pag, lpagina, dados):
        b, p = pag
        self.estado[b][p] = VALIDA
        self.dados[b][p] = dados
        self.dono[pag] = lpagina
        self.mapa[lpagina] = pag
        self.escritas_fisicas += 1

    def _n_invalidas(self, b):
        return self.estado[b].count(INVALIDA)

    def _apagar(self, b):
        self.estado[b] = [LIVRE] * self.ppb
        self.dados[b] = [None] * self.ppb
        self.apagamentos[b] += 1
        self.livres.append(b)

    # ------------------------------------------------------------ coleta de lixo
    def _coletar_lixo(self):
        candidatos = [b for b in range(self.n_blocos) if b not in self.livres and b != self.ativo]
        if not candidatos:
            raise IOSimError("SSD sem blocos para coletar")
        penal = (lambda b: -self.apagamentos[b]) if self.wl != "none" else (lambda b: 0)
        vitima = max(candidatos, key=lambda b: (self._n_invalidas(b), penal(b), -b))
        invalidas = self._n_invalidas(vitima)
        if invalidas == 0:
            raise IOSimError("SSD cheio: nenhuma página inválida para recuperar")
        copiadas = 0
        self._em_gc = True
        try:
            for p in range(self.ppb):
                if self.estado[vitima][p] == VALIDA:
                    lp = self.dono[(vitima, p)]
                    dados = self.dados[vitima][p]
                    nova = self._alocar_pagina()
                    self._programar(nova, lp, dados)
                    copiadas += 1
        finally:
            self._em_gc = False
        self._apagar(vitima)
        self.gcs += 1
        self.eventos.append(f"[GC] Bloco {vitima}: {invalidas} página(s) inválida(s), {copiadas} válida(s) "
                            f"copiada(s) → bloco apagado (apagamentos do Bloco {vitima}: {self.apagamentos[vitima]})")
        self._wear_leveling_estatico()

    def _wear_leveling_estatico(self):
        """Dados 'frios' (nunca reescritos) prendem blocos pouco desgastados. Quando a
        diferença de desgaste passa do limiar, move-os para um bloco muito desgastado,
        devolvendo o bloco novo ao ciclo de escritas."""
        if self.wl != "static":
            return
        if max(self.apagamentos) - min(self.apagamentos) <= self.limiar:
            return
        frios = [b for b in range(self.n_blocos) if b not in self.livres and b != self.ativo
                 and all(s == VALIDA for s in self.estado[b])]
        if not frios or not self.livres:
            return
        frio = min(frios, key=lambda b: (self.apagamentos[b], b))
        destino = max(self.livres, key=lambda b: (self.apagamentos[b], -b))
        if self.apagamentos[destino] - self.apagamentos[frio] <= self.limiar:
            return
        self.livres.remove(destino)
        for p in range(self.ppb):
            lp = self.dono[(frio, p)]
            self._programar((destino, p), lp, self.dados[frio][p])
        self._apagar(frio)
        self.migracoes_wl += 1
        self.eventos.append(f"[Wear Leveling estático] dados frios do Bloco {frio} "
                            f"({self.apagamentos[frio] - 1} apagamentos) migrados para o Bloco {destino} "
                            f"({self.apagamentos[destino]} apagamentos); Bloco {frio} volta ao uso")

    # ------------------------------------------------------------ interface de blocos
    def _check(self, l):
        if not 0 <= l < self.capacidade:
            raise IOSimError(f"página lógica {l} fora do SSD")

    def write(self, l, dados, rotulo=None):
        self._check(l)
        self.escritas_logicas += 1
        pag = self._alocar_pagina()                 # pode disparar GC (que pode mover a página antiga)
        antiga = self.mapa.get(l)
        if antiga is not None:
            self._invalidar(antiga)
        self._programar(pag, l, bytes(dados).ljust(self.block_size, b"\0"))
        nota = f" [{rotulo}]" if rotulo else ""
        inval = f"; página antiga {antiga[0]}:{antiga[1]} invalidada" if antiga is not None else ""
        self.eventos.append(f"Escrita: página lógica {l:02d}{nota} → bloco físico {pag[0]}, "
                            f"página {pag[1]} (fora-do-lugar{inval})")

    def write_batch(self, blocos, rotulo=lambda b: None):
        for l, dados in blocos.items():
            self.write(l, dados, rotulo(l))

    def read(self, l):
        self._check(l)
        pag = self.mapa.get(l)
        if pag is None:
            return bytes(self.block_size)
        self.leituras += 1
        self.eventos.append(f"Leitura: página lógica {l:02d} ← bloco físico {pag[0]}, página {pag[1]} "
                            "(acesso direto, sem deslocamento de braço)")
        return self.dados[pag[0]][pag[1]]

    def trim(self, l):
        """O FS liberou a página: a FTL a invalida (comando TRIM) e o GC passa a poder usá-la."""
        pag = self.mapa.pop(l, None)
        if pag is not None:
            self._invalidar(pag)
            self.eventos.append(f"TRIM: página lógica {l:02d} liberada (bloco {pag[0]}, "
                                f"página {pag[1]} → inválida)")

    # ------------------------------------------------------------ relatórios
    def mapa_visual(self):
        """Uma linha por bloco: estado das páginas (V/I/L) e desgaste."""
        maximo = max(self.apagamentos) or 1
        linhas = []
        for b in range(self.n_blocos):
            paginas = "".join(self.estado[b])
            marca = " ← ativo" if b == self.ativo else (" (livre)" if b in self.livres else "")
            barra = "█" * math.ceil(20 * self.apagamentos[b] / maximo) if self.apagamentos[b] else ""
            linhas.append(f"Bloco {b:02d} [{paginas}] apagamentos: {self.apagamentos[b]:>4} {barra}{marca}")
        return linhas


# ---------------------------------------------------------------- demonstração
def _carga(ssd, frias, quentes, n_escritas, semente=42):
    rng = random.Random(semente)
    for l in range(frias):                                   # dados frios: gravados 1 vez
        ssd.write(l, f"frio {l}".encode())
    for _ in range(n_escritas):                              # dados quentes: reescritos sem parar
        l = frias + rng.randrange(quentes)
        ssd.write(l, b"quente")


def demonstracao(n_escritas=2000, capacidade=16, paginas_por_bloco=4, saida=print):
    """Compara os três modos de Wear Leveling com a mesma carga de trabalho."""
    quentes = max(2, capacidade // 4)
    frias = capacidade - quentes
    saida("--- DEMONSTRAÇÃO: WEAR LEVELING (SSD) ---")
    saida(f"Carga: {frias} páginas frias gravadas 1 vez + {quentes} páginas quentes "
          f"reescritas {n_escritas} vezes (mesma sequência nos 3 modos).")
    rotulos = {"none": "sem Wear Leveling", "dynamic": "WL dinâmico",
               "static": "WL dinâmico + estático"}
    resultados = {}
    for modo in MODOS_WL:
        ssd = SSD(capacidade, paginas_por_bloco=paginas_por_bloco, wear_leveling=modo)
        _carga(ssd, frias, quentes, n_escritas)
        resultados[modo] = ssd
    n_blocos = resultados["none"].n_blocos
    saida(f"\nApagamentos por bloco ({n_blocos} blocos):")
    saida(f"{'Modo':<24}" + "".join(f"B{b:<4}" for b in range(n_blocos)) + " mín  máx  máx-mín")
    for modo, ssd in resultados.items():
        a = ssd.apagamentos
        saida(f"{rotulos[modo]:<24}" + "".join(f"{x:<5}" for x in a)
              + f" {min(a):<4} {max(a):<4} {max(a) - min(a)}")
    saida("\nIndicadores:")
    saida(f"{'Modo':<24}{'apagam.':<9}{'GCs':<7}{'migr. WL':<10}{'amplif. de escrita':<20}")
    for modo, ssd in resultados.items():
        saida(f"{rotulos[modo]:<24}{ssd.total_apagamentos:<9}{ssd.gcs:<7}{ssd.migracoes_wl:<10}"
              f"{ssd.amplificacao:.2f}x")
    limite = 3000
    saida(f"\nVida útil estimada (bloco mais gasto atingindo {limite} apagamentos):")
    for modo, ssd in resultados.items():
        vida = int(n_escritas * limite / max(max(ssd.apagamentos), 1))
        saida(f"  {rotulos[modo]:<24}≈ {vida:,} escritas lógicas".replace(",", "."))
    saida("O desgaste uniforme faz o disco durar mais: o 1º bloco a morrer define a vida do SSD.")
    return resultados
