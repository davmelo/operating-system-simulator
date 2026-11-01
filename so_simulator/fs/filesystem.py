import time
from .erros import FSError
from .node import Node


class FileSystem:
    """API do sistema de arquivos: caminhos + CRUD, ligando árvore, alocador e disco.

    `guard(usuario, operacao, node)` é um gancho para a Etapa 3 (ACL): se levantar
    FSError, a operação é negada. Operações: listar, ler, escrever, remover.
    """

    def __init__(self, device, allocator, user="root", guard=None):
        self.dev = device
        self.alloc = allocator
        self.user = user
        self.guard = guard
        self.root = Node("", True, owner=user)
        self.cwd = self.root
        self.alloc.reservar_raiz(self.root)

    def _autorizar(self, operacao, node):
        if self.guard:
            self.guard(self.user, operacao, node)

    # ------------------------------------------------------------ caminhos
    def resolve(self, path):
        node = self.root if path.startswith("/") else self.cwd
        for parte in [p for p in path.split("/") if p]:
            if parte == ".":
                continue
            if parte == "..":
                node = node.parent or node
            elif node.is_dir and parte in node.children:
                node = node.children[parte]
            else:
                raise FSError(f"caminho não encontrado: {path}")
        return node

    def resolve_parent(self, path):
        path = path.rstrip("/") or "/"
        if path == "/":
            raise FSError("caminho inválido")
        head, _, name = path.rpartition("/")
        parent = self.resolve(head if head else ("/" if path.startswith("/") else "."))
        if not parent.is_dir:
            raise FSError(f"'{parent.path()}' não é diretório")
        if name in ("", ".", ".."):
            raise FSError("nome inválido")
        return parent, name

    def arquivos(self):
        """Todos os arquivos (não diretórios) da árvore."""
        pilha, achados = [self.root], []
        while pilha:
            n = pilha.pop()
            for c in n.children.values():
                (pilha if c.is_dir else achados).append(c)
        return achados

    # ------------------------------------------------------------ criar
    def mkdir(self, path):
        parent, name = self.resolve_parent(path)
        self._autorizar("escrever", parent)
        if name in parent.children:
            raise FSError("já existe")
        parent.children[name] = Node(name, True, parent, owner=self.user)

    def create(self, path, size=0):
        parent, name = self.resolve_parent(path)
        self._autorizar("escrever", parent)
        if name in parent.children:
            raise FSError("já existe")
        node = Node(name, False, parent, owner=self.user)
        self.alloc.allocate(node, size)          # se falhar, o arquivo não é criado
        parent.children[name] = node

    # ------------------------------------------------------------ ler
    def listdir(self, path="."):
        node = self.resolve(path)
        if not node.is_dir:
            raise FSError("não é diretório")
        self._autorizar("listar", node)
        return sorted(node.children.values(), key=lambda n: n.name)

    def _ler(self, node):
        return b"".join(self.dev.read_block(b) for b in node.blocks)[:node.size]

    def read_file(self, path):
        node = self.resolve(path)
        if node.is_dir:
            raise FSError("é um diretório")
        self._autorizar("ler", node)
        return self._ler(node)

    def cd(self, path):
        node = self.resolve(path)
        if not node.is_dir:
            raise FSError("não é diretório")
        self.cwd = node

    # ------------------------------------------------------------ atualizar
    def _gravar(self, node, dados):
        """Troca o conteúdo do arquivo, realocando blocos. Desfaz se não couber."""
        antigo = self._ler(node)
        self.alloc.release(node)
        try:
            self.alloc.allocate(node, len(dados))
        except FSError:
            self.alloc.allocate(node, len(antigo))
            self._escrever_blocos(node, antigo)
            raise
        self._escrever_blocos(node, dados)
        node.modified = time.time()

    def _escrever_blocos(self, node, dados):
        bs = self.dev.block_size
        for k, b in enumerate(node.blocks):
            self.dev.write_block(b, dados[k * bs:(k + 1) * bs])

    def write_file(self, path, dados):
        node = self.resolve(path)
        if node.is_dir:
            raise FSError("é um diretório")
        self._autorizar("escrever", node)
        self._gravar(node, dados)

    def append_file(self, path, dados):
        node = self.resolve(path)
        if node.is_dir:
            raise FSError("é um diretório")
        self._autorizar("escrever", node)
        self._gravar(node, self._ler(node) + dados)

    def move(self, origem, destino):
        src = self.resolve(origem)
        if src is self.root:
            raise FSError("não dá para mover a raiz")
        try:
            dst = self.resolve(destino)
        except FSError:
            dst = None
        if dst is not None and dst.is_dir:
            parent, name = dst, src.name
        else:
            parent, name = self.resolve_parent(destino)
        p = parent
        while p:                                   # não mover diretório para dentro de si
            if p is src:
                raise FSError("destino dentro da origem")
            p = p.parent
        if name in parent.children:
            raise FSError("destino já existe")
        self._autorizar("escrever", src)
        del src.parent.children[src.name]
        src.name, src.parent = name, parent
        parent.children[name] = src

    # ------------------------------------------------------------ deletar
    def remove(self, path):
        node = self.resolve(path)
        if node.is_dir:
            raise FSError("é diretório, use rmdir")
        self._autorizar("remover", node)
        self.alloc.release(node)
        del node.parent.children[node.name]

    def rmdir(self, path):
        node = self.resolve(path)
        if not node.is_dir or node is self.root:
            raise FSError("diretório inválido")
        if node.children:
            raise FSError("diretório não está vazio")
        self._autorizar("remover", node)
        if node is self.cwd:
            self.cwd = node.parent
        del node.parent.children[node.name]

    # ------------------------------------------------------------ manutenção
    def compactar(self):
        """Reagrupa os arquivos no início do disco, preservando o conteúdo."""
        def primeiro(f):
            return min(f.blocks + ([f.inode] if f.inode is not None else [self.dev.n_blocks]))
        arquivos = sorted(self.arquivos(), key=primeiro)
        conteudos = [(f, self._ler(f)) for f in arquivos]
        for f, _ in conteudos:
            self.alloc.release(f)
        for f, dados in conteudos:
            self.alloc.allocate(f, len(dados))
            self._escrever_blocos(f, dados)
