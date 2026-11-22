import re

from .erros import MemoriaError

KB = 1024
MB = 1024 * 1024


def tamanho(txt):
    """'512KB', '10KB', '1.5MB', '4096' → bytes."""
    m = re.fullmatch(r"(\d+(?:[.,]\d+)?)\s*(B|KB|MB)?", str(txt).strip(), re.I)
    if not m:
        raise MemoriaError(f"tamanho inválido: '{txt}' (use ex.: 512, 10KB, 1.5MB)")
    valor = float(m[1].replace(",", "."))
    return int(round(valor * {"B": 1, "KB": KB, "MB": MB}[(m[2] or "B").upper()]))


def dec(v):
    """Número com até 2 casas, mas sempre com ao menos 1: 1.0 / 0.5 / 1.25."""
    s = f"{v:.2f}"
    return s[:-1] if s.endswith("0") else s


def kb(n):
    """Bytes → 'N KB' (512 KB, 2.5 KB)."""
    v = n / KB
    return f"{int(v)} KB" if v == int(v) else f"{dec(v).rstrip('0').rstrip('.')} KB"


def mb(n):
    """Bytes → 'X.Y MB' como nos exemplos do enunciado (1.0 MB, 2.5 MB)."""
    return f"{dec(n / MB)} MB"


def mb_total(n):
    return f"{n // MB} MB" if n % MB == 0 else kb(n)


def pc(v):
    """Percentual sem casas quando inteiro: 50% / 62.5%."""
    return f"{v:.0f}%" if abs(v - round(v)) < 0.05 else f"{v:.1f}%"


def razao(a, b):
    return 100.0 * a / b if b else 0.0


def barra(a, b, largura=20):
    cheio = round(largura * a / b) if b else 0
    return "[" + "#" * cheio + "." * (largura - cheio) + "]"


def intervalo_paginas(n):
    """Páginas 0..n-1 como no enunciado: 'Página 0', 'Páginas 0 e 1', 'Páginas 0, 1 e 2'."""
    if n == 1:
        return "Página 0"
    if n > 6:
        return f"Páginas 0 a {n - 1}"
    return "Páginas " + ", ".join(str(i) for i in range(n - 1)) + f" e {n - 1}"
