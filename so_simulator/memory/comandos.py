from .erros import MemoriaError
from .replacement import ALGORITMOS, simular
from .reserved import POLITICAS
from .util import tamanho


def _opcao(args, nome, padrao=None):
    if nome in args:
        i = args.index(nome)
        if i + 1 >= len(args):
            raise MemoriaError(f"faltou o valor de {nome}")
        return args[i + 1]
    if padrao is not None:
        return padrao
    raise MemoriaError(f"faltou {nome}")


def _processo(args):
    """--process NOME|PID  (aceita também --name e --pid)."""
    for flag in ("--process", "--name", "--pid"):
        if flag in args:
            return _opcao(args, flag)
    raise MemoriaError("faltou --process NOME (ou PID)")


def _simulacao(refs, n, algos):
    print(f"--- SIMULAÇÃO DE SUBSTITUIÇÃO DE PÁGINAS ({n} molduras) ---")
    print("Referências: " + " ".join(map(str, refs)))
    totais = []
    for a in algos:
        r = simular(refs, n, a)
        print(f"\n[{a.upper()}]  Ref | Molduras" + " " * max(1, 3 * n - 6) + "| Resultado")
        for ref, molduras, falta, vitima in r.passos:
            cel = " ".join(f"{'-' if m is None else m:>2}" for m in molduras)
            res = (f"PAGE FAULT (vítima: {vitima})" if vitima is not None else "PAGE FAULT") if falta else "HIT"
            print(f"       {ref:>3} | {cel} | {res}")
        print(f"Total {a.upper()}: {r.faltas} page faults ({100 * r.faltas / len(refs):.1f}% das referências)")
        totais.append((a, r.faltas))
    if len(totais) > 1:
        print("\n--- COMPARAÇÃO (menos page faults = melhor) ---")
        for a, f in sorted(totais, key=lambda t: t[1]):
            print(f"{a.upper():<14} {f:>3} page faults")


def registrar(shell, mm, fs=None):
    """Registra os comandos da Etapa 4 (memória). `mm` é o MemoryManager."""
    shell.pos_comando.append(mm.swap_io.flush)       # E/S de swap do comando é escalonada no fim dele

    def create_process(a):
        tipo = _opcao(a, "--type", "normal")
        mm.criar_processo(_opcao(a, "--name"), tamanho(_opcao(a, "--size")), tipo)

    def terminate_process(a):
        mm.encerrar(_processo(a))

    def memory_status(a):
        so_r, so_v = "--reserved" in a, "--virtual" in a
        if "--process" in a:
            mm.tabela_paginas(_opcao(a, "--process"))
        elif so_r and so_v or not (so_r or so_v):
            mm.status_resumo()
            if so_r and so_v:
                print()
                mm.status_reservada()
                print()
                mm.status_virtual()
        elif so_r:
            mm.status_reservada()
        else:
            mm.status_virtual()

    def memory_compact(a):
        if "--reserved" not in a:
            raise MemoriaError("use: memory_compact --reserved (só a Área Reservada é compactada; "
                               "a memória virtual é paginada e não fragmenta externamente)")
        mm.compactar()

    def translate_address(a):
        pagina = int(_opcao(a, "--page")) if "--page" in a else None
        endereco = int(_opcao(a, "--address")) if "--address" in a else None
        offset = int(_opcao(a, "--offset")) if "--offset" in a else None
        mm.traduzir(_processo(a), pagina, endereco, offset, "--write" in a)

    def execute_step(a):
        mm.executar_passo(_processo(a), _opcao(a, "--instruction"))

    def mem_write(a):
        faltas = mm.escrever(_processo(a), int(_opcao(a, "--address", "0")), _opcao(a, "--text").encode())
        print("\n".join(faltas) if faltas else "Escrita concluída (todas as páginas já estavam na RAM).")

    def mem_read(a):
        dados, faltas = mm.ler(_processo(a), int(_opcao(a, "--address", "0")), int(_opcao(a, "--length")))
        if faltas:
            print("\n".join(faltas))
        texto = dados.rstrip(b"\0").decode("utf-8", "replace")
        print(f"Lido ({len(dados)} Bytes): {texto!r}")

    def memory_algo(a):
        if a:
            mm.set_algoritmo(a[0])
        print(f"Algoritmo de substituição de páginas: {mm.sub.longo}")

    def memory_policy(a):
        if a:
            mm.reservada.set_politica(a[0])
        print(f"Política de alocação contígua da Área Reservada: {mm.reservada.politica}-fit")

    def memory_report(a):
        if "--every" in a:
            mm.intervalo = int(_opcao(a, "--every"))
            print("Relatório periódico: " + (f"a cada {mm.intervalo} operações" if mm.intervalo else "desligado"))
        else:
            mm.relatorio_fragmentacao()

    def swap_log(a):
        if a and a[0] in ("on", "off"):
            mm.swap_io.log = a[0] == "on"
        print(f"Log de E/S do swap: {'ligado' if mm.swap_io.log else 'desligado'}")

    def page_sim(a):
        refs = [int(x) for x in _opcao(a, "--refs").split(",")]
        n = int(_opcao(a, "--frames", "3"))
        escolha = _opcao(a, "--algo", mm.sub.nome.lower().replace(" ", "_"))
        nomes = list(ALGORITMOS) + ["optimal"]
        algos = nomes if escolha == "all" else [escolha]
        for x in algos:
            if x not in nomes:
                raise MemoriaError(f"algoritmo inválido: {x} (use {', '.join(nomes)} ou all)")
        _simulacao(refs, n, algos)

    r = shell.registrar
    r("create_process", create_process, 'create_process --name "Nome" --size 512KB [--type critical|normal]')
    r("terminate_process", terminate_process, 'terminate_process --name "Nome" | --pid N')
    r("memory_status", memory_status, "memory_status [--reserved] [--virtual] [--process NOME]")
    r("memory_compact", memory_compact, "memory_compact --reserved  (compacta a Área Reservada; root)")
    r("translate_address", translate_address,
      "translate_address --process NOME (--page N [--offset O] | --address A) [--write]")
    r("execute_step", execute_step, 'execute_step --process NOME --instruction "LOAD_DATA Página 2"')
    r("mem_write", mem_write, 'mem_write --process NOME --address A --text "dados"')
    r("mem_read", mem_read, "mem_read --process NOME --address A --length N")
    r("memory_algo", memory_algo, "memory_algo [fifo|lru|second_chance|lfu|nru]")
    r("memory_policy", memory_policy, "memory_policy [first|best|worst]  (alocação contígua)")
    r("memory_report", memory_report, "memory_report [--every N]  (fragmentação interna e externa)")
    r("swap_log", swap_log, "swap_log [on|off]  (log de E/S do swap)")
    r("page_sim", page_sim, "page_sim --refs 7,0,1,2,0,3 --frames 3 [--algo lru|all]  (demo de algoritmos)")
