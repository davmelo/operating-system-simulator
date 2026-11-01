import shlex
from .fs.erros import FSError


class Shell:
    """Shell único do simulador. Cada etapa registra os seus comandos com registrar()."""

    def __init__(self, prompt=lambda: "user@so-simulator:~ $ "):
        self.prompt = prompt
        self.comandos = {}

    def registrar(self, nome, funcao, ajuda=""):
        self.comandos[nome] = (funcao, ajuda)

    def executar(self, linha):
        partes = shlex.split(linha)
        if not partes:
            return
        nome, args = partes[0], partes[1:]
        if nome == "help":
            for n, (_, ajuda) in sorted(self.comandos.items()):
                print(f"  {ajuda or n}")
            print("  exit")
            return
        if nome not in self.comandos:
            print("comando desconhecido (digite 'help')")
            return
        try:
            self.comandos[nome][0](args)
        except FSError as e:
            print(f"erro: {e}")
        except (IndexError, ValueError):
            print("erro: argumentos inválidos (digite 'help')")

    def loop(self):
        while True:
            try:
                linha = input(self.prompt()).strip()
            except (EOFError, KeyboardInterrupt):
                break
            if linha == "exit":
                break
            self.executar(linha)
