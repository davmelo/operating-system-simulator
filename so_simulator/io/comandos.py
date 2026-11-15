import random
import string

from ..fs.erros import FSError
from ..fs.util import fmt, parse_size
from .erros import IOSimError
from .raid import RAIDArray
from .scheduler import ALGORITMOS, NOMES, escalonar, formatar
from .ssd import MODOS_WL, SSD, demonstracao


def _opcao(args, nome, padrao=None):
    if nome in args:
        i = args.index(nome)
        if i + 1 >= len(args):
            raise IOSimError(f"faltou o valor de {nome}")
        return args[i + 1]
    if padrao is not None:
        return padrao
    raise IOSimError(f"faltou {nome}")


def _conteudo(nome, tamanho):
    """Conteúdo imprimível e determinístico (para o XOR/paridade ter dados reais)."""
    rng = random.Random(nome)
    return "".join(rng.choices(string.ascii_letters + string.digits, k=tamanho)).encode()


def registrar(shell, es, fs):
    """Registra os comandos da Etapa 2 (E/S, escalonamento, RAID, SSD) no shell."""

    def rotulo(b):
        slot = fs.alloc.slots[b] if 0 <= b < len(fs.alloc.slots) else None
        if slot and slot["kind"] == "DADO":
            nome = slot["node"].name
            return f"Dados de '{nome}' - Parte {slot['seq']}"
        if slot and slot["kind"] == "INODE":
            return f"Índice de '{slot['node'].name}'"
        return f"Bloco lógico {b}"

    es.rotulo = rotulo
    shell.pos_comando.append(es.flush)           # cada comando termina escalonando suas requisições

    def hdd():
        if es.backend.kind != "hdd":
            raise IOSimError("comando disponível só com HDD/RAID (use io_device hdd)")
        return es.backend

    def ssd():
        if es.backend.kind != "ssd":
            raise IOSimError("comando disponível só com SSD (use io_device ssd)")
        return es.backend

    # ------------------------------------------------------------ arquivos (disparam E/S)
    def write_file(a):
        caminho, resto = a[0], a[1:]
        teste = "--teste-falha" in resto
        key = None
        if "--key" in resto:
            pos_key = resto.index("--key")
            if pos_key + 1 >= len(resto):
                raise FSError("faltou o valor de --key")
            key = resto[pos_key + 1]
            del resto[pos_key:pos_key + 2]
        resto = [x for x in resto if x != "--teste-falha"]
        if "--size" in resto:
            dados = _conteudo(caminho, parse_size(_opcao(resto, "--size")))
        elif "--text" in resto:
            dados = _opcao(resto, "--text").encode()
        elif resto:
            dados = " ".join(resto).encode()
        else:
            raise FSError('use: write_file <arquivo> "texto" | --text "..." | --size 8KB [--teste-falha]')
        try:
            fs.resolve(caminho)
        except FSError:
            fs.create(caminho)
        fs.write_file(caminho, dados, key=key)
        es.flush()
        if teste:
            es.teste_falha()

    def read_file(a):
        if not a:
            raise FSError("uso: read_file <arquivo> [--key CHAVE]")
        caminho = a[0]
        key = _opcao(a, "--key") if "--key" in a else None
        # A leitura física passa pelo FS; arquivos cifrados só são apresentados
        # após validação da chave e do MAC na camada de Segurança.
        dados = fs.read_file(caminho, key=key).rstrip(b"\0")
        node = fs.resolve(caminho)
        print(f"[SISTEMA DE ARQUIVOS] Leitura concluída: {node.path()} ({node.size} bytes)")
        if dados:
            print(dados.decode("utf-8", "replace"))
        else:
            print("[arquivo vazio]")

    # ------------------------------------------------------------ escalonamento
    def io_sched(a):
        trilhas = [int(x) for x in _opcao(a, "--tracks").split(",")]
        head = int(_opcao(a, "--head"))
        algo = _opcao(a, "--algo", es.algo)
        maximo = int(_opcao(a, "--max", str(es.backend.tracks - 1 if es.backend.kind == "hdd" else 199)))
        sentido = _opcao(a, "--dir", "up")
        if sentido not in ("up", "down"):
            raise IOSimError("--dir deve ser up ou down")
        algos = ALGORITMOS if algo == "all" else (algo,)
        for x in algos:
            if x not in ALGORITMOS:
                raise IOSimError(f"algoritmo inválido: {x} (use {', '.join(ALGORITMOS)} ou all)")
        print(f"[E/S] Requisições de leitura/escrita pendentes nas trilhas: {trilhas}")
        print(f"Posição atual da cabeça de leitura: Trilha {head}")
        resultados = []
        for x in algos:
            plano = escalonar(x, head, trilhas, maximo, sentido)
            print("\n" + "\n".join(formatar(plano)))
            resultados.append((x, plano.total))
        if len(algos) > 1:
            print("\n--- COMPARAÇÃO (total de trilhas percorridas) ---")
            for x, total in sorted(resultados, key=lambda r: r[1]):
                print(f"{x.upper():<6} {total:>5}  {NOMES[x]}")

    def io_algo(a):
        if a:
            es.set_algo(a[0])
        print(f"Escalonador do braço de disco: {NOMES[es.algo]}")

    def io_log(a):
        if a and a[0] in ("on", "off"):
            es.log = a[0] == "on"
        print(f"Log de E/S: {'ligado' if es.log else 'desligado'}")

    def io_status(a):
        be = es.backend
        print("[E/S - STATUS]")
        print(f"Dispositivo: {be.descricao()}")
        print(f"Blocos lógicos do FS: {es.n_blocks} | gravados: {len(es._valid)} | "
              f"operações do FS: {es.leituras} leituras, {es.escritas} gravações")
        if be.kind == "ssd":
            print(be.resumo())
            return
        print(f"Escalonador: {NOMES[es.algo]}")
        for d in be.disks:
            estado = "FALHO" if d.failed else "OK"
            print(f"Disco {d.id}: {estado:<5} | cabeça: trilha {d.head:>3} | {d.leituras} leituras, "
                  f"{d.escritas} escritas | movimento acumulado: {d.movimento} trilhas")
        print(f"Movimento total acumulado do braço: {sum(d.movimento for d in be.disks)} trilhas")

    # ------------------------------------------------------------ troca de dispositivo
    def raid_mode(a):
        nivel = a[0]
        n = int(_opcao(a, "--disks")) if "--disks" in a else None
        ref = es.backend
        tracks = ref.tracks if ref.kind == "hdd" else 200
        head = ref.disks[0].head if ref.kind == "hdd" else 53
        novo = RAIDArray(nivel, n, es.block_size, es.n_blocks, tracks, head)
        es.trocar_backend(novo)
        print(f"Armazenamento: {novo.descricao()} — {len(es._valid)} bloco(s) migrado(s).")

    def io_device(a):
        if a[0] == "ssd":
            wl = _opcao(a, "--wl", "static")
            ppb = int(_opcao(a, "--pages", "4"))
            novo = SSD(es.n_blocks, es.block_size, ppb, wear_leveling=wl)
        elif a[0] == "hdd":
            ref = es.backend
            novo = RAIDArray("single", None, es.block_size, es.n_blocks,
                             ref.tracks if ref.kind == "hdd" else 200, 53)
        else:
            raise IOSimError("use io_device hdd | ssd [--wl none|dynamic|static] [--pages N]")
        es.trocar_backend(novo)
        print(f"Armazenamento: {novo.descricao()} — {len(es._valid)} bloco(s) migrado(s).")

    # ------------------------------------------------------------ RAID
    def raid_status(a):
        be = hdd()
        print(f"[RAID] {be.descricao()} | tolerância a falhas: {be.tolerancia} disco(s) | "
              f"capacidade útil: {fmt(es.n_blocks * es.block_size)} de {fmt(be.capacidade_bruta)} brutos")
        cab = "Faixa | " + " | ".join(f"Disco {d.id}{' ✗' if d.failed else '  '}" for d in be.disks)
        print(cab)
        for faixa, cel in be.layout():
            print(f"{faixa:>5} | " + " | ".join(f"{c:^8}" for c in cel))
        print("(Lxx = bloco lógico, P = paridade, ✗ = disco falho)")

    def raid_fail(a):
        be = hdd()
        for linha in be.fail(int(a[0])):
            print(f"* {linha}")

    def raid_rebuild(a):
        be = hdd()
        es.flush(silencioso=True)
        for linha in be.rebuild(int(a[0])):
            print(f"* {linha}")
        es.flush(silencioso=True)

    def raid_check(a):
        ok, linhas = hdd().verificar()
        print("\n".join(linhas))

    def raid_test(a):
        bloco = int(_opcao(a, "--bloco")) if "--bloco" in a else None
        disco = int(_opcao(a, "--disk")) if "--disk" in a else None
        es.teste_falha(bloco, disco)

    # ------------------------------------------------------------ SSD
    def ssd_status(a):
        s = ssd()
        print(f"[SSD] {s.descricao()}")
        print("Páginas: V = válida, I = inválida (aguardando GC), L = livre")
        print("\n".join(s.mapa_visual()))
        print(s.resumo())

    def ssd_demo(a):
        demonstracao(int(_opcao(a, "--writes", "2000")))

    r = shell.registrar
    r("write_file", write_file, 'write_file <arquivo> "texto" | --size 8KB [--key CHAVE] [--teste-falha]  (cria se preciso; gera E/S)')
    r("read_file", read_file, "read_file <arquivo>  (lê o conteúdo; gera E/S)")
    r("io_sched", io_sched, "io_sched --tracks 98,183,37 --head 53 [--algo sstf|all] [--max 199] [--dir up|down]  (demo)")
    r("io_algo", io_algo, "io_algo [fcfs|sstf|scan|look|cscan|clook]  (escalonador do braço)")
    r("io_log", io_log, "io_log [on|off]  (mostra/oculta o log de E/S)")
    r("io_status", io_status, "io_status  (cabeças, contadores e movimento acumulado)")
    r("io_device", io_device, "io_device hdd | ssd [--wl none|dynamic|static] [--pages N]")
    r("raid_mode", raid_mode, "raid_mode single|0|1|4|5 [--disks N]  (migra os dados)")
    r("raid_status", raid_status, "raid_status  (mapa de faixas e paridade)")
    r("raid_fail", raid_fail, "raid_fail <disco>  (falha definitiva; dados do disco somem)")
    r("raid_rebuild", raid_rebuild, "raid_rebuild <disco>  (troca o disco e reconstrói)")
    r("raid_check", raid_check, "raid_check  (confere paridade/espelhos)")
    r("raid_test", raid_test, "raid_test [--bloco N] [--disk N]  (falha simulada + recuperação)")
    r("ssd_status", ssd_status, "ssd_status  (páginas, desgaste por bloco, amplificação)")
    r("ssd_demo", ssd_demo, "ssd_demo [--writes N]  (compara os modos de Wear Leveling)")
