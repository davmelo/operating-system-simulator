import argparse
from . import fs as modulo_fs
from . import io as modulo_io
from .io.raid import MIN_DISCOS
from .io.scheduler import ALGORITMOS
from .io.ssd import MODOS_WL
from .shell import Shell


def main():
    ap = argparse.ArgumentParser(prog="so_simulator")
    ap.add_argument("--strategy", choices=list(modulo_fs.ALOCADORES), default="indexada")
    ap.add_argument("--block-size", type=int, default=4096)
    ap.add_argument("--blocks", type=int, default=16)
    # Etapa 2: Entrada/Saída e Armazenamento
    ap.add_argument("--device", choices=["hdd", "ssd"], default="hdd")
    ap.add_argument("--raid", choices=["none", "0", "1", "4", "5"], default="none")
    ap.add_argument("--disks", type=int, help="nº de discos do arranjo (padrão: mínimo do nível)")
    ap.add_argument("--io-algo", choices=ALGORITMOS, default="fcfs")
    ap.add_argument("--tracks", type=int, default=200, help="trilhas por disco")
    ap.add_argument("--head", type=int, default=53, help="trilha inicial da cabeça")
    ap.add_argument("--ssd-wl", choices=MODOS_WL, default="static")
    ap.add_argument("--ssd-pages-per-block", type=int, default=4)
    args = ap.parse_args()

    entrada_saida = modulo_io.criar_io(args.block_size, args.blocks, args.device, args.raid,
                                       args.disks, args.io_algo, args.tracks, args.head,
                                       args.ssd_wl, args.ssd_pages_per_block)
    sistema = modulo_fs.criar_filesystem(args.strategy, args.block_size, args.blocks,
                                         dispositivo=entrada_saida)
    shell = Shell(prompt=lambda: f"user@so-simulator:~{sistema.cwd.path()} $ ")
    modulo_fs.registrar(shell, sistema)                       # Etapa 1
    modulo_io.registrar(shell, entrada_saida, sistema)        # Etapa 2

    print(f"Simulador de SO iniciado (alocação {args.strategy}; "
          f"armazenamento: {entrada_saida.backend.descricao()}). Digite 'help'.")
    shell.loop()


if __name__ == "__main__":
    main()
