import argparse
from . import fs as modulo_fs
from .shell import Shell


def main():
    ap = argparse.ArgumentParser(prog="so_simulator")
    ap.add_argument("--strategy", choices=list(modulo_fs.ALOCADORES), default="indexada")
    ap.add_argument("--block-size", type=int, default=4096)
    ap.add_argument("--blocks", type=int, default=16)
    args = ap.parse_args()

    sistema = modulo_fs.criar_filesystem(args.strategy, args.block_size, args.blocks)
    shell = Shell(prompt=lambda: f"user@so-simulator:~{sistema.cwd.path()} $ ")
    modulo_fs.registrar(shell, sistema)      # Etapa 1

    print(f"Simulador de SO iniciado (alocação {args.strategy}). Digite 'help'.")
    shell.loop()


main()
