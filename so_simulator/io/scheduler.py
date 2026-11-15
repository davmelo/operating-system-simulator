"""Escalonamento do braço de disco: FCFS, SSTF, SCAN, C-SCAN (e as variantes LOOK/C-LOOK).

Todas as funções recebem a posição da cabeça e a lista de trilhas requisitadas e
devolvem um Plano: o percurso da cabeça, passo a passo.
"""
from dataclasses import dataclass

NOMES = {
    "fcfs": "FCFS (First-Come, First-Served)",
    "sstf": "SSTF (Shortest Seek Time First)",
    "scan": "SCAN (Elevador)",
    "look": "LOOK (SCAN sem ir até a borda)",
    "cscan": "C-SCAN (Circular SCAN)",
    "clook": "C-LOOK (C-SCAN sem ir até a borda)",
}
ALGORITMOS = tuple(NOMES)


@dataclass
class Plano:
    algo: str
    passos: list                 # [(trilha, atende?)]; o 1º passo é a posição inicial
    direcao_final: str = "up"

    @property
    def atendimento(self):
        """Trilhas realmente atendidas, na ordem."""
        return [t for t, atende in self.passos[1:] if atende]

    @property
    def trechos(self):
        return [abs(b[0] - a[0]) for a, b in zip(self.passos, self.passos[1:])]

    @property
    def total(self):
        return sum(self.trechos)

    @property
    def final(self):
        return self.passos[-1][0]


def _plano(algo, head, passos, direcao="up"):
    return Plano(algo, [(head, True)] + passos, direcao)


def fcfs(head, tracks):
    return _plano("fcfs", head, [(t, True) for t in tracks])


def sstf(head, tracks):
    pend, pos, passos = list(tracks), head, []
    while pend:
        prox = min(pend, key=lambda t: abs(t - pos))     # empate: a requisição mais antiga
        pend.remove(prox)
        passos.append((prox, True))
        pos = prox
    return _plano("sstf", head, passos)


def scan(head, tracks, max_track, direction="up", look=False):
    """Elevador: atende no sentido atual, inverte no fim e atende o resto.
    SCAN vai até a borda (0 ou max_track); LOOK inverte na última requisição."""
    if direction == "up":
        antes = sorted(t for t in tracks if t >= head)
        depois = sorted((t for t in tracks if t < head), reverse=True)
        borda = max_track
    else:
        antes = sorted((t for t in tracks if t <= head), reverse=True)
        depois = sorted(t for t in tracks if t > head)
        borda = 0
    passos = [(t, True) for t in antes]
    if depois:
        ultimo = passos[-1][0] if passos else head
        if not look and ultimo != borda:
            passos.append((borda, False))                # chega à borda sem atender nada
        passos += [(t, True) for t in depois]
    final = direction if not depois else ("down" if direction == "up" else "up")
    return _plano("look" if look else "scan", head, passos, final)


def cscan(head, tracks, max_track, look=False):
    """Circular: só atende subindo; ao chegar ao fim, volta ao início (o retorno
    conta como deslocamento) e sobe de novo. C-LOOK salta direto à menor requisição."""
    subida = sorted(t for t in tracks if t >= head)
    resto = sorted(t for t in tracks if t < head)
    passos = [(t, True) for t in subida]
    if resto:
        if not look:
            ultimo = passos[-1][0] if passos else head
            if ultimo != max_track:
                passos.append((max_track, False))
            passos.append((0, False))                    # retorno ao início
        passos += [(t, True) for t in resto]
    return _plano("clook" if look else "cscan", head, passos, "up")


def escalonar(algo, head, tracks, max_track, direction="up"):
    if algo == "fcfs":
        return fcfs(head, tracks)
    if algo == "sstf":
        return sstf(head, tracks)
    if algo in ("scan", "look"):
        return scan(head, tracks, max_track, direction, look=(algo == "look"))
    if algo in ("cscan", "clook"):
        return cscan(head, tracks, max_track, look=(algo == "clook"))
    raise ValueError(f"algoritmo desconhecido: {algo}")


def formatar(plano):
    """Linhas de texto no formato do enunciado (Exemplo 2.1)."""
    ordem = " -> ".join(str(t) if atende else f"[{t}]" for t, atende in plano.passos)
    desloc = " + ".join(f"|{a[0]}-{b[0]}|" for a, b in zip(plano.passos, plano.passos[1:]))
    soma = " + ".join(str(x) for x in plano.trechos)
    total = f"{soma} = {plano.total} trilhas" if len(plano.trechos) > 1 else f"{plano.total} trilhas"
    linhas = [f"--- ESCALONAMENTO DE DISCO: {NOMES[plano.algo]} ---",
              f"Ordem de Atendimento : {ordem}",
              f"Deslocamento da Cabeça: {desloc}",
              f"Total de Movimento    : {total}"]
    if any(not atende for _, atende in plano.passos):
        linhas.append("([n] = extremo/retorno da cabeça, sem atendimento; o deslocamento conta)")
    return linhas
