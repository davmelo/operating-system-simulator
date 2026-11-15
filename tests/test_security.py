import contextlib
import io
import unittest

from so_simulator import fs as modulo_fs
from so_simulator import io as modulo_io
from so_simulator.security.security import SecurityManager, registrar
from so_simulator.shell import Shell


class TestSecurity(unittest.TestCase):
    def setUp(self):
        es = modulo_io.criar_io(4096, 16)
        self.es = es
        self.fs = modulo_fs.criar_filesystem("indexada", 4096, 16, dispositivo=es)
        self.sh = Shell()
        modulo_fs.registrar(self.sh, self.fs)
        modulo_io.registrar(self.sh, es, self.fs)
        self.sec = SecurityManager(self.fs)
        registrar(self.sh, self.sec)
        self.fs.create("/segredo.txt")
        self.fs.write_file("/segredo.txt", b"conteudo confidencial")
        self.es.flush(silencioso=True)

    def run_cmd(self, *cmds):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            for cmd in cmds:
                self.sh.executar(cmd)
        return out.getvalue()

    def test_password_hash_and_login(self):
        self.run_cmd("useradd carlos Senha123 devs", "logout")
        self.run_cmd("login --user carlos --password Senha123")
        self.assertEqual(self.sec.current_user, "carlos")
        self.assertEqual(self.sec.mode, "MODO_USUARIO")
        self.assertNotIn("Senha123", repr(self.sec.users["carlos"]))

    def test_acl_denies_and_logs(self):
        self.run_cmd("useradd carlos Senha123 devs", "chmod /segredo.txt 640", "logout",
                     "login --user carlos --password Senha123")
        out = self.run_cmd("cat /segredo.txt")
        self.assertIn("NEGADO", out)
        self.assertTrue(any("ACL_NEGADA" in event for _, event in self.sec.audit_log))

    def test_encryption_requires_key_and_never_stores_it(self):
        self.run_cmd("encrypt_file /segredo.txt --key chave-secreta")
        node = self.fs.resolve("/segredo.txt")
        raw = self.fs._ler(node)
        self.assertNotIn(b"conteudo confidencial", raw)
        self.assertNotIn("key", node.crypto)
        with self.assertRaisesRegex(Exception, "Informe a chave"):
            self.fs.read_file("/segredo.txt")
        self.assertEqual(self.fs.read_file("/segredo.txt", key="chave-secreta"),
                         b"conteudo confidencial")
        with self.assertRaisesRegex(Exception, "chave incorreta"):
            self.fs.read_file("/segredo.txt", key="errada")

    def test_encrypted_read_command_requires_and_checks_key(self):
        self.run_cmd("encrypt_file /segredo.txt --key chave-secreta")
        out = self.run_cmd("read_file /segredo.txt")
        self.assertIn("Informe a chave", out)
        out = self.run_cmd("read_file /segredo.txt --key errada")
        self.assertIn("chave incorreta", out)
        out = self.run_cmd("read_file /segredo.txt --key chave-secreta")
        self.assertIn("conteudo confidencial", out)

    def test_encrypted_file_is_readable_by_authorized_user_only_with_key(self):
        self.run_cmd("encrypt_file /segredo.txt --key chave-secreta", "useradd carlos Senha123 devs",
                     "logout", "login --user carlos --password Senha123")
        out = self.run_cmd("read_file /segredo.txt")
        self.assertIn("Informe a chave", out)
        out = self.run_cmd("read_file /segredo.txt --key chave-secreta")
        self.assertIn("conteudo confidencial", out)

    def test_encrypted_append_requires_key_and_preserves_ciphertext(self):
        self.run_cmd("encrypt_file /segredo.txt --key chave-secreta")
        with self.assertRaisesRegex(Exception, "Informe a chave"):
            self.fs.append_file("/segredo.txt", b" adicional")
        self.fs.append_file("/segredo.txt", b" adicional", key="chave-secreta")
        self.assertEqual(self.fs.read_file("/segredo.txt", key="chave-secreta"),
                         b"conteudo confidencial adicional")

    def test_decrypt_file_removes_encryption_and_restores_plaintext(self):
        self.run_cmd("encrypt_file /segredo.txt --key chave-secreta")
        out = self.run_cmd("decrypt_file /segredo.txt --key chave-secreta")
        self.assertIn("descriptografado permanentemente", out)
        self.assertIsNone(self.fs.resolve("/segredo.txt").crypto)
        self.assertEqual(self.fs.read_file("/segredo.txt"), b"conteudo confidencial")

    def test_antivirus_reports_encrypted_file_without_claiming_mac_validation(self):
        self.run_cmd("encrypt_file /segredo.txt --key chave-secreta")
        out = self.run_cmd("antivirus_scan /")
        self.assertIn("Criptografado; blocos íntegros", out)
        self.assertIn("MAC/assinaturas não verificados sem chave", out)

    def test_integrity_detects_physical_tampering(self):
        node = self.fs.resolve("/segredo.txt")
        block = node.blocks[0]
        # Escrita direta no backend contorna o FS e simula alteração não autorizada.
        self.fs.dev.backend.disks[0].write(block, b"X" * self.fs.dev.block_size)
        out = self.run_cmd("antivirus_scan /")
        self.assertIn("hash do bloco difere", out)

    def test_user_cannot_change_global_storage(self):
        self.run_cmd("useradd carlos Senha123 devs", "logout",
                     "login --user carlos --password Senha123")
        out = self.run_cmd("io_device ssd")
        self.assertIn("MODO_KERNEL", out)

    def test_user_home_allows_create_write_and_read(self):
        self.run_cmd("useradd carlos Senha123 devs", "logout",
                     "login --user carlos --password Senha123")
        out = self.run_cmd("mkdir /home/carlos/docs",
                           "write_file /home/carlos/docs/nota.txt --text 'ola carlos'",
                           "read_file /home/carlos/docs/nota.txt")
        self.assertIn("ola carlos", out)
        node = self.fs.resolve("/home/carlos/docs/nota.txt")
        self.assertEqual(node.owner, "carlos")
        self.assertEqual(node.group, "devs")

    def test_read_file_reports_empty_file(self):
        out = self.run_cmd("read_file /segredo.txt")
        self.assertIn("Leitura concluída", out)
        self.assertIn("conteudo confidencial", out)


if __name__ == "__main__":
    unittest.main()
