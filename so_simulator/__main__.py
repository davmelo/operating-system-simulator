import argparse
from . import fs as modulo_fs
from . import io as modulo_io
from .io.raid import MIN_DISCOS
from .io.scheduler import ALGORITMOS
from .io.ssd import MODOS_WL
from .shell import Shell
from . import memory as modulo_memoria
from .fs.erros import FSError
from .fs.util import parse_size
from .memory.reserved import POLITICAS
from .memory.replacement import ALGORITMOS as ALGORITMOS_PAGINAS
from .security.security import SecurityManager, registrar as registrar_seguranca


def _tam(txt):
    try:
        return parse_size(txt)
    except FSError as e:
        raise argparse.ArgumentTypeError(str(e))


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
    # Etapa 4: Gerenciamento de Memória
    ap.add_argument("--ram-reserved", type=_tam, default=_tam("4MB"), help="Área Reservada (contígua)")
    ap.add_argument("--kernel-base", type=_tam, default=_tam("1MB"), help="tamanho do Núcleo Base do SO")
    ap.add_argument("--ram-dynamic", type=_tam, default=_tam("64KB"), help="Região Dinâmica (paginada)")
    ap.add_argument("--page-size", type=_tam, default=4096)
    ap.add_argument("--replacement", choices=list(ALGORITMOS_PAGINAS), default="lru")
    ap.add_argument("--reserved-policy", choices=POLITICAS, default="first")
    ap.add_argument("--swap-size", type=_tam, default=_tam("256KB"))
    ap.add_argument("--swap-device", choices=["hdd", "ssd"], default="hdd")
    ap.add_argument("--report-every", type=int, default=10, help="relatório de fragmentação a cada N operações (0=off)")
    args = ap.parse_args()

    entrada_saida = modulo_io.criar_io(args.block_size, args.blocks, args.device, args.raid,
                                       args.disks, args.io_algo, args.tracks, args.head,
                                       args.ssd_wl, args.ssd_pages_per_block)
    sistema = modulo_fs.criar_filesystem(args.strategy, args.block_size, args.blocks,
                                         dispositivo=entrada_saida)
    shell = Shell(prompt=lambda: f"user@so-simulator:~{sistema.cwd.path()} $ ")
    modulo_fs.registrar(shell, sistema)                       # Etapa 1
    modulo_io.registrar(shell, entrada_saida, sistema)        # Etapa 2
    seguranca = SecurityManager(sistema)                      # Etapa 3
    registrar_seguranca(shell, seguranca)
    shell.prompt = lambda: f"{seguranca.current_user}@so-simulator:{sistema.cwd.path()} $ "
    # Etapa 4: o swap é um dispositivo próprio da camada de E/S (Etapa 2)
    swap_io = modulo_io.criar_io(args.block_size, max(1, -(-args.swap_size // args.block_size)),
                                 args.swap_device, "none", None, args.io_algo, args.tracks,
                                 args.head, args.ssd_wl, args.ssd_pages_per_block)
    swap_io.etiqueta, swap_io.origem = "E/S - SWAP", "Gerenciador de Memória (swap)"
    memoria = modulo_memoria.MemoryManager(
        swap_io, args.ram_reserved, args.kernel_base, args.ram_dynamic, args.page_size,
        args.replacement, args.reserved_policy, seguranca, args.report_every)
    modulo_memoria.registrar(shell, memoria, sistema)

    print(f"Simulador de SO iniciado (alocação {args.strategy}; "
          f"armazenamento: {entrada_saida.backend.descricao()}). Digite 'help'.")
    shell.loop()


if __name__ == "__main__":
    main()
