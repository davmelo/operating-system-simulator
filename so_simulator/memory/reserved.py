"""Região Estática/Fixa da RAM: alocação CONTÍGUA, sem paginação (Kernel e processos críticos).

Mantém uma lista de segmentos (ocupados e lacunas) que cobre toda a área. Gera
fragmentação externa quando processos terminam e deixam lacunas; a compactação
junta os processos no início da área e devolve uma única lacuna grande.
"""
from dataclasses import dataclass

from .erros import FragmentacaoExterna, MemoriaError
from .util import KB, kb, mb, mb_total

POLITICAS = ("first", "best", "worst")
NOME_NUCLEO = "Núcleo Base do SO"


@dataclass
class Segmento:
    inicio: int                 # bytes desde o começo da RAM
    tamanho: int
    dono: str | None = None     # None = lacuna livre
    pid: int | None = None
    fixo: bool = False          # não pode ser movido na compactação (núcleo)

    @property
    def fim(self):
        return self.inicio + self.tamanho

    @property
    def livre(self):
        return self.dono is None


class ReservedArea:
    def __init__(self, total, nucleo, politica="first"):
        if nucleo >= total:
            raise MemoriaError("o núcleo base deve ser menor que a Área Reservada")
        self.total = total
        self.politica = "first"
        self.set_politica(politica)
        self.segmentos = [Segmento(0, nucleo, NOME_NUCLEO, 0, fixo=True),
                          Segmento(nucleo, total - nucleo)]

    def set_politica(self, politica):
        if politica not in POLITICAS:
            raise MemoriaError(f"política inválida: {politica} (use {', '.join(POLITICAS)})")
        self.politica = politica

    # ------------------------------------------------------------ consultas
    def livres(self):
        return [s for s in self.segmentos if s.livre]

    def total_livre(self):
        return sum(s.tamanho for s in self.livres())

    def maior_livre(self):
        return max((s.tamanho for s in self.livres()), default=0)

    def ocupado(self):
        return self.total - self.total_livre()

    def buscar(self, pid):
        return next((s for s in self.segmentos if s.pid == pid and not s.livre), None)

    @property
    def fragmentada(self):
        return len(self.livres()) > 1

    # ------------------------------------------------------------ alocação / liberação
    def alocar(self, nome, pid, tamanho):
        tamanho = -(-tamanho // KB) * KB                    # granularidade de 1 KB
        if tamanho <= 0:
            raise MemoriaError("tamanho do processo deve ser maior que zero")
        candidatos = [s for s in self.livres() if s.tamanho >= tamanho]
        if not candidatos:
            if self.total_livre() >= tamanho:
                raise FragmentacaoExterna(
                    f"[FALHA] Requisição contígua de {kb(tamanho)} sem bloco único: livre total "
                    f"{kb(self.total_livre())}, maior bloco {kb(self.maior_livre())} (fragmentação "
                    "externa). Execute 'memory_compact --reserved'.")
            raise MemoriaError(f"[FALHA] Área Reservada sem espaço: pedido de {kb(tamanho)}, "
                               f"livre {kb(self.total_livre())}.")
        if self.politica == "first":
            alvo = candidatos[0]
        elif self.politica == "best":
            alvo = min(candidatos, key=lambda s: (s.tamanho, s.inicio))
        else:
            alvo = max(candidatos, key=lambda s: (s.tamanho, -s.inicio))
        novo = Segmento(alvo.inicio, tamanho, nome, pid)
        resto = alvo.tamanho - tamanho
        i = self.segmentos.index(alvo)
        self.segmentos[i:i + 1] = [novo] + ([Segmento(novo.fim, resto)] if resto else [])
        return novo

    def liberar(self, pid):
        seg = self.buscar(pid)
        if seg is None:
            raise MemoriaError("processo não está na Área Reservada")
        info = Segmento(seg.inicio, seg.tamanho, seg.dono, seg.pid)   # cópia: a lacuna pode ser fundida
        seg.dono = seg.pid = None
        self._unir_lacunas()
        return info

    def _unir_lacunas(self):
        out = []
        for s in self.segmentos:
            if out and out[-1].livre and s.livre:
                out[-1].tamanho += s.tamanho
            else:
                out.append(s)
        self.segmentos = out

    # ------------------------------------------------------------ compactação
    def compactar(self):
        """Empurra os processos móveis para o início (depois do núcleo fixo).
        Retorna [(nome, inicio_antigo, inicio_novo, tamanho)] dos que foram movidos."""
        cursor, novos, movidos = 0, [], []
        for s in self.segmentos:
            if s.livre:
                continue
            if s.fixo:
                cursor = s.fim
            elif s.inicio != cursor:
                movidos.append((s.dono, s.inicio, cursor, s.tamanho))
                s.inicio = cursor
                cursor += s.tamanho
            else:
                cursor += s.tamanho
            novos.append(s)
        if cursor < self.total:
            novos.append(Segmento(cursor, self.total - cursor))
        self.segmentos = novos
        return movidos

    # ------------------------------------------------------------ relatórios
    def mapa_linhas(self):
        livres = self.livres()
        nomes, n_lac = [], 0
        for s in self.segmentos:
            if not s.livre:
                nomes.append(s.dono)
            elif len(livres) > 1:
                n_lac += 1
                nomes.append(f"FRAGMENTO LIVRE {n_lac}")
            else:
                nomes.append("Bloco Livre Contíguo")
        faixas = [f"[{mb(s.inicio)} - {mb(s.fim)}]" for s in self.segmentos]
        tams = [f"({kb(s.tamanho)})" for s in self.segmentos]
        wf, wn, wt = max(map(len, faixas)), max(20, *map(len, nomes)), max(map(len, tams))
        linhas = []
        for s, f, n, t in zip(self.segmentos, faixas, nomes, tams):
            estado = ("OCUPADO" if not s.livre else "LIVRE - LACUNA" if len(livres) > 1 else "LIVRE")
            linhas.append(f"{f:<{wf}} : {n:<{wn}} {t:<{wt}} [{estado}]")
        return linhas

    def diagnostico_linhas(self):
        livres = self.livres()
        total, maior = self.total_livre(), self.maior_livre()
        linhas = ["--- DIAGNÓSTICO DE FRAGMENTAÇÃO EXTERNA ---",
                  f"Total de Memória Livre na Área Reservada : {kb(total)} ({mb(total)[:-3]} MB)",
                  f"Maior Bloco Contíguo Disponível          : {kb(maior)} ({mb(maior)[:-3]} MB)"]
        if len(livres) > 1:
            linhas.append(f"Fragmentação Externa Detectada           : SIM ({len(livres)} fragmentos não contíguos)")
            falha = -(-((maior + total) // 2) // KB) * KB
            linhas.append(f"Status: Uma requisição contígua de {kb(falha)} falhará por ausência de bloco único.")
        else:
            situacao = "área totalmente ocupada" if not livres else "1 bloco livre contíguo"
            linhas.append(f"Fragmentação Externa Detectada           : NÃO ({situacao})")
            linhas.append("Status: Área Reservada cheia: nenhuma nova requisição pode ser atendida."
                          if not livres else
                          f"Status: Qualquer requisição contígua de até {kb(maior)} pode ser atendida.")
        return linhas

    def titulo_mapa(self):
        return f"--- MAPA DA ÁREA CRÍTICA/RESERVADA DA RAM (Total: {mb_total(self.total)} - Alocação Fixa) ---"
