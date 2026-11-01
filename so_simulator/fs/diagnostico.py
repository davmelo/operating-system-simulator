from .util import fmt


def gerar_relatorio(fs, detalhes=True):
    """Monta o texto do fs_status: mapa do disco + relatório de fragmentação."""
    dev, al = fs.dev, fs.alloc
    bs, n = dev.block_size, dev.n_blocks
    out = []
    p = out.append
    p(f"Tamanho do Bloco de Disco: {fmt(bs)} | Total de Blocos: {n} ({fmt(bs * n)})")
    p(f"\n[MAPA DE ALOCAÇÃO DO DISCO ({al.nome.capitalize()})]")

    if detalhes:
        i = 0
        while i < n:
            s = al.slots[i]
            if s is None:
                j = i
                while j + 1 < n and al.slots[j + 1] is None:
                    j += 1
                rot = f"Bloco {i:02d}" if i == j else f"Bloco {i:02d}..{j:02d}"
                p(f"{rot:<22} : [LIVRE]")
                i = j + 1
                continue
            if s["kind"] == "RAIZ":
                p(f"Bloco {i:02d} {'[INODE_RAIZ]':<13} : Diretório '/'")
            elif s["kind"] == "INODE":
                p(f"Bloco {i:02d} {'[INODE]':<13} : Arquivo '{s['node'].path()}'")
            else:
                p(f"Bloco {i:02d} {'[DADO]':<13} : '{s['node'].path()}' "
                  f"(Bloco {s['seq']} - {s['used']} bytes ocupados)")
            i += 1
        p("")

    # mini-mapa: R=raiz, i=i-node, letra=arquivo, .=livre
    letras = {}
    for s in al.slots:
        if s and s["kind"] != "RAIZ" and s["node"] not in letras:
            letras[s["node"]] = chr(ord("A") + len(letras) % 26)
    barra = "".join("." if s is None else "R" if s["kind"] == "RAIZ"
                    else "i" if s["kind"] == "INODE" else letras[s["node"]] for s in al.slots)
    p(f"Mapa visual: [{barra}]")
    for node, l in letras.items():
        p(f"  {l} = {node.path()}")

    p("\n--- RELATÓRIO DE FRAGMENTAÇÃO ---")
    total_interna = 0
    for node in letras:
        alocado = len(node.blocks) * bs
        interna = alocado - node.size if node.blocks else 0
        total_interna += interna
        pct = f" ({interna / bs * 100:.0f}% de desperdício no último bloco)" if interna else ""
        idx = " + 1 de índice" if node.inode is not None else ""
        p(f"Arquivo '{node.name}': real {node.size} bytes ({fmt(node.size)}) | "
          f"alocado {alocado} bytes ({len(node.blocks)} blocos de dados{idx}) | "
          f"frag. interna {interna} bytes{pct}")
    externa, compactar = al.fragmentacao_externa()
    nota = "" if compactar or al.nome == "contigua" else f" (alocação {al.nome})"
    p(f"Fragmentação Interna total       : {total_interna} bytes")
    p(f"Fragmentação Externa             : {externa} bytes")
    p(f"Necessidade de Compactação       : {'SIM' if compactar else 'NÃO'}{nota}")
    return "\n".join(out)
