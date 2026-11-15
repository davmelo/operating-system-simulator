import contextlib
import io as stdio
import random
import unittest

from so_simulator import fs as modulo_fs
from so_simulator import io as modulo_io
from so_simulator.io import RAIDArray, SSD, xor_blocks
from so_simulator.io.erros import ArranjoInoperante, IOSimError
from so_simulator.io.scheduler import escalonar
from so_simulator.shell import Shell

TR = [98, 183, 37, 122, 14]


def montar(**kw):
    es = modulo_io.criar_io(4096, kw.pop("blocos", 16), **kw)
    sistema = modulo_fs.criar_filesystem("indexada", 4096, es.n_blocks, dispositivo=es)
    shell = Shell()
    modulo_fs.registrar(shell, sistema)
    modulo_io.registrar(shell, es, sistema)
    return es, sistema, shell


def rodar(shell, *linhas):
    buf = stdio.StringIO()
    with contextlib.redirect_stdout(buf):
        for l in linhas:
            shell.executar(l)
    return buf.getvalue()


class TestEscalonador(unittest.TestCase):
    def test_exemplo_do_enunciado(self):
        p = escalonar("sstf", 53, TR, 199)
        self.assertEqual([t for t, _ in p.passos], [53, 37, 14, 98, 122, 183])
        self.assertEqual(p.trechos, [16, 23, 84, 24, 61])
        self.assertEqual(p.total, 208)

    def test_totais_dos_outros_algoritmos(self):
        esperado = {"fcfs": 469, "scan": 331, "look": 299, "cscan": 382, "clook": 322}
        for algo, total in esperado.items():
            self.assertEqual(escalonar(algo, 53, TR, 199).total, total, algo)

    def test_scan_para_baixo_e_sem_requisicoes(self):
        p = escalonar("scan", 53, TR, 199, "down")
        self.assertEqual(p.atendimento, [37, 14, 98, 122, 183])
        self.assertEqual(p.total, 53 + 183)          # até 0 e depois até 183


class TestRAID(unittest.TestCase):
    def dados(self, n):
        return random.Random(n).randbytes(4096)

    def test_mapeamento_raid5_rotaciona_paridade(self):
        r = RAIDArray("5", 3, 4096, 16)
        self.assertEqual([r.disco_paridade(f) for f in range(4)], [2, 1, 0, 2])
        self.assertEqual(RAIDArray("4", 4, 4096, 16).disco_paridade(7), 3)

    def test_faixa_completa_nao_le_e_paridade_e_xor(self):
        r = RAIDArray("5", 3, 4096, 16)
        a, b = self.dados(1), self.dados(2)
        r.write_batch({0: a, 1: b})
        self.assertEqual(sum(d.leituras for d in r.disks), 0)
        self.assertEqual(r.disks[2].peek(0), xor_blocks(a, b))
        self.assertTrue(r.verificar()[0])

    def test_escrita_parcial_rmw_mantem_paridade(self):
        r = RAIDArray("5", 3, 4096, 16)
        r.write_batch({0: self.dados(1), 1: self.dados(2)})
        r.write_batch({1: self.dados(3)})                       # parcial
        self.assertEqual(sum(d.leituras for d in r.disks), 2)   # lê D antigo e P antiga
        self.assertTrue(r.verificar()[0])

    def test_raid5_falha_leitura_degradada_e_rebuild(self):
        for falho in (1, 2, 3):
            r = RAIDArray("5", 3, 4096, 16)
            ref = {l: self.dados(l) for l in range(16)}
            r.write_batch(ref)
            r.fail(falho)
            for l, d in ref.items():                            # leitura degradada correta
                self.assertEqual(r.read(l), d)
            r.write_batch({3: self.dados(99)})                  # escrita degradada
            ref[3] = self.dados(99)
            for l, d in ref.items():
                self.assertEqual(r.read(l), d)
            r.rebuild(falho)
            self.assertTrue(r.verificar()[0])
            for l, d in ref.items():
                self.assertEqual(r.read(l), d)

    def test_raid4_e_raid5_com_4_discos_aleatorio(self):
        for nivel in ("4", "5"):
            rng = random.Random(7)
            r = RAIDArray(nivel, 4, 4096, 15)
            ref = {}
            for _ in range(60):
                lote = {rng.randrange(15): self.dados(rng.randrange(1000)) for _ in range(rng.randint(1, 5))}
                r.write_batch(lote)
                ref.update(lote)
            self.assertTrue(r.verificar()[0])
            r.fail(2)
            for l, d in ref.items():
                self.assertEqual(r.read(l), d)
            r.rebuild(2)
            self.assertTrue(r.verificar()[0])

    def test_segunda_falha_torna_raid5_inoperante(self):
        r = RAIDArray("5", 3, 4096, 16)
        r.write_batch({0: self.dados(1), 1: self.dados(2)})
        r.fail(1)
        r.fail(2)
        with self.assertRaises(ArranjoInoperante):
            r.read(0)

    def test_raid0_perde_dados_e_raid1_sobrevive(self):
        r0 = RAIDArray("0", 2, 4096, 16)
        r0.write_batch({0: self.dados(1), 1: self.dados(2)})
        r0.fail(1)
        with self.assertRaises(ArranjoInoperante):
            r0.read(0)
        self.assertEqual(r0.read(1), self.dados(2))             # o outro disco segue vivo
        r1 = RAIDArray("1", 2, 4096, 16)
        r1.write_batch({0: self.dados(1)})
        r1.fail(1)
        self.assertEqual(r1.read(0), self.dados(1))
        r1.rebuild(1)
        self.assertTrue(r1.verificar()[0])

    def test_teste_de_falha_raid5(self):
        r = RAIDArray("5", 3, 4096, 16)
        r.write_batch({0: self.dados(1), 1: self.dados(2)})
        linhas, ok = r.teste_falha(1)
        self.assertTrue(ok)
        self.assertIn("(Disco 1 XOR Disco 3)", linhas[1])
        self.assertFalse(r.degradado)

    def test_minimo_de_discos(self):
        with self.assertRaises(IOSimError):
            RAIDArray("5", 2, 4096, 16)


class TestSSD(unittest.TestCase):
    def test_integridade_com_gc(self):
        rng = random.Random(3)
        for modo in ("none", "dynamic", "static"):
            s = SSD(16, 4096, 4, wear_leveling=modo)
            ref = {}
            for _ in range(3000):
                l = rng.randrange(16)
                ref[l] = bytes([rng.randrange(256)]) * 8
                s.write(l, ref[l])
                if rng.random() < 0.05 and ref:
                    t = rng.choice(list(ref))
                    s.trim(t)
                    del ref[t]
                s.eventos.clear()
            for l, d in ref.items():
                self.assertEqual(s.read(l).rstrip(b"\0"), d, modo)
            self.assertEqual(len(s.mapa), len(ref))

    def test_wear_leveling_reduz_diferenca(self):
        res = {}
        for modo in ("none", "static"):
            s = SSD(16, 4096, 4, wear_leveling=modo)
            for l in range(12):
                s.write(l, b"frio")
            for i in range(1500):
                s.write(12 + i % 4, b"quente")
            res[modo] = max(s.apagamentos) - min(s.apagamentos)
        self.assertLess(res["static"], res["none"] // 4)


class TestIntegracao(unittest.TestCase):
    def test_escrita_no_fs_gera_distribuicao_raid5(self):
        es, fs, sh = montar(raid="5", n_discos=3, algo="sstf")
        out = rodar(sh, "mkdir /dados", "write_file /dados/planilha.csv --size 8KB --teste-falha")
        self.assertIn("Arranjo Ativo: RAID 5 (3 Discos Físicos, Blocos de 4 KB)", out)
        self.assertIn("[PARIDADE : XOR(Bloco 02, Bloco 03)]", out)
        self.assertIn("Reconstrução [OK]", out)
        self.assertEqual(len(fs.read_file("/dados/planilha.csv")), 8192)

    def test_escalonamento_aparece_no_comando(self):
        es, fs, sh = montar(algo="sstf")
        out = rodar(sh, "io_sched --tracks 98,183,37,122,14 --head 53 --algo sstf")
        self.assertIn("Ordem de Atendimento : 53 -> 37 -> 14 -> 98 -> 122 -> 183", out)
        self.assertIn("16 + 23 + 84 + 24 + 61 = 208 trilhas", out)

    def test_dados_sobrevivem_a_troca_de_dispositivo(self):
        es, fs, sh = montar(algo="scan")
        rodar(sh, "mkdir /a", 'write_file /a/x.txt "ola mundo"', "write_file /a/y.bin --size 9KB")
        y = fs.read_file("/a/y.bin")
        for cmd in ("raid_mode 5 --disks 4", "raid_mode 1", "io_device ssd", "raid_mode 0 --disks 3", "io_device hdd"):
            rodar(sh, cmd)
            self.assertEqual(fs.read_file("/a/x.txt"), b"ola mundo", cmd)
            self.assertEqual(fs.read_file("/a/y.bin"), y, cmd)

    def test_fs_sobrevive_a_falha_de_disco_no_raid5(self):
        es, fs, sh = montar(raid="5", n_discos=3)
        rodar(sh, "mkdir /d", "write_file /d/a --size 12KB", 'write_file /d/b "texto"')
        a, b = fs.read_file("/d/a"), fs.read_file("/d/b")
        rodar(sh, "ls /d")
        out = rodar(sh, "raid_fail 2", "read_file /d/a", "raid_rebuild 2", "raid_check")
        self.assertIn("Leitura degradada", out)
        self.assertIn("[OK]", out)
        self.assertEqual((fs.read_file("/d/a"), fs.read_file("/d/b")), (a, b))

    def test_fs_no_ssd_e_remocao_vira_trim(self):
        es, fs, sh = montar(device="ssd")
        out = rodar(sh, "mkdir /s", "write_file /s/f --size 8KB", "rm /s/f", "ssd_status")
        self.assertIn("SSD não tem braço", out)
        self.assertIn("TRIM", out)
        self.assertIn("amplificação", out)

    def test_erro_de_io_aparece_como_erro_do_shell(self):
        es, fs, sh = montar(raid="0", n_discos=2)
        out = rodar(sh, "write_file /f --size 4KB", "raid_fail 1", "raid_fail 2", "cat /f")
        self.assertIn("erro:", out)


if __name__ == "__main__":
    unittest.main()
