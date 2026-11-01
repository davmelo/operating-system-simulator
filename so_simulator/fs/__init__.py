from .allocators import ALOCADORES
from .blockdevice import BlockDevice
from .erros import FSError
from .filesystem import FileSystem
from .comandos import registrar


def criar_filesystem(estrategia="indexada", block_size=4096, n_blocks=16):
    dev = BlockDevice(block_size, n_blocks)
    return FileSystem(dev, ALOCADORES[estrategia](dev))


__all__ = ["ALOCADORES", "BlockDevice", "FSError", "FileSystem", "criar_filesystem", "registrar"]
