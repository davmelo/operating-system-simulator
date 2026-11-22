"""Algoritmos de substituição de páginas: FIFO, LRU, Segunda Chance, LFU e NRU.

Cada algoritmo recebe a tabela de molduras (todas ocupadas) e devolve a moldura vítima
junto com as linhas que explicam a decisão (a execução é explícita, como pede o enunciado).
"""
from dataclasses import dataclass


class Frame:
    """Moldura física da RAM dinâmica, com os bits e contadores que os algoritmos usam."""

    def __init__(self, idx):
        self.idx = idx
        self.limpar()

    def limpar(self):
        self.pid = None
        self.pagina = None
        self.ordem = 0              # ordem de chegada (FIFO / Segunda Chance)
        self.carregado_em = 0       # ms
        self.ultimo_acesso = 0      # ms (LRU)
        self.ref = False            # bit R (referenciada)
        self.sujo = False           # bit M (modificada)
        self.contagem = 0           # nº de acessos (LFU)
        self.dados = None

    @property
    def ocupada(self):
        return self.pagina is not None


def desc(f):
    return f"Página Lógica {f.pagina} do PID {f.pid}"


class Substituidor:
    nome = ""
    longo = ""

    def ao_acessar(self, frames, agora):
        """Gancho chamado a cada referência (antes de marcar o bit R)."""

    def escolher(self, frames, agora):
        raise NotImplementedError


class FIFO(Substituidor):
    nome, longo = "FIFO", "FIFO (First-In, First-Out)"

    def escolher(self, frames, agora):
        v = min(frames, key=lambda f: (f.ordem, f.idx))
        return v, [f"Fila de chegada: {desc(v)} está na RAM há {agora - v.carregado_em}ms (a primeira a entrar)."]


class LRU(Substituidor):
    nome, longo = "LRU", "LRU (Least Recently Used)"

    def escolher(self, frames, agora):
        v = min(frames, key=lambda f: (f.ultimo_acesso, f.idx))
        return v, [f"Analisando histórico de acesso: {desc(v)} não é usada há "
                   f"{agora - v.ultimo_acesso}ms (Mais antiga)."]


class SegundaChance(Substituidor):
    nome, longo = "Segunda Chance", "Segunda Chance (FIFO com bit de referência)"

    def escolher(self, frames, agora):
        linhas = []
        while True:
            f = min(frames, key=lambda x: (x.ordem, x.idx))
            if f.ref:
                f.ref = False
                f.ordem = max(x.ordem for x in frames) + 1      # volta para o fim da fila
                linhas.append(f"Moldura {f.idx} ({desc(f)}): bit R=1 -> ganha segunda chance "
                              "(R := 0, vai para o fim da fila).")
            else:
                linhas.append(f"Moldura {f.idx} ({desc(f)}): bit R=0 -> escolhida como vítima.")
                return f, linhas


class LFU(Substituidor):
    nome, longo = "LFU", "LFU (Least Frequently Used)"

    def escolher(self, frames, agora):
        v = min(frames, key=lambda f: (f.contagem, f.ordem, f.idx))
        return v, [f"Menos frequentemente usada: {desc(v)} com {v.contagem} acesso(s)."]


class NRU(Substituidor):
    nome, longo = "NRU", "NRU (Not Recently Used)"
    INTERVALO = 4                    # a cada 4 referências o "relógio" zera os bits R

    def __init__(self):
        self.n = 0

    def ao_acessar(self, frames, agora):
        self.n += 1
        if self.n % self.INTERVALO == 0:
            for f in frames:
                f.ref = False

    def escolher(self, frames, agora):
        def classe(f):
            return 2 * f.ref + f.sujo
        v = min(frames, key=lambda f: (classe(f), f.ordem, f.idx))
        c = classe(v)
        return v, [f"Classe NRU {c} (R={int(v.ref)}, M={int(v.sujo)}): {desc(v)} é a de menor classe."]


ALGORITMOS = {"fifo": FIFO, "lru": LRU, "second_chance": SegundaChance, "lfu": LFU, "nru": NRU}


def criar_substituidor(nome):
    from .erros import MemoriaError
    chave = nome.lower().replace("-", "_")
    if chave not in ALGORITMOS:
        raise MemoriaError(f"algoritmo inválido: {nome} (use {', '.join(ALGORITMOS)})")
    return ALGORITMOS[chave]()


# ---------------------------------------------------------------- simulação com cadeia de referências
@dataclass
class Resultado:
    algo: str
    faltas: int
    passos: list          # [(ref, [páginas nas molduras], falta?, vítima)]


def simular(refs, n_frames, algo):
    """Roda uma cadeia de referências (um único processo) e conta os page faults.
    `algo` pode ser qualquer um de ALGORITMOS ou 'optimal' (Belady: conhece o futuro)."""
    frames = [Frame(i) for i in range(n_frames)]
    sub = None if algo == "optimal" else criar_substituidor(algo)
    faltas, ordem, passos = 0, 0, []
    for i, ref in enumerate(refs):
        agora = i + 1
        f = next((x for x in frames if x.pagina == ref), None)
        vitima, falta = None, f is None
        if falta:
            faltas += 1
            f = next((x for x in frames if not x.ocupada), None)
            if f is None:
                if algo == "optimal":
                    def proximo_uso(x):
                        futuro = refs[i + 1:]
                        return futuro.index(x.pagina) if x.pagina in futuro else len(refs) + 1
                    f = max(frames, key=lambda x: (proximo_uso(x), -x.idx))
                else:
                    f, _ = sub.escolher(frames, agora)
                vitima = f.pagina
            ordem += 1
            f.limpar()
            f.pid, f.pagina, f.ordem, f.carregado_em = 1, ref, ordem, agora
        if sub:
            sub.ao_acessar(frames, agora)
        f.ultimo_acesso, f.ref = agora, True
        f.contagem += 1
        passos.append((ref, [x.pagina for x in frames], falta, vitima))
    return Resultado(algo, faltas, passos)
