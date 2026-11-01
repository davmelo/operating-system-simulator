from .diagnostico import gerar_relatorio
from .erros import FSError
from .util import fmt, parse_size


def _opcao(args, nome):
    if nome not in args or args.index(nome) + 1 >= len(args):
        raise FSError(f"faltou {nome}")
    return args[args.index(nome) + 1]


def _dados(args):
    if "--text" in args:
        return _opcao(args, "--text").encode()
    if "--size" in args:
        return bytes(parse_size(_opcao(args, "--size")))
    raise FSError('use --size N ou --text "texto"')


def registrar(shell, fs):
    """Registra os comandos da Etapa 1 no shell."""

    def create(a):
        fs.create(a[0], parse_size(_opcao(a, "--size")) if "--size" in a else 0)

    def ls(a):
        for n in fs.listdir(a[0] if a else "."):
            print(f"{n.name}/ [DIR]" if n.is_dir else f"{n.name} ({fmt(n.size)}) [ARQ]")

    def tree(a):
        print("[ÁRVORE DO SISTEMA DE ARQUIVOS]\n/")

        def rec(node, prefixo):
            itens = sorted(node.children.values(), key=lambda x: x.name)
            for i, c in enumerate(itens):
                ultimo = i == len(itens) - 1
                rot = f"{c.name}/ [DIR]" if c.is_dir else f"{c.name} ({fmt(c.size)}) [ARQ]"
                print(prefixo + ("└── " if ultimo else "├── ") + rot)
                if c.is_dir:
                    rec(c, prefixo + ("    " if ultimo else "│   "))
        rec(fs.root, "")

    def cat(a):
        dados = fs.read_file(a[0])
        n = fs.resolve(a[0])
        print(f"{n.path()} | {n.size} bytes | dono: {n.owner} | perms: {oct(n.perms)[2:]} "
              f"| dados: {n.blocks} | i-node: {n.inode}")
        texto = dados.rstrip(b"\0")
        if texto:
            print(texto.decode("utf-8", "replace"))

    def compact(a):
        fs.compactar()
        print("Disco compactado.")

    shell.registrar("mkdir", lambda a: fs.mkdir(a[0]), "mkdir <caminho>")
    shell.registrar("create", create, "create <caminho> [--size 10KB]")
    shell.registrar("ls", ls, "ls [caminho]")
    shell.registrar("tree", tree, "tree")
    shell.registrar("cd", lambda a: fs.cd(a[0] if a else "/"), "cd [caminho]")
    shell.registrar("pwd", lambda a: print(fs.cwd.path()), "pwd")
    shell.registrar("cat", cat, "cat <arquivo>  (metadados + conteúdo)")
    shell.registrar("write", lambda a: fs.write_file(a[0], _dados(a[1:])),
                    'write <arquivo> --size N | --text "..."  (substitui o conteúdo)')
    shell.registrar("append", lambda a: fs.append_file(a[0], _dados(a[1:])),
                    'append <arquivo> --size N | --text "..."  (acrescenta)')
    shell.registrar("mv", lambda a: fs.move(a[0], a[1]), "mv <origem> <destino>")
    shell.registrar("rm", lambda a: fs.remove(a[0]), "rm <arquivo>")
    shell.registrar("rmdir", lambda a: fs.rmdir(a[0]), "rmdir <diretório>")
    shell.registrar("compact", compact, "compact  (compacta o disco)")
    shell.registrar("fs_status", lambda a: print(gerar_relatorio(fs, "--details" in a)),
                    "fs_status [--details]")
