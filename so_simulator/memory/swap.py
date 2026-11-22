"""Memória secundária (swap): um disco/SSD da Etapa 2 usado como área de troca.

Cada página de um processo ocupa um "slot" de `blocos_por_slot` blocos do dispositivo.
Toda gravação/leitura de swap passa pela camada de E/S (InputOutput), então ganha
escalonamento do braço de disco (HDD), distribuição em RAID ou FTL/Wear Leveling (SSD).
"""
import math

from .erros import MemoriaError


class SwapSpace:
    def __init__(self, io, tamanho_pagina):
        self.io = io
        self.tamanho_pagina = tamanho_pagina
        self.k = math.ceil(tamanho_pagina / io.block_size)      # blocos por página
        self.n_slots = io.n_blocks // self.k
        if self.n_slots < 1:
            raise MemoriaError("swap pequeno demais para guardar uma página")
        self.livres = list(range(self.n_slots))
        self.slots = {}                  # (pid, pagina) -> slot
        self.dono = {}                   # slot -> (pid, pagina)
        self.gravacoes = self.leituras = 0

    @property
    def usados(self):
        return len(self.slots)

    def tem(self, pid, pagina):
        return (pid, pagina) in self.slots

    def pode_gravar(self, pid, pagina):
        return self.tem(pid, pagina) or bool(self.livres)

    def gravar(self, pid, pagina, dados):
        chave = (pid, pagina)
        if chave not in self.slots:
            if not self.livres:
                raise MemoriaError("[FALHA] Swap cheio: não há onde guardar a página substituída")
            slot = self.livres.pop(0)
            self.slots[chave], self.dono[slot] = slot, chave
        base = self.slots[chave] * self.k
        bs = self.io.block_size
        for j in range(self.k):
            self.io.write_block(base + j, dados[j * bs:(j + 1) * bs])
        self.gravacoes += 1

    def ler(self, pid, pagina):
        base = self.slots[(pid, pagina)] * self.k
        self.leituras += 1
        return b"".join(self.io.read_block(base + j) for j in range(self.k))[:self.tamanho_pagina]

    def liberar(self, pid, pagina):
        slot = self.slots.pop((pid, pagina), None)
        if slot is None:
            return False
        del self.dono[slot]
        for j in range(self.k):
            self.io.clear_block(slot * self.k + j)              # no SSD vira TRIM
        self.livres.append(slot)
        self.livres.sort()
        return True

    def liberar_processo(self, pid):
        return sum(self.liberar(p, g) for (p, g) in list(self.slots) if p == pid)

    def rotulo(self, bloco):
        chave = self.dono.get(bloco // self.k)
        return f"Swap: Página Lógica {chave[1]} do PID {chave[0]}" if chave else f"Swap: bloco {bloco}"
