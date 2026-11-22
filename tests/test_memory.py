import contextlib
import io as stdio
import random
import unittest

from so_simulator import fs as modulo_fs
from so_simulator import io as modulo_io
from so_simulator import memory as modulo_mem
from so_simulator.memory import MemoriaError, ReservedArea, simular
from so_simulator.memory.util import KB, MB
from so_simulator.security.security import SecurityManager, registrar as registrar_seg
from so_simulator.shell import Shell


def montar(dinamica=16 * KB, algo="lru", swap_kb=256, swap_dev="hdd", swap_raid="none", n_discos=None,
           intervalo=0, politica="first"):
    es = modulo_io.criar_io(4096, 16)
    fs = modulo_fs.criar_filesystem("indexada", 4096, 16, dispositivo=es)
    sh = Shell()
    modulo_fs.registrar(sh, fs)
    modulo_io.registrar(sh, es, fs)
    sec = SecurityManager(fs)
    registrar_seg(sh, sec)
    swap_io = modulo_io.criar_io(4096, swap_kb // 4, swap_dev, swap_raid, n_discos)
    swap_io.etiqueta, swap_io.origem = "E/S - SWAP", "Gerenciador de Memória (swap)"
    mm = modulo_mem.MemoryManager(swap_io, 4 * MB, 1 * MB, dinamica, 4096, algo, politica, sec, intervalo)
    modulo_mem.registrar(sh, mm, fs)
    return mm, sh, sec


def rodar(sh, *linhas):
    buf = stdio.StringIO()
    with contextlib.redirect_stdout(buf):
        for l in linhas:
            sh.executar(l)
    return buf.getvalue()


class TestAreaReservada(unittest.TestCase):
    def test_exemplo_4_1(self):
        mm, sh, _ = montar()
        out = rodar(sh,
                    'create_process --name "Kernel_DiskDriver" --size 512KB --type critical',
                    'create_process --name "Kernel_NetDriver" --size 1024KB --type critical',
                    'create_process --name "Kernel_CryptoEngine" --size 512KB --type critical')
        self.assertIn("[1.0 MB até 1.5 MB] (512 KB)", out)
        self.assertIn("[1.5 MB até 2.5 MB] (1024 KB)", out)
        self.assertIn("[2.5 MB até 3.0 MB] (512 KB)", out)
        out = rodar(sh, 'terminate_process --name "Kernel_NetDriver"', "memory_status --reserved")
        self.assertIn("Área de 1024 KB no intervalo [1.5 MB a 2.5 MB] liberada", out)
        self.assertIn("FRAGMENTO LIVRE 1", out)
        self.assertIn("FRAGMENTO LIVRE 2", out)
        self.assertIn("Total de Memória Livre na Área Reservada : 2048 KB (2.0 MB)", out)
        self.assertIn("Maior Bloco Contíguo Disponível          : 1024 KB (1.0 MB)", out)
        self.assertIn("SIM (2 fragmentos não contíguos)", out)
        self.assertIn("requisição contígua de 1536 KB falhará", out)
        out = rodar(sh, 'create_process --name "Grande" --size 1536KB --type critical')
        self.assertIn("fragmentação externa", out)
        out = rodar(sh, "memory_compact --reserved")
        self.assertIn("[2.5 MB a 3.0 MB] -> Ajustado para: [1.5 MB a 2.0 MB]", out)
        self.assertIn("[2.0 MB - 4.0 MB] : Bloco Livre Contíguo (2048 KB) [LIVRE]", out)
        self.assertIn("Maior bloco contíguo: 2048 KB", out)
        out = rodar(sh, 'create_process --name "Grande" --size 1536KB --type critical')
        self.assertIn("[2.0 MB até 3.5 MB]", out)

    def test_politicas_first_best_worst(self):
        # duas lacunas: [1.0,1.25) = 256 KB e [1.75,4.0) = 2304 KB
        r = ReservedArea(4 * MB, 1 * MB, "best")
        r.alocar("a", 1, 256 * KB); r.alocar("b", 2, 512 * KB); r.alocar("c", 3, 256 * KB)
        r.liberar(1); r.liberar(3)           # lacunas: [1.0,1.25) 256KB e [1.75,4.0) 2304KB
        self.assertEqual(r.alocar("x", 9, 200 * KB).inicio, 1 * MB)      # best-fit: a menor que serve
        r2 = ReservedArea(4 * MB, 1 * MB, "worst")
        r2.alocar("a", 1, 256 * KB); r2.alocar("b", 2, 512 * KB); r2.alocar("c", 3, 256 * KB)
        r2.liberar(1); r2.liberar(3)
        self.assertEqual(r2.alocar("x", 9, 200 * KB).inicio, 1792 * KB)  # worst-fit: a maior

    def test_nucleo_nao_e_movido_e_area_cheia(self):
        r = ReservedArea(2 * MB, 1 * MB)
        r.alocar("a", 1, 512 * KB)
        r.alocar("b", 2, 512 * KB)
        with self.assertRaises(MemoriaError):
            r.alocar("c", 3, 1 * KB)
        self.assertEqual(r.compactar(), [])
        self.assertEqual(r.segmentos[0].inicio, 0)


class TestPaginacao(unittest.TestCase):
    def test_exemplo_4_2_criacao(self):
        mm, sh, _ = montar()
        out = rodar(sh, 'create_process --name "App_Navegador" --size 10KB --type normal')
        self.assertIn("10 KB / 4 KB = 2.5 -> Requer 3 Páginas Lógicas (Páginas 0, 1 e 2).", out)
        self.assertIn("Página Lógica 2 : 2048 Bytes ocupados (50% de uso)", out)
        self.assertIn("Fragmentação Interna Total: 2048 Bytes de desperdício na Página Lógica 2.", out)
        self.assertIn("sem alocar molduras na RAM (Alocação por demanda)", out)
        self.assertEqual(sum(f.ocupada for f in mm.frames), 0)

    def test_page_fault_lru_e_traducao(self):
        mm, sh, _ = montar(dinamica=12 * KB)
        rodar(sh, "create_process --name A --size 8KB", "create_process --name B --size 12KB")
        rodar(sh, "execute_step --process A --instruction 'LOAD_DATA Página 0'",
              "execute_step --process A --instruction 'LOAD_DATA Página 1'",
              "execute_step --process B --instruction 'LOAD_DATA Página 0'")
        out = rodar(sh, "translate_address --process A --page 1 --offset 10")
        self.assertIn("[HIT]", out)
        self.assertIn("Endereço Físico", out)
        out = rodar(sh, "execute_step --process B --instruction 'LOAD_DATA Página 2'")
        self.assertIn("Memória RAM cheia. Executando Substituição de Página (Algoritmo LRU):", out)
        self.assertIn("Página Lógica 0 do PID 100 não é usada há", out)       # A0 é a mais antiga (A1 foi traduzida)
        self.assertIn("Removendo Página Lógica 0 do PID 100 da RAM -> Salvando no Swap", out)
        self.assertIn("[E/S - SWAP]", out)                                       # swap passa pela camada de E/S
        self.assertFalse(mm.buscar("A").tabela[0].presente)
        self.assertTrue(mm.swap.tem(100, 0))
        # A página volta do swap (leitura de disco) quando A a acessa de novo
        out = rodar(sh, "execute_step --process A --instruction 'LOAD_DATA Página 0'")
        self.assertIn("Lendo Página Lógica 0 do Swap", out)

    def test_pagina_limpa_nao_regrava_swap(self):
        mm, sh, _ = montar(dinamica=4 * KB)
        rodar(sh, "create_process --name A --size 8KB")
        rodar(sh, "execute_step --process A --instruction 'LOAD Página 0'",
              "execute_step --process A --instruction 'LOAD Página 1'")      # A0 -> swap (1ª vez)
        g = mm.swap.gravacoes
        rodar(sh, "execute_step --process A --instruction 'LOAD Página 0'")  # A1 limpa? nunca foi gravada -> grava
        self.assertEqual(mm.swap.gravacoes, g + 1)
        out = rodar(sh, "execute_step --process A --instruction 'LOAD Página 1'")  # A0 voltou limpa e tem cópia
        self.assertIn("nenhuma escrita em disco", out)
        self.assertEqual(mm.swap.gravacoes, g + 1)

    def test_violacoes_de_segmentacao(self):
        mm, sh, _ = montar()
        rodar(sh, "create_process --name A --size 5KB")
        self.assertIn("VIOLAÇÃO DE SEGMENTAÇÃO", rodar(sh, "translate_address --process A --page 2"))
        self.assertIn("VIOLAÇÃO DE SEGMENTAÇÃO", rodar(sh, "mem_read --process A --address 5000 --length 1000"))
        self.assertIn("instrução sem referência", rodar(sh, "execute_step --process A --instruction NOP"))

    def test_encerrar_libera_molduras_e_swap(self):
        mm, sh, _ = montar(dinamica=8 * KB, swap_dev="ssd")
        rodar(sh, "create_process --name A --size 16KB")
        for p in range(4):
            rodar(sh, f"execute_step --process A --instruction 'STORE Página {p}'")
        self.assertGreater(mm.swap.usados, 0)
        out = rodar(sh, "terminate_process --name A")
        self.assertIn("Liberadas 2 moldura(s)", out)
        self.assertEqual(mm.swap.usados, 0)
        self.assertEqual(sum(f.ocupada for f in mm.frames), 0)

    def test_swap_cheio_nao_corrompe_estado(self):
        mm, sh, _ = montar(dinamica=4 * KB, swap_kb=4)
        rodar(sh, "create_process --name A --size 8KB", "create_process --name B --size 4KB")
        rodar(sh, "execute_step --process A --instruction 'STORE Página 0'",
              "execute_step --process A --instruction 'STORE Página 1'")
        out = rodar(sh, "execute_step --process B --instruction 'LOAD Página 0'")
        self.assertIn("Swap cheio", out)
        self.assertTrue(mm.buscar("A").tabela[1].presente)
        self.assertFalse(mm.buscar("B").tabela[0].presente)

    def test_processo_maior_que_memoria_virtual(self):
        mm, sh, _ = montar(dinamica=8 * KB, swap_kb=8)
        self.assertIn("não cabe na memória virtual", rodar(sh, "create_process --name A --size 100KB"))

    def test_relatorio_periodico(self):
        mm, sh, _ = montar(intervalo=2)
        out = rodar(sh, "create_process --name A --size 5KB", "create_process --name B --size 1KB")
        self.assertIn("[RELATÓRIO PERIÓDICO - após 2 operações de memória]", out)
        self.assertIn("A (PID 100): 2 página(s), 3072 Bytes de desperdício", out)
        self.assertIn("Fragmentação EXTERNA", out)


class TestAlgoritmos(unittest.TestCase):
    REFS = [7, 0, 1, 2, 0, 3, 0, 4, 2, 3, 0, 3, 2, 1, 2, 0, 1, 7, 0, 1]

    def test_cadeia_classica(self):
        self.assertEqual(simular(self.REFS, 3, "fifo").faltas, 15)
        self.assertEqual(simular(self.REFS, 3, "lru").faltas, 12)
        self.assertEqual(simular(self.REFS, 3, "optimal").faltas, 9)
        for a in ("second_chance", "lfu", "nru"):
            self.assertLessEqual(simular(self.REFS, 3, a).faltas, 15)
            self.assertGreaterEqual(simular(self.REFS, 3, a).faltas, 9)

    def test_anomalia_de_belady_no_fifo(self):
        refs = [1, 2, 3, 4, 1, 2, 5, 1, 2, 3, 4, 5]
        self.assertEqual(simular(refs, 3, "fifo").faltas, 9)
        self.assertEqual(simular(refs, 4, "fifo").faltas, 10)
        self.assertLessEqual(simular(refs, 4, "lru").faltas, simular(refs, 3, "lru").faltas)

    def test_segunda_chance_poupa_pagina_referenciada(self):
        mm, sh, _ = montar(dinamica=8 * KB, algo="second_chance")
        rodar(sh, "create_process --name A --size 12KB")
        for p in (0, 1):
            rodar(sh, f"execute_step --process A --instruction 'LOAD Página {p}'")
        mm.frames[0].ref = False                                   # A0 sem bit R; A1 com R=1
        mm.frames[1].ref = True
        out = rodar(sh, "execute_step --process A --instruction 'LOAD Página 2'")
        self.assertIn("bit R=0 -> escolhida como vítima", out)
        self.assertFalse(mm.buscar("A").tabela[0].presente)
        self.assertTrue(mm.buscar("A").tabela[1].presente)

    def test_comando_page_sim(self):
        mm, sh, _ = montar()
        out = rodar(sh, "page_sim --refs 7,0,1,2,0,3,0,4,2,3,0,3,2,1,2,0,1,7,0,1 --frames 3 --algo all")
        self.assertIn("Total FIFO: 15 page faults", out)
        self.assertIn("Total LRU: 12 page faults", out)
        self.assertIn("Total OPTIMAL: 9 page faults", out)

    def test_integridade_dos_dados_com_swap(self):
        """Escreve/lê endereços aleatórios com pouca RAM: o conteúdo tem de sobreviver ao swap."""
        configs = [("hdd", "none", None), ("ssd", "none", None), ("hdd", "5", 3), ("hdd", "1", 2)]
        for algo in ("fifo", "lru", "second_chance", "lfu", "nru"):
            for dev, raid, n in configs[:2] if algo != "lru" else configs:
                mm, sh, _ = montar(dinamica=16 * KB, algo=algo, swap_kb=256, swap_dev=dev,
                                   swap_raid=raid, n_discos=n)
                rng = random.Random(hash((algo, dev, raid)) & 0xFFFF)
                tamanhos = {"P1": 24 * KB, "P2": 17000, "P3": 9 * KB}
                modelo = {}
                with contextlib.redirect_stdout(stdio.StringIO()):
                    for nome, t in tamanhos.items():
                        mm.criar_processo(nome, t)
                        modelo[nome] = bytearray(t)
                    for _ in range(250):
                        nome = rng.choice(list(tamanhos))
                        t = tamanhos[nome]
                        ini = rng.randrange(t)
                        n_b = rng.randint(1, min(6000, t - ini))
                        if rng.random() < 0.5:
                            dados = bytes(rng.randrange(256) for _ in range(n_b))
                            mm.escrever(nome, ini, dados)
                            modelo[nome][ini:ini + n_b] = dados
                        else:
                            lido, _ = mm.ler(nome, ini, n_b)
                            self.assertEqual(lido, bytes(modelo[nome][ini:ini + n_b]), (algo, dev, raid))
                        mm.swap_io.flush(silencioso=True)
                    for nome, t in tamanhos.items():
                        lido, _ = mm.ler(nome, 0, t)
                        self.assertEqual(lido, bytes(modelo[nome]), (algo, dev, raid))
                self.assertGreater(mm.evicoes, 0)


class TestSeguranca(unittest.TestCase):
    def test_usuario_comum_nao_cria_processo_critico(self):
        mm, sh, sec = montar()
        rodar(sh, "useradd carlos Senha123 devs", "logout", "login --user carlos --password Senha123")
        out = rodar(sh, 'create_process --name Driver --size 64KB --type critical', "memory_compact --reserved")
        self.assertEqual(out.count("MODO_KERNEL"), 2)
        self.assertEqual(len(mm.procs), 0)

    def test_isolamento_entre_usuarios_e_auditoria(self):
        mm, sh, sec = montar()
        rodar(sh, "useradd ana Senha123 devs", "useradd bia Senha456 devs", "logout",
              "login --user ana --password Senha123")
        rodar(sh, "create_process --name AppAna --size 8KB")
        rodar(sh, "mem_write --process AppAna --address 0 --text segredo")
        rodar(sh, "logout", "login --user bia --password Senha456")
        for cmd in ("mem_read --process AppAna --address 0 --length 7",
                    "terminate_process --name AppAna",
                    "translate_address --process AppAna --page 0",
                    "execute_step --process AppAna --instruction 'LOAD Página 0'"):
            self.assertIn("NEGADO", rodar(sh, cmd), cmd)
        self.assertTrue(any("MEMORIA_NEGADA" in e for _, e in sec.audit_log))
        self.assertIn("AppAna", [p.nome for p in mm.procs.values()])
        rodar(sh, "logout")                                         # root pode tudo
        self.assertIn("segredo", rodar(sh, "mem_read --process AppAna --address 0 --length 7"))


if __name__ == "__main__":
    unittest.main()
