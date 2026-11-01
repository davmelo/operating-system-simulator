# Simulador de Sistema Operacional Modular

Projeto de Sistemas Operacionais em Python, dividido em 5 etapas:
1. **Sistema de Arquivos** (este módulo: `fs/`)
2. Entrada/Saída e Armazenamento (braço de disco, RAID)
3. Segurança e Proteção (usuários, ACL, criptografia, anti-vírus)
4. Gerenciamento de Memória
5. Gerenciamento de Processos

## Como rodar (Etapa 1)
```
python -m simulador_so --strategy indexada            # ou contigua
python -m simulador_so --strategy contigua --blocks 8 --block-size 4096
```
Digite `help` para ver os comandos: `mkdir`, `create`, `ls`, `tree`, `cd`, `pwd`, `cat`,
`write`, `append`, `mv`, `rm`, `rmdir`, `compact`, `fs_status [--details]`.

## Estrutura da Etapa 1
- `fs/blockdevice.py`: disco em blocos (`read_block` / `write_block`)
- `fs/allocators.py`: estratégias de alocação (contígua e indexada)
- `fs/filesystem.py`: caminhos absolutos/relativos e CRUD
- `fs/diagnostico.py`: mapa do disco e relatório de fragmentação
- `fs/comandos.py`: comandos do shell; `shell.py`: shell compartilhado entre as etapas

## Exemplo
```
> mkdir /sistema
> mkdir /sistema/notas
> create /sistema/notas/teste_simulador.txt --size 10KB
> write /sistema/notas/teste_simulador.txt --text "ola mundo"
> fs_status --details
Tamanho do Bloco de Disco: 4 KB | Total de Blocos: 16 (64 KB)

[MAPA DE ALOCAÇÃO DO DISCO (Indexada)]
Bloco 00 [INODE_RAIZ]  : Diretório '/'
Bloco 01 [INODE]       : Arquivo '/sistema/notas/teste_simulador.txt'
Bloco 02 [DADO]        : '/sistema/notas/teste_simulador.txt' (Bloco 1 - 9 bytes ocupados)
Bloco 03..15           : [LIVRE]

Mapa visual: [RiA.............]
  A = /sistema/notas/teste_simulador.txt

--- RELATÓRIO DE FRAGMENTAÇÃO ---
Arquivo 'teste_simulador.txt': real 9 bytes (9 B) | alocado 4096 bytes (1 blocos de dados + 1 de índice) | frag. interna 4087 bytes (100% de desperdício no último bloco)
Fragmentação Interna total       : 4087 bytes
Fragmentação Externa             : 0 bytes
Necessidade de Compactação       : NÃO (alocação indexada)
```
