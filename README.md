# Simulador de Sistema Operacional Modular

Projeto de Sistemas Operacionais em Python, dividido em 5 etapas:
1. **Sistema de Arquivos** (`fs/`)
2. **Entrada/Saída e Armazenamento** (`io/`: braço de disco, RAID, SSD)
3. Segurança e Proteção (usuários, ACL, criptografia, anti-vírus)
4. Gerenciamento de Memória
5. Gerenciamento de Processos

## Como rodar (Etapa 1)
```
python -m so_simulator --strategy indexada            # ou contigua
python -m so_simulator --strategy contigua --blocks 8 --block-size 4096
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

## Etapa 2: Entrada/Saída e Armazenamento

```
python -m so_simulator --io-algo sstf                          # disco único
python -m so_simulator --raid 5 --disks 3 --io-algo sstf       # RAID 5 com 3 discos
python -m so_simulator --device ssd --ssd-wl static            # SSD com Wear Leveling
python -m unittest discover -s tests -t .                      # testes
```

Camadas: `FileSystem → InputOutput → RAIDArray (discos com trilhas) | SSD (FTL)`.
O FS não mudou: `InputOutput` tem a interface do `BlockDevice`.

- `io/input_output.py`: fila de E/S; escritas são aplicadas e escalonadas no fim de cada comando
- `io/scheduler.py`: FCFS, SSTF, SCAN, C-SCAN (+ LOOK e C-LOOK); o retorno do C-SCAN conta no deslocamento
- `io/physical_disk.py`: disco com trilhas e cabeça (os blocos ficam espalhados pelas trilhas, 0..199)
- `io/raid.py`: disco único, RAID 0, 1, 4, 5 (paridade XOR, escrita completa de faixa ou read-modify-write, modo degradado, rebuild)
- `io/ssd.py`: FTL por páginas, escrita fora-do-lugar, TRIM, coleta de lixo, Wear Leveling dinâmico e estático
- `io/comandos.py`: comandos do shell

Comandos: `write_file`, `read_file`, `io_sched`, `io_algo`, `io_log`, `io_status`, `io_device`,
`raid_mode`, `raid_status`, `raid_fail`, `raid_rebuild`, `raid_check`, `raid_test`, `ssd_status`, `ssd_demo`.

### Exemplo 2.1: escalonamento SSTF
```
> io_sched --tracks 98,183,37,122,14 --head 53 --algo sstf
--- ESCALONAMENTO DE DISCO: SSTF (Shortest Seek Time First) ---
Ordem de Atendimento : 53 -> 37 -> 14 -> 98 -> 122 -> 183
Deslocamento da Cabeça: |53-37| + |37-14| + |14-98| + |98-122| + |122-183|
Total de Movimento    : 16 + 23 + 84 + 24 + 61 = 208 trilhas
```
`--algo all` compara todos os algoritmos. Cada `write_file`/`read_file`/`cat` também mostra o
escalonamento das suas requisições.

### Exemplo 2.2: gravação sob RAID 5
```
> write_file /dados/planilha.csv --size 8KB --teste-falha
Arranjo Ativo: RAID 5 (3 Discos Físicos, Blocos de 4 KB)
[E/S - DISTRIBUIÇÃO EM DISCO]
Disco 1: [Bloco 02: Dados de 'planilha.csv' - Parte 1 (4 KB)]
Disco 2: [PARIDADE : XOR(Bloco 02, Bloco 03)]
Disco 3: [Bloco 03: Dados de 'planilha.csv' - Parte 2 (4 KB)]
[TESTE DE FALHA SIMULADO]
* Falha forçada no Disco 3!
* Recuperando Bloco 03 via operação XOR: (Disco 1 XOR Disco 2) -> Reconstrução [OK]
```
(Os blocos 02 e 03 são os de dados do arquivo no FS; a paridade cai no Disco 2 porque
a faixa é a 1 e a paridade do RAID 5 gira entre os discos.)

### SSD
`io_device ssd` troca o armazenamento mantendo os dados. `ssd_status` mostra páginas
(V/I/L) e apagamentos por bloco; `ssd_demo` compara sem Wear Leveling, dinâmico e dinâmico+estático.

### Simplificações
Leituras são atendidas na hora e escritas no fim do comando; o escalonador reordena só o
atendimento físico (os dados seguem a ordem do programa). Liberar bloco (`rm`) não gera E/S no
HDD/RAID (só metadado) e vira TRIM no SSD. Blocos nunca gravados leem zeros sem E/S.

## Etapa 3: Segurança e Proteção

O módulo `security/` integra autenticação, privilégios, permissões Unix, criptografia
simulada em repouso e auditoria de integridade ao FS e à camada de E/S.

Comandos (a sessão inicial é `root`, senha `root`):

```
login --user root --password root
useradd carlos MinhaSenha devs
logout
login --user carlos --password MinhaSenha
mkdir /home/carlos/documentos
write_file /home/carlos/documentos/segredo.txt --text "meu conteúdo"
read_file /home/carlos/documentos/segredo.txt
cat /home/carlos/documentos/segredo.txt
logout
login --user root --password root
chmod /sistema/config.sys 750
encrypt_file /home/carlos/segredo.txt --key MinhaChave123
logout
login --user carlos --password MinhaSenha
read_file /home/carlos/segredo.txt --key MinhaChave123
logout
login --user root --password root
decrypt_file /home/carlos/segredo.txt --key MinhaChave123
antivirus_scan /
security_log
```

`useradd` e `groupadd` exigem root. Ao cadastrar um usuário, o simulador cria
`/home/<usuário>` com proprietário e grupo corretos (permissão inicial `750`).
Usuários comuns podem criar arquivos e subdiretórios dentro de seu próprio diretório
pessoal, mas não diretamente em `/home` ou `/`. A travessia de cada diretório do
caminho exige permissão `x`; criar/remover entradas exige `wx` no diretório pai.
`chmod`, criptografia e operações globais de manutenção/alteração de RAID/SSD também
exigem MODO_KERNEL. O hash de senha usa
PBKDF2-HMAC-SHA256 com salt aleatório. A ACL implementa permissões POSIX rwx para
proprietário, grupo e outros, além de entradas específicas por usuário via `Node.acl`.

Arquivos criptografados **não são descriptografados automaticamente**. O comando
`read_file <arquivo> --key CHAVE` exige a chave, valida o autenticador (HMAC) e só
então apresenta o conteúdo em memória; a gravação física permanece cifrada.
`read_file` sem `--key`, ou com chave incorreta, é recusado. `write_file`, `write`
e `append` em arquivos cifrados também exigem `--key`. `decrypt_file <arquivo>
--key CHAVE` remove permanentemente a criptografia e grava o texto claro no disco.
A chave não é armazenada nos metadados: são persistidos apenas salt, nonce,
verificador derivado e MAC. A varredura sem a chave verifica os hashes dos blocos,
mas informa que não conseguiu validar o MAC nem procurar assinaturas no texto claro.

**Nota didática:** a cifra em repouso é uma simulação baseada em fluxo derivado de
SHA-256 e autenticação HMAC, não uma implementação criptográfica para produção.
Use uma biblioteca auditada com AES-GCM ou ChaCha20-Poly1305 em sistemas reais.
