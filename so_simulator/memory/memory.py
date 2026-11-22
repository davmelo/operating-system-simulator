"""Gerenciador de memória com alocação HÍBRIDA.

RAM física simulada = [ Região Estática/Fixa (Reservada) | Região Dinâmica (Memória Virtual) ]

* Reservada: Kernel e processos críticos, alocação contígua, sem paginação (reserved.py).
* Dinâmica: processos comuns, paginação por demanda, tradução página→moldura, page fault,
  substituição de páginas (FIFO, LRU, Segunda Chance, LFU, NRU) e swap em disco (swap.py).

Integração com as etapas anteriores:
  - Etapa 2: o swap é um dispositivo da camada de E/S (escalonamento do braço, RAID ou SSD);
  - Etapa 3: processos críticos, compactação e acesso à memória de outros usuários exigem
    privilégios; as negações vão para o log de auditoria do SecurityManager.
"""
import math
import re
from dataclasses import dataclass, field

from ..security.security import SecurityError
from .erros import MemoriaError
from .replacement import Frame, criar_substituidor, desc
from .reserved import ReservedArea
from .swap import SwapSpace
from .util import KB, MB, barra, dec, intervalo_paginas, kb, mb, mb_total, pc, razao

T_REF = 5        # ms simulados por referência à memória
T_FALTA = 20     # ms extras para tratar um page fault (E/S de swap)


@dataclass
class PTE:
    """Entrada da tabela de páginas."""
    presente: bool = False
    moldura: int | None = None


@dataclass
class Processo:
    pid: int
    nome: str
    tipo: str                 # "critical" | "normal"
    tamanho: int              # bytes
    dono: str
    criado_em: int
    tabela: list = field(default_factory=list)    # só processos paginados
    segmento: object = None                       # só processos críticos
    faltas: int = 0
    acessos: int = 0

    @property
    def paginas(self):
        return len(self.tabela)


class MemoryManager:
    def __init__(self, swap_io, reservada=4 * MB, nucleo=1 * MB, dinamica=64 * KB,
                 tamanho_pagina=4096, algoritmo="lru", politica="first",
                 seguranca=None, intervalo_relatorio=10):
        if tamanho_pagina <= 0:
            raise MemoriaError("tamanho de página deve ser positivo")
        if dinamica // tamanho_pagina < 1:
            raise MemoriaError("a RAM dinâmica deve ter pelo menos uma moldura")
        self.reservada = ReservedArea(reservada, nucleo, politica)
        self.tamanho_pagina = tamanho_pagina
        self.n_frames = dinamica // tamanho_pagina
        self.dinamica = self.n_frames * tamanho_pagina
        self.base_dinamica = reservada               # endereço físico onde a região dinâmica começa
        self.frames = [Frame(i) for i in range(self.n_frames)]
        self.sub = criar_substituidor(algoritmo)
        self.swap_io = swap_io
        self.swap = SwapSpace(swap_io, tamanho_pagina)
        swap_io.rotulo = self.swap.rotulo
        self.seg = seguranca
        self.intervalo = intervalo_relatorio
        self.procs = {}
        self.prox_pid = 100
        self.agora = 0                               # relógio simulado (ms)
        self.seq = 0                                 # ordem de chegada das páginas
        self.ops = 0
        self.refs = self.hits = self.faltas = self.evicoes = 0

    # ------------------------------------------------------------ configuração
    def set_algoritmo(self, nome):
        self.sub = criar_substituidor(nome)

    # ------------------------------------------------------------ segurança (Etapa 3)
    def _usuario(self):
        return self.seg.current_user if self.seg else "root"

    def _root(self):
        return self.seg is None or self.seg.mode == "MODO_KERNEL"

    def _exigir_root(self):
        if self.seg:
            self.seg.require_root()

    def _autorizar(self, proc, acao):
        if self._root() or proc.dono == self._usuario():
            return
        u = self._usuario()
        self.seg._log(f"MEMORIA_NEGADA usuário={u} ação={acao} processo={proc.nome} (PID {proc.pid})")
        raise SecurityError(f"[NEGADO] O usuário '{u}' não pode {acao} o processo '{proc.nome}' "
                            f"(PID {proc.pid}) de '{proc.dono}'. Tentativa registrada nos logs.")

    # ------------------------------------------------------------ processos
    def buscar(self, ref):
        ref = str(ref)
        if ref.isdigit() and int(ref) in self.procs:
            return self.procs[int(ref)]
        for p in self.procs.values():
            if p.nome == ref:
                return p
        raise MemoriaError(f"processo não encontrado: {ref}")

    def criar_processo(self, nome, tamanho, tipo="normal"):
        if tipo not in ("critical", "normal"):
            raise MemoriaError("tipo inválido (use critical ou normal)")
        if tamanho <= 0:
            raise MemoriaError("tamanho do processo deve ser maior que zero")
        if any(p.nome == nome for p in self.procs.values()):
            raise MemoriaError(f"já existe um processo chamado '{nome}'")
        if tipo == "critical":
            self._exigir_root()
            proc = self._criar_critico(nome, tamanho)
        else:
            proc = self._criar_normal(nome, tamanho)
        self._fim_operacao()
        return proc

    def _novo_pid(self):
        pid, self.prox_pid = self.prox_pid, self.prox_pid + 1
        return pid

    def _criar_critico(self, nome, tamanho):
        seg = self.reservada.alocar(nome, self.prox_pid, tamanho)    # pode falhar: o PID só é gasto depois
        proc = Processo(self._novo_pid(), nome, "critical", tamanho, self._usuario(), self.agora, segmento=seg)
        self.procs[proc.pid] = proc
        print(f"[CRIAÇÃO DE PROCESSO CRÍTICO - ÁREA RESERVADA] '{nome}' (PID {proc.pid}, dono: {proc.dono})")
        print(f"- Alocado de forma contígua na Área Reservada: [{mb(seg.inicio)} até {mb(seg.fim)}] "
              f"({kb(seg.tamanho)})")
        return proc

    def _criar_normal(self, nome, tamanho):
        ps = self.tamanho_pagina
        n = math.ceil(tamanho / ps)
        if n > self.n_frames + self.swap.n_slots:
            raise MemoriaError(f"[FALHA] Processo de {n} páginas não cabe na memória virtual "
                               f"({self.n_frames} molduras + {self.swap.n_slots} páginas de swap)")
        proc = Processo(self._novo_pid(), nome, "normal", tamanho, self._usuario(), self.agora,
                        tabela=[PTE() for _ in range(n)])
        self.procs[proc.pid] = proc
        print("[CRIAÇÃO DE PROCESSO NÃO CRÍTICO - MEMÓRIA VIRTUAL]")
        print(f"Processo            : '{nome}' (PID {proc.pid}, dono: {proc.dono})")
        print(f"Tamanho do Processo : {kb(tamanho)} ({tamanho} Bytes)")
        print(f"Tamanho da Página   : {kb(ps)} ({ps} Bytes)")
        print(f"Cálculo de Páginas  : {kb(tamanho)} / {kb(ps)} = {dec(tamanho / ps)} -> "
              f"Requer {n} Página{'s' if n > 1 else ''} Lógica{'s' if n > 1 else ''} "
              f"({intervalo_paginas(n)}).")
        print("\n--- ANÁLISE DE FRAGMENTAÇÃO INTERNA ---")
        usados = [ps] * (n - 1) + [tamanho - (n - 1) * ps]
        i = 0
        while i < n:
            if i < n - 1 and n > 4:                       # agrupa páginas cheias consecutivas
                print(f"Páginas Lógicas {i} a {n - 2} : {ps} Bytes ocupados (100%)")
                i = n - 1
                continue
            print(f"Página Lógica {i} : {usados[i]} Bytes ocupados ({pc(razao(usados[i], ps))}"
                  f"{' de uso' if usados[i] < ps else ''})")
            i += 1
        perda = n * ps - tamanho
        if perda:
            print(f"Fragmentação Interna Total: {perda} Bytes de desperdício na Página Lógica {n - 1}.")
        else:
            print("Fragmentação Interna Total: 0 Bytes (o processo preenche todas as páginas).")
        print("Status: Tabela de Páginas gerada sem alocar molduras na RAM (Alocação por demanda).")
        return proc

    def encerrar(self, ref):
        proc = self.buscar(ref)
        self._autorizar(proc, "encerrar")
        if proc.tipo == "critical":
            seg = self.reservada.liberar(proc.pid)
            print(f"[ENCERRAMENTO DE PROCESSO CRÍTICO] Processo '{proc.nome}' finalizado.")
            print(f"Área de {kb(seg.tamanho)} no intervalo [{mb(seg.inicio)} a {mb(seg.fim)}] "
                  "liberada na Área Reservada da RAM.")
        else:
            molduras = 0
            for pte in proc.tabela:
                if pte.presente:
                    self.frames[pte.moldura].limpar()
                    molduras += 1
            paginas_swap = self.swap.liberar_processo(proc.pid)
            print(f"[ENCERRAMENTO DE PROCESSO] Processo '{proc.nome}' (PID {proc.pid}) finalizado.")
            print(f"Liberadas {molduras} moldura(s) da RAM dinâmica e {paginas_swap} página(s) do Swap.")
        del self.procs[proc.pid]
        self._fim_operacao()

    # ------------------------------------------------------------ paginação: referência e page fault
    def referenciar(self, proc, pagina, escrita=False, log=None):
        """Uma referência à página lógica `pagina`. Devolve (moldura, info)."""
        if not 0 <= pagina < proc.paginas:
            raise MemoriaError(f"[VIOLAÇÃO DE SEGMENTAÇÃO] Página Lógica {pagina} fora do espaço de "
                               f"endereçamento de '{proc.nome}' (páginas 0 a {proc.paginas - 1})")
        self.agora += T_REF
        self.refs += 1
        proc.acessos += 1
        pte = proc.tabela[pagina]
        info = {"hit": pte.presente, "vitima": None, "origem": None}
        if pte.presente:
            self.hits += 1
            frame = self.frames[pte.moldura]
        else:
            self.faltas += 1
            proc.faltas += 1
            frame = self._tratar_falta(proc, pagina, info, log or (lambda _: None))
        self.sub.ao_acessar(self.frames, self.agora)
        frame.ultimo_acesso, frame.ref = self.agora, True
        frame.contagem += 1
        if escrita:
            frame.sujo = True
        info["moldura"] = frame.idx
        return frame, info

    def _tratar_falta(self, proc, pagina, info, log):
        log(f"\n[PAGE FAULT DETECTADO]: Página Lógica {pagina} ausente na RAM!")
        livre = next((f for f in self.frames if not f.ocupada), None)
        if livre is None:
            log(f"Memória RAM cheia. Executando Substituição de Página (Algoritmo {self.sub.nome}):")
            vit, linhas = self.sub.escolher(self.frames, self.agora)
            for linha in linhas:
                log(f"- {linha}")
            precisa = vit.sujo or not self.swap.tem(vit.pid, vit.pagina)
            if precisa and not self.swap.pode_gravar(vit.pid, vit.pagina):
                raise MemoriaError("[FALHA] Swap cheio: não há onde guardar a página substituída")
            info["vitima"] = (vit.pid, vit.pagina, precisa)
            self._despejar(vit, precisa, log)
            frame = vit
        else:
            log(f"Há moldura livre (Moldura Física {livre.idx}): nenhuma substituição necessária.")
            frame = livre
        if self.swap.tem(proc.pid, pagina):
            log(f"- Lendo Página Lógica {pagina} do Swap (Disco): E/S de leitura.")
            dados, info["origem"] = bytearray(self.swap.ler(proc.pid, pagina)), "swap"
        else:
            log("- Primeiro acesso: página zerada (zero-fill), sem leitura de disco.")
            dados, info["origem"] = bytearray(self.tamanho_pagina), "zero-fill"
        self.seq += 1
        frame.limpar()
        frame.pid, frame.pagina, frame.ordem = proc.pid, pagina, self.seq
        frame.carregado_em, frame.dados = self.agora, dados
        pte = proc.tabela[pagina]
        pte.presente, pte.moldura = True, frame.idx
        self.agora += T_FALTA
        log(f"- Carregando Página Lógica {pagina} do {proc.nome} na Moldura Física {frame.idx} (RAM Dinâmica).")
        log(f"- Tabela de Páginas atualizada: Página Lógica {pagina} ---> Moldura Física {frame.idx}.")
        return frame

    def _despejar(self, vit, precisa, log):
        dono = self.procs[vit.pid]
        if precisa:
            self.swap.gravar(vit.pid, vit.pagina, bytes(vit.dados))
            log(f"- Removendo {desc(vit)} da RAM -> Salvando no Swap (Disco).")
        else:
            log(f"- Removendo {desc(vit)} da RAM -> Página limpa: já há cópia idêntica no Swap "
                "(nenhuma escrita em disco).")
        pte = dono.tabela[vit.pagina]
        pte.presente, pte.moldura = False, None
        vit.limpar()
        self.evicoes += 1

    # ------------------------------------------------------------ comandos de alto nível
    def _paginado(self, ref, acao):
        proc = self.buscar(ref)
        self._autorizar(proc, acao)
        return proc

    def traduzir(self, ref, pagina=None, endereco=None, offset=None, escrita=False):
        proc = self._paginado(ref, "acessar")
        ps = self.tamanho_pagina
        if proc.tipo == "critical":
            return self._traduzir_critico(proc, endereco if endereco is not None else 0)
        if endereco is not None:
            if not 0 <= endereco < proc.tamanho:
                raise MemoriaError(f"[VIOLAÇÃO DE SEGMENTAÇÃO] Endereço lógico {endereco} fora do processo "
                                   f"(tamanho {proc.tamanho} Bytes)")
            pagina, offset = divmod(endereco, ps)
        if pagina is None:
            raise MemoriaError("informe --page N ou --address A")
        if not 0 <= pagina < proc.paginas:
            raise MemoriaError(f"[VIOLAÇÃO DE SEGMENTAÇÃO] Página Lógica {pagina} fora do espaço de "
                               f"endereçamento de '{proc.nome}' (páginas 0 a {proc.paginas - 1})")
        if offset is not None and (not 0 <= offset < ps or pagina * ps + offset >= proc.tamanho):
            raise MemoriaError("[VIOLAÇÃO DE SEGMENTAÇÃO] deslocamento fora da página/processo")
        pte = proc.tabela[pagina]
        print("[TRADUÇÃO DE ENDEREÇO: PÁGINA LÓGICA -> MOLDURA FÍSICA]")
        if pte.presente:
            print(f"Solicitação: Página Lógica {pagina} -> Mapeada na Moldura Física {pte.moldura} (RAM Dinâmica) [HIT]")
            frame, _ = self.referenciar(proc, pagina, escrita)
        else:
            print(f"Solicitação: Página Lógica {pagina} -> AUSENTE da RAM [MISS]")
            frame, _ = self.referenciar(proc, pagina, escrita, log=print)
            print(f"\nSolicitação: Página Lógica {pagina} -> Mapeada na Moldura Física {frame.idx} "
                  "(RAM Dinâmica) [PAGE FAULT RESOLVIDO]")
        if offset is not None:
            fisico = self.base_dinamica + frame.idx * ps + offset
            print(f"Endereço Lógico {pagina * ps + offset} = Página {pagina} + Deslocamento {offset} -> "
                  f"Endereço Físico {fisico} (0x{fisico:08X}) = Base da Dinâmica {mb(self.base_dinamica)} "
                  f"+ Moldura {frame.idx} × {ps} + {offset}")
        self._fim_operacao()

    def _traduzir_critico(self, proc, endereco):
        seg = proc.segmento
        print("[TRADUÇÃO DE ENDEREÇO: PROCESSO CRÍTICO - REGISTRADORES BASE/LIMITE]")
        if not 0 <= endereco < seg.tamanho:
            raise MemoriaError(f"[VIOLAÇÃO DE LIMITE] Endereço {endereco} fora do limite de {kb(seg.tamanho)} "
                               f"do processo '{proc.nome}'")
        print(f"Processo crítico '{proc.nome}': sem paginação (Base = {mb(seg.inicio)}, Limite = {kb(seg.tamanho)}).")
        print(f"Endereço Lógico {endereco} -> Endereço Físico {seg.inicio + endereco} "
              f"(0x{seg.inicio + endereco:08X}) = Base + {endereco}")
        self._fim_operacao()

    def executar_passo(self, ref, instrucao):
        proc = self._paginado(ref, "executar")
        escrita = bool(re.match(r"\s*(STORE|WRITE|SAVE|SET)", instrucao, re.I))
        print("[CPU - REFERÊNCIA DE MEMÓRIA EM EXECUÇÃO]")
        if proc.tipo == "critical":
            print(f"{proc.nome} (processo crítico) executa na Área Reservada: endereçamento contíguo "
                  "direto, sem paginação e sem page fault.")
            self._fim_operacao()
            return
        m = re.search(r"(?:p[áa]gina|page|pag)\.?\s*(?:l[óo]gica\s*)?#?(\d+)", instrucao, re.I)
        if not m:
            raise MemoriaError('instrução sem referência de página (ex.: "LOAD_DATA Página 2")')
        pagina = int(m[1])
        if not 0 <= pagina < proc.paginas:
            raise MemoriaError(f"[VIOLAÇÃO DE SEGMENTAÇÃO] Página Lógica {pagina} fora do espaço de "
                               f"endereçamento de '{proc.nome}' (páginas 0 a {proc.paginas - 1})")
        print(f"{proc.nome} requisita acesso à Página Lógica {pagina}...")
        pte = proc.tabela[pagina]
        if pte.presente:
            print(f"- Consulta Tabela de Páginas -> Página Lógica {pagina} presente na Moldura Física "
                  f"{pte.moldura} [HIT]")
        else:
            print(f"- Consulta Tabela de Páginas -> Página Lógica {pagina} NÃO ESTÁ NA RAM!")
        self.referenciar(proc, pagina, escrita, log=print)
        print(f"- Acesso de {'escrita' if escrita else 'leitura'} concluído (bit R=1"
              f"{', bit M=1' if escrita else ''}).")
        self._fim_operacao()

    def _percorrer(self, proc, endereco, n, escrita, buf=None):
        if proc.tipo == "critical":
            raise MemoriaError("processo crítico usa a Área Reservada (sem memória paginada)")
        if n < 0 or endereco < 0 or endereco + n > proc.tamanho:
            raise MemoriaError(f"[VIOLAÇÃO DE SEGMENTAÇÃO] acesso de {n} Bytes em {endereco} além do "
                               f"espaço de endereçamento (tamanho {proc.tamanho} Bytes)")
        ps, pos, saida, faltas = self.tamanho_pagina, 0, bytearray(), []
        while pos < n:
            pagina, off = divmod(endereco + pos, ps)
            take = min(n - pos, ps - off)
            frame, info = self.referenciar(proc, pagina, escrita)
            if escrita:
                frame.dados[off:off + take] = buf[pos:pos + take]
            else:
                saida += frame.dados[off:off + take]
            if not info["hit"]:
                v = info["vitima"]
                alvo = (f"vítima: Página {v[1]} do PID {v[0]} -> " + ("Swap" if v[2] else "descartada (limpa)")
                        if v else "moldura livre")
                faltas.append(f"[PAGE FAULT] Página {pagina} de '{proc.nome}' -> Moldura {info['moldura']} "
                              f"({info['origem']}; {alvo})")
            pos += take
        return bytes(saida), faltas

    def escrever(self, ref, endereco, dados):
        proc = self._paginado(ref, "escrever na memória d")
        _, faltas = self._percorrer(proc, endereco, len(dados), True, dados)
        self._fim_operacao()
        return faltas

    def ler(self, ref, endereco, n):
        proc = self._paginado(ref, "ler a memória d")
        dados, faltas = self._percorrer(proc, endereco, n, False)
        self._fim_operacao()
        return dados, faltas

    def compactar(self):
        self._exigir_root()
        print("[EXECUTANDO COMPACTAÇÃO DA ÁREA RESERVADA DA RAM]")
        print("Realocando blocos contíguos para eliminar a fragmentação externa...")
        movidos = self.reservada.compactar()
        for nome, antigo, novo, tam in movidos:
            print(f"- Mapeamento de '{nome}': [{mb(antigo)} a {mb(antigo + tam)}] -> "
                  f"Ajustado para: [{mb(novo)} a {mb(novo + tam)}]")
        if not movidos:
            print("- Nenhum bloco precisou ser movido: a área já está compactada.")
        print("\n--- MAPA APÓS COMPACTAÇÃO ---")
        print("\n".join(self.reservada.mapa_linhas()))
        print(f"Resultado: Fragmentação externa eliminada. Maior bloco contíguo: "
              f"{kb(self.reservada.maior_livre())}.")
        print(f"Dados movidos: {kb(sum(t for *_, t in movidos))} em {len(movidos)} bloco(s).")
        self._fim_operacao()

    # ------------------------------------------------------------ relatórios
    def status_reservada(self):
        print(self.reservada.titulo_mapa())
        print("\n".join(self.reservada.mapa_linhas()))
        print()
        print("\n".join(self.reservada.diagnostico_linhas()))

    def _paginados(self):
        return [p for p in self.procs.values() if p.tipo == "normal"]

    def status_virtual(self):
        ps, n = self.tamanho_pagina, self.n_frames
        usadas = sum(f.ocupada for f in self.frames)
        print(f"--- MEMÓRIA VIRTUAL (Região Dinâmica da RAM: {kb(self.dinamica)} = {n} molduras de {kb(ps)} "
              f"| Substituição: {self.sub.nome}) ---")
        letras = {}
        for p in self._paginados():
            letras[p.pid] = chr(ord("A") + len(letras) % 26)
        print("Mapa de molduras: [" + "".join(letras[f.pid] if f.ocupada else "." for f in self.frames[:64])
              + ("..." if n > 64 else "") + "]")
        for f in self.frames:
            if f.ocupada:
                nome = self.procs[f.pid].nome
                print(f"  Moldura {f.idx:>3}: {nome} (PID {f.pid}) - Página {f.pagina} "
                      f"[R={int(f.ref)} M={int(f.sujo)}] último acesso: {f.ultimo_acesso}ms")
        print(f"Molduras ocupadas: {usadas}/{n} ({pc(razao(usadas, n))}) | livres: {n - usadas}")
        if self._paginados():
            print("\nPID  Processo              Tamanho     Págs  Na RAM  No Swap  Faltas")
            for p in self._paginados():
                na_ram = sum(t.presente for t in p.tabela)
                no_swap = sum(self.swap.tem(p.pid, i) for i in range(p.paginas))
                print(f"{p.pid:<4} {p.nome:<21} {kb(p.tamanho):<11} {p.paginas:<5} {na_ram:<7} {no_swap:<8} {p.faltas}")
        print(f"\nSwap: {self.swap.usados}/{self.swap.n_slots} páginas em uso "
              f"({pc(razao(self.swap.usados, self.swap.n_slots))}) | {self.swap.gravacoes} gravações, "
              f"{self.swap.leituras} leituras")
        taxa = razao(self.hits, self.refs)
        eat = ((self.hits * T_REF) + (self.faltas * (T_REF + T_FALTA))) / self.refs if self.refs else 0
        print(f"Referências: {self.refs} | HITs: {self.hits} | Page Faults: {self.faltas} | "
              f"Substituições: {self.evicoes} | Taxa de acerto: {pc(taxa)} | "
              f"Tempo médio de acesso: {eat:.1f}ms")

    def status_resumo(self):
        r = self.reservada
        criticos = [p for p in self.procs.values() if p.tipo == "critical"]
        usadas = sum(f.ocupada for f in self.frames)
        print("=== ANÁLISE DE OCUPAÇÃO DA RAM CRÍTICA E DA MEMÓRIA VIRTUAL ===")
        print(f"RAM física: {mb_total(r.total)} (Área Reservada) + {kb(self.dinamica)} (Região Dinâmica)")
        print(f"\n[ÁREA RESERVADA - crítica, contígua, sem paginação]")
        print(f"  Ocupação: {kb(r.ocupado())} de {kb(r.total)} ({pc(razao(r.ocupado(), r.total))}) "
              f"{barra(r.ocupado(), r.total)}")
        print(f"  Núcleo base: {kb(r.segmentos[0].tamanho)} | processos críticos: {len(criticos)} "
              f"({kb(sum(p.segmento.tamanho for p in criticos))}) | livre: {kb(r.total_livre())} em "
              f"{len(r.livres())} lacuna(s), maior bloco {kb(r.maior_livre())} | política: {r.politica}-fit")
        print(f"\n[MEMÓRIA VIRTUAL - dinâmica, paginada ({self.sub.nome})]")
        print(f"  Molduras: {usadas}/{self.n_frames} ({pc(razao(usadas, self.n_frames))}) "
              f"{barra(usadas, self.n_frames)}")
        print(f"  Swap    : {self.swap.usados}/{self.swap.n_slots} páginas "
              f"({pc(razao(self.swap.usados, self.swap.n_slots))}) {barra(self.swap.usados, self.swap.n_slots)}")
        print(f"  Processos paginados: {len(self._paginados())} | Page Faults: {self.faltas} | "
              f"Taxa de acerto: {pc(razao(self.hits, self.refs))}")
        print("(use 'memory_status --reserved' ou '--virtual' para os detalhes)")

    def relatorio_fragmentacao(self):
        ps = self.tamanho_pagina
        print("[RELATÓRIO DE FRAGMENTAÇÃO]")
        print("Fragmentação INTERNA (memória virtual: páginas subutilizadas):")
        total = 0
        paginados = self._paginados()
        for p in paginados:
            perda = p.paginas * ps - p.tamanho
            total += perda
            ult = p.tamanho - (p.paginas - 1) * ps
            print(f"  {p.nome} (PID {p.pid}): {p.paginas} página(s), {perda} Bytes de desperdício "
                  f"(última página {pc(razao(ult, ps))} usada)")
        if not paginados:
            print("  Nenhum processo paginado.")
        print(f"  Total: {total} Bytes em {len(paginados)} processo(s).")
        print("Fragmentação EXTERNA (Área Reservada, alocação contígua):")
        print("\n".join("  " + l for l in self.reservada.diagnostico_linhas()[1:]))

    def tabela_paginas(self, ref):
        proc = self._paginado(ref, "consultar")
        if proc.tipo == "critical":
            raise MemoriaError("processo crítico não tem tabela de páginas (alocação contígua)")
        print(f"--- TABELA DE PÁGINAS: {proc.nome} (PID {proc.pid}) ---")
        print("Pág | Estado                      | Moldura | R | M")
        for i, pte in enumerate(proc.tabela):
            if pte.presente:
                f = self.frames[pte.moldura]
                print(f"{i:>3} | PRESENTE                    | {pte.moldura:>7} | {int(f.ref)} | {int(f.sujo)}")
            else:
                estado = "AUSENTE (cópia no Swap)" if self.swap.tem(proc.pid, i) else "AUSENTE (nunca carregada)"
                print(f"{i:>3} | {estado:<27} | {'-':>7} | - | -")

    def _fim_operacao(self):
        self.ops += 1
        if self.intervalo and self.ops % self.intervalo == 0:
            print(f"\n[RELATÓRIO PERIÓDICO - após {self.ops} operações de memória]")
            self.relatorio_fragmentacao()
