"""Segurança e proteção: identidade, privilégios, ACL, criptografia e auditoria.

A criptografia é uma simulação didática (XOR com fluxo derivado de SHA-256 + HMAC).
Não deve ser usada para proteger dados reais; em produção use AES-GCM/ChaCha20-Poly1305.
"""
import hashlib
import hmac
import os
import time

from ..fs.erros import FSError
from ..fs.node import Node


class SecurityError(FSError):
    pass


class SecurityManager:
    def __init__(self, fs):
        self.fs = fs
        self.groups = {"root": {"root"}, "admin": {"root"}, "users": {"root"}}
        self.users = {}
        self.current_user = "root"
        self.mode = "MODO_KERNEL"
        self.audit_log = []
        self.signatures = (b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE", b"MALWARE_SIMULADO")
        self._add_user("root", "root", "admin", admin=True)

        # O grupo primário do superusuário é admin.
        fs.root.group = "admin"
        fs.user = "root"
        fs.guard = self.authorize
        fs.security = self

    @staticmethod
    def _password(password, salt=None):
        salt = salt or os.urandom(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
        return salt, digest

    def _add_user(self, username, password, group="users", admin=False):
        if username in self.users:
            raise SecurityError(f"usuário já existe: {username}")
        self.groups.setdefault(group, set())
        salt, digest = self._password(password)
        self.users[username] = {"salt": salt, "hash": digest, "group": group, "admin": admin}
        self.groups[group].add(username)

    def add_user(self, username, password, group="users"):
        self.require_root()
        self._add_user(username, password, group)
        # Cada usuário recebe /home/<usuário>, pertencente a ele e ao seu grupo.
        # /home é compartilhado, mas não pode ser alterado por usuários comuns.
        try:
            home_root = self.fs.resolve("/home")
        except FSError:
            home_root = Node("home", True, self.fs.root, owner="root", perms=0o755)
            home_root.group = "admin"
            self.fs.root.children["home"] = home_root
        if not home_root.is_dir:
            raise SecurityError("/home existe, mas não é um diretório")
        home = Node(username, True, home_root, owner=username, perms=0o750)
        home.group = group
        home_root.children[username] = home
        print(f"Usuário '{username}' cadastrado no grupo '{group}'. Diretório pessoal: /home/{username}")

    def add_group(self, group):
        self.require_root()
        if group in self.groups:
            raise SecurityError(f"grupo já existe: {group}")
        self.groups[group] = set()
        print(f"Grupo '{group}' criado.")

    def login(self, username, password):
        u = self.users.get(username)
        if not u:
            self._log(f"LOGIN_NEGADO usuário inexistente: {username}")
            raise SecurityError("usuário ou senha inválidos")
        _, digest = self._password(password, u["salt"])
        if not hmac.compare_digest(digest, u["hash"]):
            self._log(f"LOGIN_NEGADO usuário: {username}")
            raise SecurityError("usuário ou senha inválidos")
        self.current_user = username
        self.mode = "MODO_KERNEL" if u["admin"] else "MODO_USUARIO"
        self.fs.user = username
        print(f"Autenticado com sucesso. Usuário: '{username}' | Grupo: '{u['group']}' | Modo: {self.mode}")

    def logout(self):
        self.current_user, self.mode, self.fs.user = "root", "MODO_KERNEL", "root"
        print("Sessão encerrada. Usuário atual: root | Modo: MODO_KERNEL")

    def require_root(self):
        if self.mode != "MODO_KERNEL":
            self._log(f"PRIVILEGIO_NEGADO usuário={self.current_user}")
            raise SecurityError("operação exige MODO_KERNEL (root)")

    def _log(self, msg):
        self.audit_log.append((time.time(), msg))


    @staticmethod
    def _permissoes_rwx(mode):
        """Converte os nove bits Unix em rwxrwxrwx."""
        simbolos = ((4, "r"), (2, "w"), (1, "x"))
        partes = []

        for deslocamento in (6, 3, 0):
            bits = (mode >> deslocamento) & 0b111
            partes.append(
                "".join(
                    letra if bits & valor else "-"
                    for valor, letra in simbolos
                )
            )

        return "".join(partes)

    def _relatar_verificacao_acl(self, username, operation, node):
        """Exibe os metadados usados na verificação de ACL."""
        if operation != "ler":
            return

        user = self.users.get(username, {})
        grupo_usuario = user.get("group", "desconhecido")
        grupo_objeto = getattr(node, "group", "users")

        acao = {
            "ler": "LEITURA",
            "escrever": "ESCRITA",
            "listar": "LISTAGEM",
            "criar": "CRIAÇÃO",
            "remover": "REMOÇÃO",
            "executar": "EXECUÇÃO",
        }.get(operation, operation.upper())

        print("[SISTEMA DE SEGURANÇA - VERIFICAÇÃO DE ACL]")
        print(f"Objeto      : {node.path()}")
        print(f"Proprietário: {node.owner} (Grupo: {grupo_objeto})")
        print(
            f"Permissões  : {self._permissoes_rwx(node.perms)} "
            f"({node.perms:03o})"
        )
        print(
            f"Ação Pedida : {acao} por '{username}' "
            f"(Grupo: {grupo_usuario})"
        )


    def authorize(self, username, operation, node):
        # Exibe os dados antes da decisão, inclusive para root.
        self._relatar_verificacao_acl(username, operation, node)

        if self.current_user == "root":
            return
        user = self.users[self.current_user]
        # compatibilidade com permissões Unix: owner, group, others
        if username == node.owner:
            bits = (node.perms >> 6) & 7
        elif user["group"] == getattr(node, "group", "users"):
            bits = (node.perms >> 3) & 7
        else:
            bits = node.perms & 7
        required = {
            "ler": 4,
            "listar": 5,       # leitura + travessia do diretório
            "escrever": 2,
            "criar": 3,         # escrita + execução no diretório pai
            "remover": 2,
            "executar": 1,
        }.get(operation, 0)
        acl = getattr(node, "acl", {}).get(username)
        allowed = bool((bits & required) == required)
        if acl is not None:
            allowed = (acl & required) == required
        if not allowed:
            self._log(f"ACL_NEGADA usuário={username} ação={operation} objeto={node.path()}")
            raise SecurityError(f"[NEGADO] O usuário '{username}' não possui permissão de {operation} no objeto '{node.path()}'. Tentativa registrada nos logs.")

    def chmod(self, path, mode):
        self.require_root()
        node = self.fs.resolve(path)
        value = int(mode, 8)
        if value < 0 or value > 0o777:
            raise SecurityError("permissão deve estar entre 000 e 777")
        node.perms = value
        print(f"Permissões de '{path}' alteradas para {value:03o}.")

    def chown(self, path, owner, group=None):
        self.require_root()
        if owner not in self.users:
            raise SecurityError("proprietário inexistente")
        node = self.fs.resolve(path)
        node.owner = owner
        node.group = group or self.users[owner]["group"]

    @staticmethod
    def _crypt(data, key, nonce):
        raw = key.encode() if isinstance(key, str) else key
        out, counter, offset = bytearray(), 0, 0
        while offset < len(data):
            stream = hashlib.sha256(raw + nonce + counter.to_bytes(8, "big")).digest()
            chunk = data[offset:offset + len(stream)]
            out.extend(a ^ b for a, b in zip(chunk, stream))
            offset += len(chunk)
            counter += 1
        return bytes(out)

    @staticmethod
    def _derive_file_keys(key, salt):
        """Deriva chaves independentes de cifra e autenticação; nunca persiste a senha."""
        material = hashlib.pbkdf2_hmac(
            "sha256", str(key).encode(), salt, 200_000, dklen=64
        )
        return material[:32], material[32:]

    def _validar_chave(self, crypto, key):
        enc_key, mac_key = self._derive_file_keys(key, crypto["salt"])
        verifier = hmac.new(mac_key, b"so-simulator-file-key", hashlib.sha256).digest()
        if not hmac.compare_digest(verifier, crypto["key_verifier"]):
            raise SecurityError("chave incorreta")
        return enc_key, mac_key

    def decrypt_payload(self, node, ciphertext, key):
        """Valida a chave e o MAC antes de disponibilizar o texto claro em memória."""
        crypto = getattr(node, "crypto", None)
        if not crypto:
            return ciphertext
        enc_key, mac_key = self._validar_chave(crypto, key)
        expected = hmac.new(mac_key, crypto["nonce"] + ciphertext, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, crypto["mac"]):
            raise SecurityError("integridade criptográfica comprometida")
        return self._crypt(ciphertext, enc_key, crypto["nonce"])

    def encrypt_payload(self, node, plaintext, key):
        """Recriptografa uma nova versão do conteúdo sem armazenar a chave."""
        crypto = node.crypto
        enc_key, mac_key = self._validar_chave(crypto, key)
        nonce = os.urandom(16)
        cipher = self._crypt(plaintext, enc_key, nonce)
        crypto["nonce"] = nonce
        crypto["mac"] = hmac.new(mac_key, nonce + cipher, hashlib.sha256).digest()
        return cipher

    def encrypt_file(self, path, key):
        self.require_root()
        node = self.fs.resolve(path)
        if node.is_dir:
            raise SecurityError("não é arquivo")
        if node.crypto:
            raise SecurityError("arquivo já está criptografado")
        plain = self.fs._ler(node)
        salt, nonce = os.urandom(16), os.urandom(16)
        enc_key, mac_key = self._derive_file_keys(key, salt)
        cipher = self._crypt(plain, enc_key, nonce)
        node.crypto = {
            "salt": salt,
            "nonce": nonce,
            "key_verifier": hmac.new(
                mac_key, b"so-simulator-file-key", hashlib.sha256
            ).digest(),
            "mac": hmac.new(mac_key, nonce + cipher, hashlib.sha256).digest(),
        }
        try:
            self.fs._gravar(node, cipher)
        except Exception:
            node.crypto = None
            raise
        self._log(f"ENCRYPT arquivo={path}")
        print(f"Arquivo '{path}' criptografado com sucesso. Conteúdo físico protegido.")

    def decrypt_file(self, path, key):
        node = self.fs.resolve(path)
        if node.is_dir:
            raise SecurityError("não é arquivo")
        self.fs._autorizar("escrever", node)
        crypto = getattr(node, "crypto", None)
        if not crypto:
            raise SecurityError("arquivo não está criptografado")
        cipher = self.fs._ler(node)
        plain = self.decrypt_payload(node, cipher, key)
        self.fs._gravar(node, plain)
        node.crypto = None
        self._log(f"DECRYPT arquivo={path} usuário={self.current_user}")
        print(f"Arquivo '{path}' descriptografado permanentemente.")

    def scan(self, path="/"):
        self.require_root()
        print("[MÓDULO DE SEGURANÇA - VARREDURA DE INTEGRIDADE]")
        nodes = []
        start = self.fs.resolve(path)
        def visit(n):
            if not n.is_dir:
                nodes.append(n)
            else:
                for child in n.children.values(): visit(child)
        visit(start)
        print(f"Analisando {len(nodes)} arquivos em busca de assinaturas maliciosas e hashes corrompidos...")
        alerts = 0
        for node in nodes:
            data = self.fs._ler(node)
            if any(sig in data for sig in self.signatures):
                status = "ALERTA - assinatura maliciosa detectada"
                alerts += 1
            elif getattr(node, "crypto", None):
                # Sem a chave, a auditoria verifica os hashes dos blocos físicos.
                # A autenticação HMAC e a busca de assinaturas no texto claro
                # exigem a chave e, portanto, não são declaradas como verificadas.
                if not self._verify_blocks(node):
                    status = "ALERTA - hash do bloco difere do registro oficial"
                else:
                    status = ("OK - Criptografado; blocos íntegros "
                              "(MAC/assinaturas não verificados sem chave)")
                alerts += status.startswith("ALERTA")
            else:
                status = "OK - Íntegro" if self._verify_blocks(node) else "ALERTA - hash do bloco difere do registro oficial"
                alerts += status.startswith("ALERTA")
            print(f"- {node.path():<32}: [{status}]")
        print(f"Resultado: {alerts} alerta(s) encontrado(s).")

    def _verify_blocks(self, node):
        expected = getattr(node, "block_hashes", {})
        for block in node.blocks:
            current = hashlib.sha256(self.fs.dev.read_block(block)).hexdigest()
            if expected.get(block) != current:
                return False
        return True


def registrar(shell, security):
    def login(a):
        if len(a) < 2: raise SecurityError("uso: login --user NOME --password SENHA")
        user = a[a.index("--user") + 1] if "--user" in a else a[0]
        password = a[a.index("--password") + 1] if "--password" in a else input("Senha: ")
        security.login(user, password)
    def useradd(a): security.add_user(a[0], a[1], a[2] if len(a) > 2 else "users")
    def groupadd(a): security.add_group(a[0])
    def chmod(a): security.chmod(a[0], a[1])
    def encrypt(a):
        if "--key" not in a or a.index("--key") + 1 >= len(a):
            raise SecurityError('uso: encrypt_file <arquivo> --key "chave"')
        security.encrypt_file(a[0], a[a.index("--key") + 1])

    def decrypt(a):
        if "--key" not in a or a.index("--key") + 1 >= len(a):
            raise SecurityError('uso: decrypt_file <arquivo> --key "chave"')
        security.decrypt_file(a[0], a[a.index("--key") + 1])
    shell.registrar("login", login, "login --user NOME [--password SENHA]")
    shell.registrar("logout", lambda a: security.logout(), "logout")
    shell.registrar("useradd", useradd, "useradd <usuário> <senha> [grupo] (root)")
    shell.registrar("groupadd", groupadd, "groupadd <grupo> (root)")
    shell.registrar("chmod", chmod, "chmod <caminho> <modo-octal> (root)")
    shell.registrar("encrypt_file", encrypt, 'encrypt_file <arquivo> --key "chave" (root)')
    shell.registrar("decrypt_file", decrypt, "decrypt_file <arquivo> --key CHAVE (descriptografa permanentemente)")
    shell.registrar("antivirus_scan", lambda a: security.scan(a[0] if a else "/"), "antivirus_scan [caminho] (root)")
    shell.registrar("security_log", lambda a: print("\n".join(m for _, m in security.audit_log) or "Sem eventos."), "security_log")
    # Operações que alteram a configuração/estado global do hardware simulado.
    for command in ("compact", "raid_mode", "io_device", "raid_fail", "raid_rebuild", "raid_test"):
        if command in shell.comandos:
            func, help_text = shell.comandos[command]
            def protected(args, fn=func):
                security.require_root()
                return fn(args)
            shell.comandos[command] = (protected, help_text)
