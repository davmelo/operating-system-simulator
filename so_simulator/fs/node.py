import time


class Node:
    """Arquivo ou diretório. Os metadados (owner, perms, acl) serão usados na Etapa 3."""

    def __init__(self, name, is_dir, parent=None, owner="root", perms=None):
        self.name = name
        self.is_dir = is_dir
        self.parent = parent
        self.children = {}          # só para diretórios
        self.size = 0               # só para arquivos (bytes)
        self.blocks = []            # blocos de DADOS do arquivo
        self.inode = None           # bloco de índice (alocação indexada)
        self.owner = owner
        self.perms = perms if perms is not None else (0o755 if is_dir else 0o644)
        self.acl = {}               # reservado: Etapa 3 (usuário -> permissões)
        self.created = self.modified = time.time()

    def path(self):
        if self.parent is None:
            return "/"
        return self.parent.path().rstrip("/") + "/" + self.name
