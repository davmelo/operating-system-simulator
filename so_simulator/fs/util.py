import re
from .erros import FSError


def parse_size(txt):
    m = re.fullmatch(r"(\d+)\s*(B|KB|MB)?", txt.strip(), re.I)
    if not m:
        raise FSError(f"tamanho inválido: '{txt}' (use ex.: 512, 10KB, 1MB)")
    return int(m[1]) * {"B": 1, "KB": 1024, "MB": 1024 ** 2}[(m[2] or "B").upper()]


def fmt(n):
    return f"{n // 1024} KB" if n and n % 1024 == 0 else f"{n} B"
