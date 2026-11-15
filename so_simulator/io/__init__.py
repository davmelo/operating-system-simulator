from .comandos import registrar
from .erros import ArranjoInoperante, DiscoFalhou, IOSimError
from .input_output import InputOutput
from .physical_disk import PhysicalDisk
from .raid import RAIDArray, xor_blocks
from .request import IORequest
from .scheduler import ALGORITMOS, escalonar
from .ssd import SSD


def criar_io(block_size=4096, n_blocks=16, device="hdd", raid="none", n_discos=None,
             algo="fcfs", tracks=200, head=53, ssd_wl="static", ssd_ppb=4):
    """Monta a camada de E/S (HDD/RAID ou SSD) com a geometria pedida."""
    if device == "ssd":
        backend = SSD(n_blocks, block_size, ssd_ppb, wear_leveling=ssd_wl)
    else:
        backend = RAIDArray(raid, n_discos, block_size, n_blocks, tracks, head)
    return InputOutput(backend, algo)


__all__ = ["ALGORITMOS", "ArranjoInoperante", "DiscoFalhou", "IOSimError", "IORequest",
           "InputOutput", "PhysicalDisk", "RAIDArray", "SSD", "criar_io", "escalonar",
           "registrar", "xor_blocks"]
