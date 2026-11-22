from .comandos import registrar
from .erros import FragmentacaoExterna, MemoriaError
from .memory import MemoryManager, Processo
from .replacement import ALGORITMOS, simular
from .reserved import POLITICAS, ReservedArea
from .swap import SwapSpace

__all__ = ["ALGORITMOS", "FragmentacaoExterna", "MemoriaError", "MemoryManager", "POLITICAS",
           "Processo", "ReservedArea", "SwapSpace", "registrar", "simular"]
